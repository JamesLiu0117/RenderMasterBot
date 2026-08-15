"""Text-model planning of bounded RenderSpec corrections or explicit capability gaps."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field, ValidationError, field_validator, model_validator

from render_master_bot.asset_index import AssetIndexError, load_asset_card_catalog
from render_master_bot.contracts import (
    AssetCard,
    ArtifactRecord,
    CorrectionDecision,
    EvaluationReport,
    LongText,
    PatchOperation,
    RenderSpecPatch,
    RunManifest,
    ShortText,
)
from render_master_bot.models import RenderSpec, StrictModel
from render_master_bot.ollama import StructuredResponse
from render_master_bot.patching import PatchApplicationError, apply_render_spec_patch
from render_master_bot.schemas import ollama_model_schema
from render_master_bot.serialization import canonical_sha256
from render_master_bot.unreal_executor import (
    UnrealSceneBuildError,
    resolve_scene_build_request,
)


SYSTEM_PROMPT = """You are the bounded correction planner for RenderMasterBot.
Decide whether the supplied EvaluationReport can be fixed using only the explicitly allowed
RenderSpec replacement paths. Return outcome 'patch' only when those operations directly address
every error or blocking issue. Otherwise return 'unresolved' and name the missing capabilities.
Never claim that camera, lighting, or transform changes can create a missing texture or material.
Never modify a scene object's primary asset reference, object IDs, scene identity, schema metadata,
or source prompts. A listed materials path may reference only supplied material asset IDs and the
target object's listed material slot names. Do not invent assets, materials, lights, or objects.
Patch values must be literal JSON values of the target field type. For example, replace a numeric
intensity with "value": 2000.0, never with a wrapper such as {"type":"number","value":2000.0}.
Return exactly one JSON object matching the schema, without Markdown or extra prose.
"""

_ALLOWED_PATH = re.compile(
    r"^/(?:"
    r"camera/(?:transform/(?:location_cm|rotation_deg)|focal_length_mm|"
    r"focus_distance_cm|aperture_f_stop)|"
    r"lights/[0-9]+/(?:transform/(?:location_cm|rotation_deg)|intensity|"
    r"color_rgb|cast_shadows)|"
    r"objects/[0-9]+/(?:materials|transform/(?:location_cm|rotation_deg|scale))|"
    r"render/(?:width_px|height_px|quality|seed)"
    r")$"
)


class CorrectionPlanningError(RuntimeError):
    """Raised when correction inputs or model output cannot be trusted."""

    def __init__(self, message: str, response: StructuredResponse | None = None):
        super().__init__(message)
        self.response = response


class CorrectionOperationDraft(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    # Keep the model-facing schema free of recursive JsonValue references. The value is
    # immediately revalidated as a public PatchOperation and then as a full RenderSpec.
    value: Any

    @field_validator("value", mode="before")
    @classmethod
    def unwrap_redundant_literal_wrapper(cls, value: Any) -> Any:
        """Normalize the narrow wrappers some structured models add to JSON literals."""

        if isinstance(value, dict) and set(value) == {"value"}:
            return value["value"]
        if isinstance(value, dict) and set(value) == {"type", "value"}:
            return value["value"]
        return value


class CorrectionDraft(StrictModel):
    outcome: Literal["patch", "unresolved"]
    rationale: LongText
    operations: list[CorrectionOperationDraft] = Field(default_factory=list, max_length=16)
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def outcome_is_consistent(self) -> "CorrectionDraft":
        if self.outcome == "patch":
            if not self.operations:
                raise ValueError("patch outcomes require at least one operation")
            if self.missing_capabilities:
                raise ValueError("patch outcomes cannot declare missing capabilities")
        else:
            if self.operations:
                raise ValueError("unresolved outcomes cannot contain operations")
            if not self.missing_capabilities:
                raise ValueError("unresolved outcomes require missing capabilities")
        return self


class StructuredCorrectionClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any],
    ) -> StructuredResponse: ...


@dataclass(frozen=True, slots=True)
class CorrectionPlanningResult:
    decision: CorrectionDecision
    response: StructuredResponse
    corrected_spec: RenderSpec | None = None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _one_artifact(artifacts: list[ArtifactRecord], role: str) -> ArtifactRecord:
    matches = [artifact for artifact in artifacts if artifact.role == role]
    if len(matches) != 1:
        raise CorrectionPlanningError(
            f"RunManifest must contain exactly one {role!r} artifact, observed {len(matches)}"
        )
    return matches[0]


def _verified_artifact(run_root: Path, artifact: ArtifactRecord) -> Path:
    path = (run_root / artifact.path).resolve()
    try:
        path.relative_to(run_root)
    except ValueError as exc:
        raise CorrectionPlanningError(
            f"artifact escapes the run directory: {artifact.path}"
        ) from exc
    if not path.is_file() or artifact.sha256 is None:
        raise CorrectionPlanningError(f"artifact is missing or unhashed: {artifact.path}")
    observed = _sha256_file(path)
    if observed != artifact.sha256:
        raise CorrectionPlanningError(f"artifact SHA-256 mismatch: {artifact.path}")
    return path


def _load_inputs(
    run_directory: str | Path,
    evaluation_path: str,
) -> tuple[RenderSpec, EvaluationReport, list[AssetCard], str]:
    run_root = Path(run_directory).expanduser().resolve()
    try:
        manifest = RunManifest.model_validate_json(
            (run_root / "run_manifest.json").read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise CorrectionPlanningError(f"invalid RunManifest: {exc}") from exc
    if manifest.status != "succeeded":
        raise CorrectionPlanningError("correction planning requires a succeeded preview run")

    spec_artifact = _one_artifact(manifest.input_artifacts, "render_spec")
    assets_artifact = _one_artifact(manifest.input_artifacts, "asset_catalog")
    preview_artifact = _one_artifact(manifest.output_artifacts, "beauty_preview")
    spec_path = _verified_artifact(run_root, spec_artifact)
    assets_path = _verified_artifact(run_root, assets_artifact)
    _verified_artifact(run_root, preview_artifact)
    report_path = (run_root / evaluation_path).resolve()
    try:
        report_path.relative_to(run_root)
    except ValueError as exc:
        raise CorrectionPlanningError("evaluation path escapes the run directory") from exc
    try:
        spec = RenderSpec.model_validate_json(spec_path.read_text(encoding="utf-8-sig"))
        report = EvaluationReport.model_validate_json(
            report_path.read_text(encoding="utf-8-sig")
        )
        cards = load_asset_card_catalog(assets_path)
    except (OSError, ValidationError, AssetIndexError) as exc:
        raise CorrectionPlanningError(f"invalid correction input: {exc}") from exc

    spec_hash = canonical_sha256(spec)
    if manifest.render_spec_sha256 != spec_hash or report.render_spec_sha256 != spec_hash:
        raise CorrectionPlanningError("RenderSpec, RunManifest, and EvaluationReport hashes differ")
    if report.evaluation_stage != "preview":
        raise CorrectionPlanningError("correction planning requires a preview EvaluationReport")
    if report.preview_paths != [preview_artifact.path]:
        raise CorrectionPlanningError("EvaluationReport does not reference the run beauty preview")
    return spec, report, cards, canonical_sha256(report)


def _allowed_paths(spec: RenderSpec) -> list[str]:
    paths = [
        "/camera/transform/location_cm",
        "/camera/transform/rotation_deg",
        "/camera/focal_length_mm",
        "/camera/focus_distance_cm",
        "/camera/aperture_f_stop",
        "/render/width_px",
        "/render/height_px",
        "/render/quality",
        "/render/seed",
    ]
    for index, _ in enumerate(spec.objects):
        paths.extend((
            f"/objects/{index}/materials",
            f"/objects/{index}/transform/location_cm",
            f"/objects/{index}/transform/rotation_deg",
            f"/objects/{index}/transform/scale",
        ))
    for index, _ in enumerate(spec.lights):
        paths.extend((
            f"/lights/{index}/transform/location_cm",
            f"/lights/{index}/transform/rotation_deg",
            f"/lights/{index}/intensity",
            f"/lights/{index}/color_rgb",
            f"/lights/{index}/cast_shadows",
        ))
    return paths


def _relevant_asset_context(
    spec: RenderSpec,
    cards: list[AssetCard],
) -> list[dict[str, Any]]:
    used_ids = {item.asset.asset_id for item in spec.objects}
    relevant = [
        card
        for card in cards
        if card.asset_id in used_ids or card.asset_type == "material"
    ]
    return [
        {
            "asset_id": card.asset_id,
            "display_name": card.display_name,
            "asset_type": card.asset_type,
            "description": card.description,
            "tags": card.tags,
            "material_slots": card.material_slots,
        }
        for card in relevant
    ]


def plan_correction(
    client: StructuredCorrectionClient,
    *,
    model: str,
    run_directory: str | Path,
    evaluation_path: str = "evaluation.json",
) -> CorrectionPlanningResult:
    """Plan a validated replacement-only correction or report a capability gap."""

    spec, report, cards, report_hash = _load_inputs(run_directory, evaluation_path)
    allowed_paths = _allowed_paths(spec)
    schema = ollama_model_schema(CorrectionDraft)
    prompt = (
        "Decide whether this evaluation can be fixed with the allowed replacement paths.\n"
        f"Allowed paths:\n{json.dumps(allowed_paths, ensure_ascii=False)}\n"
        "RenderSpec:\n"
        f"{spec.model_dump_json(indent=2)}\n"
        "EvaluationReport:\n"
        f"{report.model_dump_json(indent=2)}\n"
        "Relevant asset evidence:\n"
        f"{json.dumps(_relevant_asset_context(spec, cards), ensure_ascii=False, indent=2)}\n"
        "Output JSON Schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    response = client.chat_structured(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        json_schema=schema,
    )
    try:
        draft = CorrectionDraft.model_validate_json(response.content)
    except ValidationError as exc:
        raise CorrectionPlanningError(
            f"correction model returned an invalid decision draft:\n{exc}",
            response=response,
        ) from exc

    spec_hash = canonical_sha256(spec)
    model_identity = {"provider": "ollama", "model": response.model}
    if draft.outcome == "unresolved":
        decision = CorrectionDecision(
            render_spec_sha256=spec_hash,
            evaluation_report_sha256=report_hash,
            planner=model_identity,
            outcome="unresolved",
            rationale=draft.rationale,
            missing_capabilities=draft.missing_capabilities,
        )
        return CorrectionPlanningResult(decision=decision, response=response)

    allowed = set(allowed_paths)
    invalid_paths = sorted({
        operation.path
        for operation in draft.operations
        if operation.path not in allowed or _ALLOWED_PATH.fullmatch(operation.path) is None
    })
    if invalid_paths:
        raise CorrectionPlanningError(
            "correction model used forbidden or unavailable paths: " + ", ".join(invalid_paths),
            response=response,
        )
    patch = RenderSpecPatch(
        base_spec_sha256=spec_hash,
        rationale=draft.rationale,
        proposed_by=model_identity,
        operations=[
            PatchOperation(op="replace", path=operation.path, value=operation.value)
            for operation in draft.operations
        ],
    )
    try:
        corrected_spec = apply_render_spec_patch(spec, patch)
        resolve_scene_build_request(corrected_spec, cards)
        decision = CorrectionDecision(
            render_spec_sha256=spec_hash,
            evaluation_report_sha256=report_hash,
            planner=model_identity,
            outcome="patch",
            rationale=draft.rationale,
            patch=patch,
        )
    except (PatchApplicationError, UnrealSceneBuildError, ValidationError) as exc:
        raise CorrectionPlanningError(
            f"correction model proposed an invalid patch: {exc}",
            response=response,
        ) from exc
    return CorrectionPlanningResult(
        decision=decision,
        response=response,
        corrected_spec=corrected_spec,
    )
