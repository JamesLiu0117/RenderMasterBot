"""Validated local vision-model evaluation of a completed Unreal preview run."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field, ValidationError, model_validator

from render_master_bot.contracts import (
    ArtifactRecord,
    EvaluationIssue,
    EvaluationReport,
    ImageStatistics,
    LongText,
    RunManifest,
)
from render_master_bot.models import Identifier, RenderSpec, StrictModel, UnitFloat
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema
from render_master_bot.serialization import canonical_sha256


MAX_PREVIEW_BYTES = 20 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

SYSTEM_PROMPT = """You are the visual quality evaluator for RenderMasterBot.
Judge only evidence visible in the supplied preview image and compare it with the RenderSpec.
Do not claim to see hidden geometry, metadata, intent, or materials that are not visible.
Treat evaluation scope and exclusions stated in source_prompt as binding. Do not report an excluded
feature unless it prevents evaluation of an in-scope requirement. Inspect the complete frame,
including its center, before declaring the image blank, black, or missing all geometry.
Before returning pass, check every non-excluded visible requirement in source_prompt independently.
One correct property does not excuse a wrong requested view, framing, material color, texture,
lighting response, or composition. A pass means all visible in-scope requirements are satisfied.
Judge those properties from the image rather than assuming they are correct from RenderSpec
metadata.
Use stable lowercase snake_case issue IDs and only the supplied scene object IDs.
Use blocking or error only when the requested result is unusable, absent, or clearly wrong.
Use warning for visible quality defects that permit another correction pass.
Distinguish clipping or exposure problems from random render noise. Large solid black or white
areas, blown highlights, or high-contrast surface patterns are lighting, exposure, or material
evidence unless genuinely random image noise is visibly distributed across the frame.
Use the supplied deterministic ImageStatistics as measured evidence. Do not call the whole image
underexposed solely because the background or side borders are black when underexposed_like is
false and center_luminance is healthy. A low foreground_fraction is composition evidence, not by
itself proof that the subject material or exposure is wrong.
For one tall product in a landscape frame, symmetric side background is expected. Do not call the
product too small or poorly framed when it is centered and nearly fills the image height; it does
not need to fill the image width. Do not confuse an intentionally dark weathered material with
global underexposure when measured center luminance is healthy and surface detail remains visible.
Return exactly one JSON object matching the schema, without Markdown or extra prose.
Do not propose a patch in this evaluation stage.
"""


class VisualEvaluationError(RuntimeError):
    """Raised when preview evidence or model output cannot be trusted."""

    def __init__(self, message: str, response: StructuredResponse | None = None):
        super().__init__(message)
        self.response = response


class VisualIssueDraft(StrictModel):
    issue_id: Identifier
    category: Literal[
        "asset",
        "camera",
        "composition",
        "geometry",
        "lighting",
        "material",
        "render_quality",
        "performance",
        "other",
    ]
    severity: Literal["info", "warning", "error", "blocking"]
    confidence: UnitFloat
    message: LongText
    object_ids: list[Identifier] = Field(default_factory=list, max_length=64)


class VisualEvaluationDraft(StrictModel):
    """Only the subjective fields the vision model is allowed to produce."""

    verdict: Literal["pass", "needs_review", "fail"]
    summary: LongText
    issues: list[VisualIssueDraft] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def verdict_and_issue_ids_are_consistent(self) -> "VisualEvaluationDraft":
        ids = [issue.issue_id for issue in self.issues]
        if len(ids) != len(set(ids)):
            raise ValueError("visual evaluation issue IDs must be unique")
        severe = any(issue.severity in {"error", "blocking"} for issue in self.issues)
        if self.verdict == "pass" and severe:
            raise ValueError("a pass verdict cannot contain error or blocking issues")
        if self.verdict == "fail" and not severe:
            raise ValueError("a fail verdict requires an error or blocking issue")
        return self


class StructuredVisionClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any],
        think: bool | str | None = None,
    ) -> StructuredResponse: ...


@dataclass(frozen=True, slots=True)
class PreviewEvaluationResult:
    report: EvaluationReport
    response: StructuredResponse
    statistics: ImageStatistics | None = None


@dataclass(frozen=True, slots=True)
class PreviewRunEvidence:
    spec: RenderSpec
    preview_path: str
    image_bytes: bytes


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _one_artifact(
    artifacts: list[ArtifactRecord],
    role: str,
) -> ArtifactRecord:
    matches = [artifact for artifact in artifacts if artifact.role == role]
    if len(matches) != 1:
        raise VisualEvaluationError(
            f"RunManifest must contain exactly one {role!r} artifact, observed {len(matches)}"
        )
    return matches[0]


def _verified_artifact(run_root: Path, artifact: ArtifactRecord) -> Path:
    path = (run_root / artifact.path).resolve()
    try:
        path.relative_to(run_root)
    except ValueError as exc:
        raise VisualEvaluationError(f"artifact escapes the run directory: {artifact.path}") from exc
    if not path.is_file():
        raise VisualEvaluationError(f"artifact does not exist: {artifact.path}")
    if artifact.sha256 is None:
        raise VisualEvaluationError(f"artifact has no SHA-256 evidence: {artifact.path}")
    observed = _sha256_file(path)
    if observed != artifact.sha256:
        raise VisualEvaluationError(
            f"artifact SHA-256 mismatch for {artifact.path}: "
            f"expected {artifact.sha256}, observed {observed}"
        )
    return path


def load_preview_run_evidence(run_directory: str | Path) -> PreviewRunEvidence:
    """Load and hash-verify the RenderSpec and PNG for one successful preview run."""

    run_root = Path(run_directory).expanduser().resolve()
    manifest_path = run_root / "run_manifest.json"
    try:
        manifest = RunManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise VisualEvaluationError(f"invalid RunManifest at {manifest_path}: {exc}") from exc
    if manifest.status != "succeeded":
        raise VisualEvaluationError(
            f"preview evaluation requires a succeeded run, observed {manifest.status}"
        )

    spec_artifact = _one_artifact(manifest.input_artifacts, "render_spec")
    preview_artifact = _one_artifact(manifest.output_artifacts, "beauty_preview")
    spec_path = _verified_artifact(run_root, spec_artifact)
    preview_path = _verified_artifact(run_root, preview_artifact)
    try:
        spec = RenderSpec.model_validate_json(spec_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValidationError) as exc:
        raise VisualEvaluationError(f"invalid RenderSpec artifact: {exc}") from exc
    spec_hash = canonical_sha256(spec)
    if manifest.render_spec_sha256 != spec_hash:
        raise VisualEvaluationError(
            "RunManifest render_spec_sha256 does not match the canonical RenderSpec"
        )

    try:
        image_bytes = preview_path.read_bytes()
    except OSError as exc:
        raise VisualEvaluationError(f"cannot read preview artifact: {exc}") from exc
    if not image_bytes.startswith(PNG_SIGNATURE):
        raise VisualEvaluationError("beauty_preview is not a PNG file")
    if not len(image_bytes) <= MAX_PREVIEW_BYTES:
        raise VisualEvaluationError(
            f"beauty_preview exceeds the {MAX_PREVIEW_BYTES} byte safety limit"
        )
    return PreviewRunEvidence(
        spec=spec,
        preview_path=preview_artifact.path,
        image_bytes=image_bytes,
    )


def evaluate_preview_run(
    client: StructuredVisionClient,
    *,
    model: str,
    run_directory: str | Path,
) -> PreviewEvaluationResult:
    """Evaluate one verified beauty preview and build a host-owned report envelope."""

    evidence = load_preview_run_evidence(run_directory)
    spec = evidence.spec
    preview_path = evidence.preview_path
    image_bytes = evidence.image_bytes
    try:
        # Imported lazily because the benchmark runner also uses this evaluator.
        from render_master_bot.visual_benchmark import analyze_preview_png

        statistics = analyze_preview_png(image_bytes)
    except Exception as exc:
        raise VisualEvaluationError(
            f"cannot extract deterministic statistics from beauty_preview: {exc}"
        ) from exc
    schema = ollama_model_schema(VisualEvaluationDraft)
    scene_ids = [item.object_id for item in spec.objects]
    scene_ids.append(spec.camera.camera_id)
    scene_ids.extend(light.light_id for light in spec.lights)
    user_prompt = (
        "Evaluate this rendered preview against the exact RenderSpec below.\n"
        f"Allowed object_ids: {json.dumps(scene_ids, ensure_ascii=False)}\n"
        "Deterministic ImageStatistics for this exact PNG:\n"
        f"{statistics.model_dump_json(indent=2)}\n"
        f"RenderSpec:\n{spec.model_dump_json(indent=2)}\n"
        "Output JSON Schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    response = client.chat_structured(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": user_prompt,
                "images": [base64.b64encode(image_bytes).decode("ascii")],
            },
        ],
        json_schema=schema,
        think=False,
    )
    try:
        draft = VisualEvaluationDraft.model_validate_json(response.content)
    except ValidationError as exc:
        raise VisualEvaluationError(
            f"vision model returned an invalid evaluation draft:\n{exc}",
            response=response,
        ) from exc

    allowed_ids = set(scene_ids)
    unknown_ids = sorted({
        object_id
        for issue in draft.issues
        for object_id in issue.object_ids
        if object_id not in allowed_ids
    })
    if unknown_ids:
        raise VisualEvaluationError(
            "vision model referenced unknown scene object IDs: " + ", ".join(unknown_ids),
            response=response,
        )
    try:
        issues = [
            EvaluationIssue(
                **issue.model_dump(mode="json"),
                evidence_paths=[preview_path],
            )
            for issue in draft.issues
        ]
        report = EvaluationReport(
            render_spec_sha256=canonical_sha256(spec),
            evaluator={"provider": "ollama", "model": response.model},
            evaluation_stage="preview",
            verdict=draft.verdict,
            summary=draft.summary,
            preview_paths=[preview_path],
            issues=issues,
        )
    except ValidationError as exc:
        raise VisualEvaluationError(
            f"cannot construct the trusted EvaluationReport envelope: {exc}",
            response=response,
        ) from exc
    return PreviewEvaluationResult(
        report=report,
        response=response,
        statistics=statistics,
    )
