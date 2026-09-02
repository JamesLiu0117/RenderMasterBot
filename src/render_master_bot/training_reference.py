"""Golden Reference validation and Unreal evidence collection for planner training."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from render_master_bot.asset_index import (
    AssetIndexError,
    AssetSearchHit,
    asset_document,
    load_asset_card_catalog,
)
from render_master_bot.camera_framing import frame_camera
from render_master_bot.contracts import (
    ArtifactRecord,
    RenderWorkflowManifest,
    TrainingCampaignCase,
    TrainingCampaignRun,
    TrainingCampaignSuite,
    TrainingReferenceCase,
    TrainingReferenceCaseValidation,
    TrainingReferenceSuite,
    TrainingReferenceValidationReport,
    WorkflowIteration,
)
from render_master_bot.ollama import StructuredResponse
from render_master_bot.models import RenderSpec
from render_master_bot.preflight import run_preflight
from render_master_bot.serialization import canonical_sha256
from render_master_bot.training_campaign import (
    CAMPAIGN_MANIFEST_FILENAME,
    create_training_campaign_plan,
    load_training_campaign_suite,
    validate_training_campaign_runtime,
)
from render_master_bot.unreal_preview import run_unreal_preview
from render_master_bot.visual_evaluator import PreviewEvaluationResult, evaluate_preview_run


REFERENCE_SNAPSHOT_FILENAME = "reference_suite_snapshot.json"
REFERENCE_PLANNER_MODEL = "golden-reference-v0.1"
REFERENCE_MAX_CLIPPED_PIXEL_FRACTION = 0.03
REFERENCE_MIN_FOREGROUND_FRACTION = 0.08
REFERENCE_MIN_CENTER_LUMINANCE = 0.05
REFERENCE_MAX_CENTER_LUMINANCE = 0.75


class TrainingReferenceError(RuntimeError):
    """Raised when Golden Reference identity or evidence cannot be trusted."""


@dataclass(frozen=True, slots=True)
class TrainingReferenceWorkflowResult:
    manifest: RenderWorkflowManifest


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _artifact(role: str, path: Path, root: Path) -> ArtifactRecord:
    return ArtifactRecord(
        role=role,
        path=path.resolve().relative_to(root.resolve()).as_posix(),
        sha256=_sha256_file(path),
    )


def _response_metrics(response: StructuredResponse) -> dict[str, Any]:
    return {
        "model": response.model,
        "total_duration_ns": response.total_duration_ns,
        "total_duration_seconds": (
            response.total_duration_ns / 1_000_000_000
            if response.total_duration_ns is not None
            else None
        ),
        "prompt_tokens": response.prompt_tokens,
        "output_tokens": response.output_tokens,
        "done_reason": response.done_reason,
    }


def _reference_pixel_quality(value: Any | None) -> tuple[dict[str, Any], list[str]]:
    thresholds = {
        "maximum_clipped_pixel_fraction": REFERENCE_MAX_CLIPPED_PIXEL_FRACTION,
        "minimum_foreground_fraction": REFERENCE_MIN_FOREGROUND_FRACTION,
        "minimum_center_luminance": REFERENCE_MIN_CENTER_LUMINANCE,
        "maximum_center_luminance": REFERENCE_MAX_CENTER_LUMINANCE,
    }
    issues: list[str] = []
    if value is None:
        issues.append("missing_image_statistics")
        observed = None
    else:
        observed = {
            "clipped_pixel_fraction": value.clipped_pixel_fraction,
            "foreground_fraction": value.foreground_fraction,
            "center_luminance": value.center_luminance,
            "blank_like": value.blank_like,
            "underexposed_like": value.underexposed_like,
            "overexposed_like": value.overexposed_like,
        }
        if value.blank_like:
            issues.append("blank_like")
        if value.underexposed_like:
            issues.append("underexposed_like")
        if value.overexposed_like:
            issues.append("overexposed_like")
        if value.clipped_pixel_fraction > REFERENCE_MAX_CLIPPED_PIXEL_FRACTION:
            issues.append("excessive_clipping")
        if value.foreground_fraction < REFERENCE_MIN_FOREGROUND_FRACTION:
            issues.append("insufficient_foreground")
        if value.center_luminance < REFERENCE_MIN_CENTER_LUMINANCE:
            issues.append("center_too_dark")
        if value.center_luminance > REFERENCE_MAX_CENTER_LUMINANCE:
            issues.append("center_too_bright")
    report = {
        "schema_version": "0.1",
        "status": "pass" if not issues else "rejected",
        "thresholds": thresholds,
        "observed": observed,
        "issue_codes": issues,
    }
    return report, issues


def _updated_manifest(
    manifest: RenderWorkflowManifest,
    **updates: object,
) -> RenderWorkflowManifest:
    value = manifest.model_dump(mode="json")
    value.update(updates)
    return RenderWorkflowManifest.model_validate(value)


def _terminal_manifest(
    manifest: RenderWorkflowManifest,
    *,
    status: str,
    stop_reason: str,
    output_artifacts: list[ArtifactRecord],
    iterations: list[WorkflowIteration],
    errors: list[str] | None = None,
) -> RenderWorkflowManifest:
    return _updated_manifest(
        manifest,
        status=status,
        stage="complete",
        stop_reason=stop_reason,
        finished_at=datetime.now(UTC),
        output_artifacts=[item.model_dump(mode="json") for item in output_artifacts],
        iterations=[item.model_dump(mode="json") for item in iterations],
        errors=errors or [],
    )


def load_training_reference_suite(path: str | Path) -> TrainingReferenceSuite:
    suite_path = Path(path).expanduser().resolve()
    try:
        return TrainingReferenceSuite.model_validate_json(
            suite_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise TrainingReferenceError(
            f"invalid training reference suite {suite_path}: {exc}"
        ) from exc


def _source_identity(
    *,
    campaign_suite: TrainingCampaignSuite,
    reference_suite: TrainingReferenceSuite,
    project_path: Path,
    asset_catalog_path: Path,
) -> None:
    if project_path.suffix.casefold() != ".uproject" or not project_path.is_file():
        raise TrainingReferenceError(f"not an existing .uproject file: {project_path}")
    if not asset_catalog_path.is_file():
        raise TrainingReferenceError(
            f"asset catalog does not exist: {asset_catalog_path}"
        )
    if project_path.stem != campaign_suite.project_name:
        raise TrainingReferenceError("project name does not match the campaign suite")
    if _sha256_file(project_path) != campaign_suite.project_descriptor_sha256:
        raise TrainingReferenceError("campaign project descriptor SHA-256 is stale")
    if _sha256_file(asset_catalog_path) != campaign_suite.asset_catalog_sha256:
        raise TrainingReferenceError("campaign asset catalog SHA-256 is stale")
    if reference_suite.campaign_suite_id != campaign_suite.suite_id:
        raise TrainingReferenceError("reference suite targets a different campaign suite")
    if reference_suite.campaign_suite_sha256 != canonical_sha256(campaign_suite):
        raise TrainingReferenceError("reference suite campaign SHA-256 is stale")


def _expectation_issues(
    case: TrainingCampaignCase,
    reference: TrainingReferenceCase,
) -> list[tuple[str, str]]:
    spec = reference.render_spec
    scene_assets = {item.asset.asset_id for item in spec.objects}
    material_assets = {
        assignment.material.asset_id
        for item in spec.objects
        for assignment in item.materials
    }
    all_assets = scene_assets | material_assets
    material_count = sum(len(item.materials) for item in spec.objects)
    issues: list[tuple[str, str]] = []
    missing_scene = sorted(
        set(case.expectation.required_scene_asset_ids) - scene_assets
    )
    missing_materials = sorted(
        set(case.expectation.required_material_asset_ids) - material_assets
    )
    forbidden = sorted(set(case.expectation.forbidden_asset_ids) & all_assets)
    if missing_scene:
        issues.append(("missing_required_scene_assets", ", ".join(missing_scene)))
    if missing_materials:
        issues.append(("missing_required_material_assets", ", ".join(missing_materials)))
    if forbidden:
        issues.append(("forbidden_assets", ", ".join(forbidden)))
    if not (
        case.expectation.minimum_object_count
        <= len(spec.objects)
        <= case.expectation.maximum_object_count
    ):
        issues.append(
            (
                "object_count",
                f"observed {len(spec.objects)}; expected "
                f"[{case.expectation.minimum_object_count}, "
                f"{case.expectation.maximum_object_count}]",
            )
        )
    if not (
        case.expectation.minimum_material_assignment_count
        <= material_count
        <= case.expectation.maximum_material_assignment_count
    ):
        issues.append(
            (
                "material_assignment_count",
                f"observed {material_count}; expected "
                f"[{case.expectation.minimum_material_assignment_count}, "
                f"{case.expectation.maximum_material_assignment_count}]",
            )
        )
    return issues


def _validate_reference_case(
    *,
    campaign_case: TrainingCampaignCase,
    reference: TrainingReferenceCase,
    cards_by_id: dict[str, Any],
) -> TrainingReferenceCaseValidation:
    spec = reference.render_spec
    scene_assets = sorted({item.asset.asset_id for item in spec.objects})
    material_assets = sorted(
        {
            assignment.material.asset_id
            for item in spec.objects
            for assignment in item.materials
        }
    )
    used_assets = set(scene_assets) | set(material_assets)
    retrieved_assets = set(reference.retrieved_asset_ids)
    issues: list[tuple[str, str]] = []
    if spec.source_prompt.strip() != campaign_case.prompt.strip():
        issues.append(
            (
                "source_prompt_mismatch",
                "RenderSpec source_prompt must exactly match the frozen campaign request",
            )
        )
    unknown_retrieved = sorted(retrieved_assets - set(cards_by_id))
    if unknown_retrieved:
        issues.append(("unknown_retrieved_assets", ", ".join(unknown_retrieved)))
    unavailable_used = sorted(used_assets - retrieved_assets)
    if unavailable_used:
        issues.append(("used_assets_not_retrieved", ", ".join(unavailable_used)))
    for item in spec.objects:
        card = cards_by_id.get(item.asset.asset_id)
        if card is None:
            continue
        if card.asset_type != "static_mesh":
            issues.append(
                (
                    f"object_asset_type_{item.object_id}",
                    f"object {item.object_id} uses {card.asset_type}, not static_mesh",
                )
            )
        if card.dimensions_cm is None:
            issues.append(
                (
                    f"object_dimensions_{item.object_id}",
                    f"object {item.object_id} has no AssetCard dimensions",
                )
            )
        slot_names = set(card.material_slots)
        for assignment in item.materials:
            material = cards_by_id.get(assignment.material.asset_id)
            if material is not None and material.asset_type != "material":
                issues.append(
                    (
                        f"material_asset_type_{item.object_id}",
                        f"{assignment.material.asset_id} is {material.asset_type}, not material",
                    )
                )
            if assignment.slot_name not in slot_names:
                issues.append(
                    (
                        f"material_slot_{item.object_id}",
                        f"slot {assignment.slot_name!r} is not present on {card.asset_id}",
                    )
                )
    issues.extend(_expectation_issues(campaign_case, reference))
    preflight = run_preflight(spec)
    if preflight.verdict != "pass":
        detail = "; ".join(issue.message for issue in preflight.issues[:4])
        issues.append(("preflight_not_passed", detail or preflight.summary))
    deduplicated = list(dict(issues).items())
    return TrainingReferenceCaseValidation(
        case_id=reference.case_id,
        status="ready" if not deduplicated else "rejected",
        render_spec_sha256=reference.render_spec_sha256,
        preflight_verdict=preflight.verdict,
        observed_scene_asset_ids=scene_assets,
        observed_material_asset_ids=material_assets,
        observed_object_count=len(spec.objects),
        issue_codes=[code for code, _ in deduplicated],
        issues=[detail for _, detail in deduplicated],
    )


def validate_training_reference_suite(
    *,
    reference_suite_path: str | Path,
    campaign_suite_path: str | Path,
    project_path: str | Path,
    asset_catalog_path: str | Path,
) -> TrainingReferenceValidationReport:
    """Statically verify Golden References before any Unreal or model process runs."""

    reference_suite = load_training_reference_suite(reference_suite_path)
    campaign_suite = load_training_campaign_suite(campaign_suite_path)
    project = Path(project_path).expanduser().resolve()
    catalog = Path(asset_catalog_path).expanduser().resolve()
    _source_identity(
        campaign_suite=campaign_suite,
        reference_suite=reference_suite,
        project_path=project,
        asset_catalog_path=catalog,
    )
    try:
        cards = load_asset_card_catalog(catalog)
    except AssetIndexError as exc:
        raise TrainingReferenceError(str(exc)) from exc
    cards_by_id = {card.asset_id: card for card in cards}
    campaign_cases = {case.case_id: case for case in campaign_suite.cases}
    unknown_cases = sorted(
        {case.case_id for case in reference_suite.cases} - set(campaign_cases)
    )
    if unknown_cases:
        raise TrainingReferenceError(
            "reference cases are missing from the campaign suite: "
            + ", ".join(unknown_cases)
        )
    results = [
        _validate_reference_case(
            campaign_case=campaign_cases[reference.case_id],
            reference=reference,
            cards_by_id=cards_by_id,
        )
        for reference in reference_suite.cases
    ]
    ready = sum(result.status == "ready" for result in results)
    missing = [
        case.case_id
        for case in campaign_suite.cases
        if case.case_id not in {reference.case_id for reference in reference_suite.cases}
    ]
    payload = {
        "schema_version": "0.1",
        "reference_suite_id": reference_suite.reference_suite_id,
        "reference_suite_sha256": canonical_sha256(reference_suite),
        "campaign_suite_id": campaign_suite.suite_id,
        "campaign_suite_sha256": canonical_sha256(campaign_suite),
        "project_descriptor_sha256": _sha256_file(project),
        "asset_catalog_sha256": _sha256_file(catalog),
        "integrity_status": "passed" if ready == len(results) else "failed",
        "total_campaign_case_count": len(campaign_suite.cases),
        "reference_case_count": len(results),
        "ready_case_count": ready,
        "rejected_case_count": len(results) - ready,
        "missing_case_ids": missing,
        "full_coverage": not missing and len(results) == len(campaign_suite.cases),
        "ready_to_run": ready == len(results),
        "case_results": [result.model_dump(mode="json") for result in results],
    }
    payload["report_sha256"] = canonical_sha256(payload)
    return TrainingReferenceValidationReport.model_validate(payload)


def _frozen_retrieval_ids(
    *,
    retrieval_campaign_root: Path | None,
    case_id: str,
    required_asset_id: str,
    cards_by_id: dict[str, Any],
) -> list[str]:
    if retrieval_campaign_root is None:
        return [required_asset_id]
    attempts_root = retrieval_campaign_root / "workflows" / case_id
    candidates = sorted(
        attempts_root.glob("attempt-*/planning/retrieval.json"),
        key=lambda path: path.as_posix().casefold(),
    )
    if not candidates:
        raise TrainingReferenceError(
            f"no frozen retrieval evidence exists for reference case {case_id}"
        )
    try:
        value = json.loads(candidates[-1].read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ValueError("retrieval evidence is not a JSON array")
        ids = [item["asset_id"] for item in value]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise TrainingReferenceError(
            f"invalid frozen retrieval evidence for {case_id}: {exc}"
        ) from exc
    ids = list(dict.fromkeys(ids))
    unknown = sorted(set(ids) - set(cards_by_id))
    if unknown:
        raise TrainingReferenceError(
            f"frozen retrieval for {case_id} contains unknown assets: "
            + ", ".join(unknown)
        )
    if required_asset_id not in ids:
        ids.insert(0, required_asset_id)
    if len(ids) > 50:
        raise TrainingReferenceError(
            f"frozen retrieval for {case_id} exceeds the 50-asset contract limit"
        )
    return ids


def _single_asset_reference_spec(
    *,
    case: TrainingCampaignCase,
    card: Any,
) -> RenderSpec:
    if card.dimensions_cm is None:
        raise TrainingReferenceError(
            f"single-asset reference {case.case_id} has no dimensions evidence"
        )
    target = card.pivot_offset_cm
    initial = {
        "source_prompt": case.prompt,
        "scene_name": f"{case.case_id}_reference",
        "objects": [
            {
                "object_id": "subject",
                "asset": {
                    "asset_id": card.asset_id,
                    "display_name": card.display_name,
                },
                "materials": [],
            }
        ],
        "camera": {
            "camera_id": "main_camera",
            "transform": {
                "location_cm": {
                    "x": target.x - 100.0,
                    "y": target.y - 100.0,
                    "z": target.z + 35.0,
                }
            },
            "focal_length_mm": 50.0,
            "aperture_f_stop": 5.6,
            "exposure": {"mode": "fixed", "fixed_ev100": 11.0},
        },
        "lights": [
            {
                "light_id": "key_light",
                "kind": "directional",
                "transform": {
                    "rotation_deg": {"y": -35.0, "z": -45.0},
                },
                "intensity": 20_000.0,
                "intensity_unit": "lux",
            },
            {
                "light_id": "fill_light",
                "kind": "directional",
                "transform": {
                    "rotation_deg": {"y": -20.0, "z": 135.0},
                },
                "intensity": 4_000.0,
                "intensity_unit": "lux",
                "cast_shadows": False,
            },
        ],
        "render": {
            "width_px": 1280,
            "height_px": 720,
            "output_format": "png",
            "quality": "preview",
            "seed": 0,
        },
        "notes": [],
    }
    base_spec = RenderSpec.model_validate(initial)
    return frame_camera(
        base_spec,
        [card],
        margin_fraction=0.04,
        view_axis="preserve",
    ).spec


def compile_single_asset_reference_suite(
    *,
    campaign_suite_path: str | Path,
    project_path: str | Path,
    asset_catalog_path: str | Path,
    output_path: str | Path,
    reference_suite_id: str,
    retrieval_campaign_directory: str | Path | None = None,
    case_ids: list[str] | None = None,
) -> TrainingReferenceSuite:
    """Compile deterministic three-quarter references for frozen single-asset cases."""

    campaign_suite = load_training_campaign_suite(campaign_suite_path)
    project = Path(project_path).expanduser().resolve()
    catalog = Path(asset_catalog_path).expanduser().resolve()
    if project.suffix.casefold() != ".uproject" or not project.is_file():
        raise TrainingReferenceError(f"not an existing .uproject file: {project}")
    if project.stem != campaign_suite.project_name:
        raise TrainingReferenceError("project name does not match the campaign suite")
    if _sha256_file(project) != campaign_suite.project_descriptor_sha256:
        raise TrainingReferenceError("campaign project descriptor SHA-256 is stale")
    if _sha256_file(catalog) != campaign_suite.asset_catalog_sha256:
        raise TrainingReferenceError("campaign asset catalog SHA-256 is stale")
    try:
        cards = load_asset_card_catalog(catalog)
    except AssetIndexError as exc:
        raise TrainingReferenceError(str(exc)) from exc
    cards_by_id = {card.asset_id: card for card in cards}
    single_cases = [
        case for case in campaign_suite.cases if case.category == "single_asset"
    ]
    if case_ids:
        if len(case_ids) != len(set(case_ids)):
            raise TrainingReferenceError("reference compiler case IDs must be unique")
        case_map = {case.case_id: case for case in single_cases}
        missing = [case_id for case_id in case_ids if case_id not in case_map]
        if missing:
            raise TrainingReferenceError(
                "reference compiler supports only known single_asset cases: "
                + ", ".join(missing)
            )
        selected_cases = [case_map[case_id] for case_id in case_ids]
    else:
        selected_cases = single_cases
    if not selected_cases:
        raise TrainingReferenceError("campaign suite has no selected single_asset cases")
    retrieval_root = (
        Path(retrieval_campaign_directory).expanduser().resolve()
        if retrieval_campaign_directory is not None
        else None
    )
    references: list[TrainingReferenceCase] = []
    for case in selected_cases:
        expectation = case.expectation
        if (
            len(expectation.required_scene_asset_ids) != 1
            or expectation.required_material_asset_ids
            or expectation.minimum_object_count != 1
            or expectation.maximum_object_count != 1
            or expectation.minimum_material_assignment_count != 0
            or expectation.maximum_material_assignment_count != 0
        ):
            raise TrainingReferenceError(
                f"single-asset compiler cannot satisfy case shape {case.case_id}"
            )
        asset_id = expectation.required_scene_asset_ids[0]
        card = cards_by_id.get(asset_id)
        if card is None:
            raise TrainingReferenceError(
                f"single-asset reference {case.case_id} uses unknown asset {asset_id}"
            )
        if card.asset_type != "static_mesh":
            raise TrainingReferenceError(
                f"single-asset reference {case.case_id} requires a static mesh"
            )
        spec = _single_asset_reference_spec(case=case, card=card)
        references.append(
            TrainingReferenceCase(
                case_id=case.case_id,
                authoring_source="deterministic_compiler",
                rationale=(
                    "Deterministic single-asset compiler v0.1: exact required mesh, "
                    "no unsolicited material, bounded elevated three-quarter camera, "
                    "fixed EV100 11, and neutral key/fill directional lighting."
                ),
                retrieved_asset_ids=_frozen_retrieval_ids(
                    retrieval_campaign_root=retrieval_root,
                    case_id=case.case_id,
                    required_asset_id=asset_id,
                    cards_by_id=cards_by_id,
                ),
                render_spec=spec,
                render_spec_sha256=canonical_sha256(spec),
            )
        )
    suite = TrainingReferenceSuite(
        reference_suite_id=reference_suite_id,
        campaign_suite_id=campaign_suite.suite_id,
        campaign_suite_sha256=canonical_sha256(campaign_suite),
        cases=references,
    )
    results = [
        _validate_reference_case(
            campaign_case=case,
            reference=reference,
            cards_by_id=cards_by_id,
        )
        for case, reference in zip(selected_cases, references, strict=True)
    ]
    rejected = [result for result in results if result.status == "rejected"]
    if rejected:
        details = "; ".join(
            f"{result.case_id}: {', '.join(result.issue_codes)}"
            for result in rejected
        )
        raise TrainingReferenceError(
            "compiled references failed their own static gate: " + details
        )
    output = Path(output_path).expanduser().resolve()
    if output.exists():
        raise TrainingReferenceError(f"reference output already exists: {output}")
    _write_json(output, suite.model_dump(mode="json"))
    return suite


def create_training_reference_campaign_plan(
    *,
    reference_suite_path: str | Path,
    campaign_suite_path: str | Path,
    campaign_directory: str | Path,
    campaign_id: str,
    project_path: str | Path,
    asset_catalog_path: str | Path,
    evaluator_model: str,
    max_attempts_per_case: int = 2,
) -> tuple[Path, TrainingCampaignRun, TrainingReferenceValidationReport]:
    """Create a normal campaign whose planner target is a validated reference suite."""

    report = validate_training_reference_suite(
        reference_suite_path=reference_suite_path,
        campaign_suite_path=campaign_suite_path,
        project_path=project_path,
        asset_catalog_path=asset_catalog_path,
    )
    if not report.ready_to_run:
        raise TrainingReferenceError("reference suite has rejected cases")
    reference_suite = load_training_reference_suite(reference_suite_path)
    provider = (
        "human"
        if all(case.authoring_source == "human_authored" for case in reference_suite.cases)
        else "local"
    )
    manifest_path, run = create_training_campaign_plan(
        suite_path=campaign_suite_path,
        campaign_directory=campaign_directory,
        campaign_id=campaign_id,
        project_path=project_path,
        asset_catalog_path=asset_catalog_path,
        planner_model=REFERENCE_PLANNER_MODEL,
        evaluator_model=evaluator_model,
        planner_provider=provider,
        reference_suite_sha256=report.reference_suite_sha256,
        case_ids=[case.case_id for case in reference_suite.cases],
        max_attempts_per_case=max_attempts_per_case,
    )
    snapshot_path = manifest_path.parent / REFERENCE_SNAPSHOT_FILENAME
    _write_json(snapshot_path, reference_suite.model_dump(mode="json"))
    return manifest_path, run, report


def validate_training_reference_campaign_runtime(
    *,
    campaign_manifest_path: str | Path,
    reference_suite_path: str | Path,
    campaign_suite_path: str | Path,
    project_path: str | Path,
    asset_catalog_path: str | Path,
) -> tuple[
    TrainingCampaignRun,
    TrainingCampaignSuite,
    TrainingReferenceSuite,
    TrainingReferenceValidationReport,
]:
    run, campaign_suite = validate_training_campaign_runtime(
        campaign_manifest_path=campaign_manifest_path,
        project_path=project_path,
        asset_catalog_path=asset_catalog_path,
    )
    reference_suite = load_training_reference_suite(reference_suite_path)
    report = validate_training_reference_suite(
        reference_suite_path=reference_suite_path,
        campaign_suite_path=campaign_suite_path,
        project_path=project_path,
        asset_catalog_path=asset_catalog_path,
    )
    if not report.ready_to_run:
        raise TrainingReferenceError("reference suite has rejected cases")
    if run.reference_suite_sha256 != report.reference_suite_sha256:
        raise TrainingReferenceError("campaign reference suite SHA-256 is stale")
    snapshot_path = Path(campaign_manifest_path).resolve().parent / REFERENCE_SNAPSHOT_FILENAME
    snapshot = load_training_reference_suite(snapshot_path)
    if canonical_sha256(snapshot) != report.reference_suite_sha256:
        raise TrainingReferenceError("campaign reference suite snapshot is stale")
    selected = [case.case_id for case in reference_suite.cases]
    if run.selected_case_ids != selected:
        raise TrainingReferenceError("campaign selected cases do not match reference suite order")
    if canonical_sha256(campaign_suite) != reference_suite.campaign_suite_sha256:
        raise TrainingReferenceError("runtime campaign suite does not match references")
    return run, campaign_suite, reference_suite, report


def run_training_reference_workflow(
    *,
    reference: TrainingReferenceCase,
    campaign_case: TrainingCampaignCase,
    vision_client: Any,
    vision_model: str,
    uproject_path: str | Path,
    engine_root: str | Path,
    asset_catalog_path: str | Path,
    workflow_directory: str | Path,
    workflow_id: str,
    timeout_seconds: int | None = None,
    preview_runner=run_unreal_preview,
    preview_evaluator=evaluate_preview_run,
) -> TrainingReferenceWorkflowResult:
    """Render one trusted target exactly once; never ask a planner or correction model."""

    project = Path(uproject_path).expanduser().resolve()
    engine = Path(engine_root).expanduser().resolve()
    catalog_path = Path(asset_catalog_path).expanduser().resolve()
    root = Path(workflow_directory).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise TrainingReferenceError(f"workflow directory is not empty: {root}")
    try:
        cards = load_asset_card_catalog(catalog_path)
    except AssetIndexError as exc:
        raise TrainingReferenceError(str(exc)) from exc
    cards_by_id = {card.asset_id: card for card in cards}
    retrieved_cards = [cards_by_id[asset_id] for asset_id in reference.retrieved_asset_ids]
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "workflow_manifest.json"
    request_path = root / "inputs" / "request.json"
    source_catalog_copy = root / "inputs" / "asset_cards.json"
    reference_path = root / "inputs" / "golden_reference_case.json"
    retrieval_path = root / "planning" / "retrieval.json"
    retrieved_catalog_path = root / "planning" / "retrieved_asset_cards.json"
    planner_raw_path = root / "planning" / "planner_raw.json"
    planner_metrics_path = root / "planning" / "planner_metrics.json"
    initial_spec_path = root / "planning" / "render_spec.json"
    spec_path = root / "specs" / "iteration-001.json"
    effective_timeout = timeout_seconds or campaign_case.timeout_seconds
    _write_json(
        request_path,
        {
            "workflow_id": workflow_id,
            "prompt": campaign_case.prompt,
            "mode": "golden_reference",
            "reference_suite_case_id": reference.case_id,
            "uproject_path": str(project),
            "engine_root": str(engine),
            "vision_model": vision_model,
            "timeout_seconds": effective_timeout,
        },
    )
    _write_json(source_catalog_copy, [card.model_dump(mode="json") for card in cards])
    _write_json(reference_path, reference.model_dump(mode="json"))
    hits = [
        AssetSearchHit(
            rank=index,
            asset_id=card.asset_id,
            display_name=card.display_name,
            asset_type=card.asset_type,
            engine_path=card.engine_path,
            distance=0.0,
            similarity=1.0,
            document=asset_document(card),
        )
        for index, card in enumerate(retrieved_cards, start=1)
    ]
    _write_json(retrieval_path, [asdict(hit) for hit in hits])
    _write_json(
        retrieved_catalog_path,
        [card.model_dump(mode="json") for card in retrieved_cards],
    )
    for path in (planner_raw_path, initial_spec_path, spec_path):
        _write_json(path, reference.render_spec.model_dump(mode="json"))
    _write_json(
        planner_metrics_path,
        {
            "model": REFERENCE_PLANNER_MODEL,
            "provider": reference.authoring_source,
            "model_invoked": False,
            "reference_render_spec_sha256": reference.render_spec_sha256,
        },
    )
    input_artifacts = [
        _artifact("workflow_request", request_path, root),
        _artifact("asset_catalog", source_catalog_copy, root),
        _artifact("golden_reference_case", reference_path, root),
    ]
    output_artifacts = [
        _artifact("retrieval_results", retrieval_path, root),
        _artifact("retrieved_asset_catalog", retrieved_catalog_path, root),
        _artifact("planner_raw", planner_raw_path, root),
        _artifact("planner_metrics", planner_metrics_path, root),
        _artifact("initial_render_spec", initial_spec_path, root),
    ]
    provider = "human" if reference.authoring_source == "human_authored" else "local"
    manifest = RenderWorkflowManifest(
        workflow_id=workflow_id,
        status="running",
        stage="preflight",
        prompt=campaign_case.prompt,
        project_name=project.stem,
        project_descriptor_sha256=_sha256_file(project),
        planner={"provider": provider, "model": REFERENCE_PLANNER_MODEL},
        evaluator={"provider": "ollama", "model": vision_model},
        max_iterations=1,
        retrieved_asset_ids=reference.retrieved_asset_ids,
        started_at=datetime.now(UTC),
        input_artifacts=input_artifacts,
        output_artifacts=output_artifacts,
    )
    _write_json(manifest_path, manifest.model_dump(mode="json"))
    iterations: list[WorkflowIteration] = []
    try:
        preflight = run_preflight(reference.render_spec)
        preflight_path = root / "specs" / "iteration-001.preflight.json"
        _write_json(preflight_path, preflight.model_dump(mode="json"))
        output_artifacts.extend(
            (
                _artifact("iteration_001_render_spec", spec_path, root),
                _artifact("iteration_001_preflight", preflight_path, root),
            )
        )
        if preflight.verdict != "pass":
            manifest = _terminal_manifest(
                manifest,
                status="stopped",
                stop_reason="preflight_rejected",
                output_artifacts=output_artifacts,
                iterations=iterations,
            )
            _write_json(manifest_path, manifest.model_dump(mode="json"))
            return TrainingReferenceWorkflowResult(manifest=manifest)

        iteration_root = root / "iterations" / "iteration-001"
        manifest = _updated_manifest(manifest, stage="rendering")
        _write_json(manifest_path, manifest.model_dump(mode="json"))
        preview_manifest, _ = preview_runner(
            project,
            engine_root=engine,
            render_spec_path=spec_path,
            asset_catalog_path=retrieved_catalog_path,
            run_directory=iteration_root,
            run_id=(workflow_id[:60] + "_i01")[:64],
            timeout_seconds=effective_timeout,
            fail_on_warning=False,
        )
        preview_manifest_path = iteration_root / "run_manifest.json"
        output_artifacts.append(
            _artifact("iteration_001_preview_manifest", preview_manifest_path, root)
        )
        for preview_artifact in preview_manifest.output_artifacts:
            artifact_path = iteration_root / preview_artifact.path
            if artifact_path.is_file():
                output_artifacts.append(
                    _artifact(f"iteration_001_{preview_artifact.role}", artifact_path, root)
                )

        manifest = _updated_manifest(manifest, stage="evaluation")
        _write_json(manifest_path, manifest.model_dump(mode="json"))
        evaluation: PreviewEvaluationResult = preview_evaluator(
            vision_client,
            model=vision_model,
            run_directory=iteration_root,
        )
        evaluation_path = iteration_root / "evaluation.json"
        evaluation_metrics_path = iteration_root / "evaluation_metrics.json"
        _write_json(evaluation_path, evaluation.report.model_dump(mode="json"))
        _write_json(evaluation_metrics_path, _response_metrics(evaluation.response))
        output_artifacts.extend(
            (
                _artifact("iteration_001_evaluation", evaluation_path, root),
                _artifact("iteration_001_evaluation_metrics", evaluation_metrics_path, root),
            )
        )
        iteration_values: dict[str, Any] = {
            "iteration": 1,
            "run_directory": "iterations/iteration-001",
            "render_spec_sha256": canonical_sha256(reference.render_spec),
            "preflight_report_sha256": canonical_sha256(preflight),
            "preflight_verdict": preflight.verdict,
            "preview_manifest_sha256": canonical_sha256(preview_manifest),
            "evaluation_report_sha256": canonical_sha256(evaluation.report),
            "evaluation_verdict": evaluation.report.verdict,
        }
        if evaluation.statistics is not None:
            statistics_path = iteration_root / "image_statistics.json"
            _write_json(statistics_path, evaluation.statistics.model_dump(mode="json"))
            output_artifacts.append(
                _artifact("iteration_001_image_statistics", statistics_path, root)
            )
            iteration_values["image_statistics_sha256"] = canonical_sha256(
                evaluation.statistics
            )
        quality_report, quality_issues = _reference_pixel_quality(evaluation.statistics)
        quality_path = iteration_root / "reference_pixel_quality.json"
        _write_json(quality_path, quality_report)
        output_artifacts.append(
            _artifact("iteration_001_reference_pixel_quality", quality_path, root)
        )
        iterations.append(WorkflowIteration.model_validate(iteration_values))
        if evaluation.report.verdict == "pass" and not quality_issues:
            output_artifacts.append(_artifact("final_render_spec", spec_path, root))
            manifest = _terminal_manifest(
                manifest,
                status="succeeded",
                stop_reason="evaluator_passed",
                output_artifacts=output_artifacts,
                iterations=iterations,
            )
        elif quality_issues:
            manifest = _terminal_manifest(
                manifest,
                status="stopped",
                stop_reason="reference_quality_rejected",
                output_artifacts=output_artifacts,
                iterations=iterations,
            )
        else:
            manifest = _terminal_manifest(
                manifest,
                status="stopped",
                stop_reason="max_iterations_reached",
                output_artifacts=output_artifacts,
                iterations=iterations,
            )
        _write_json(manifest_path, manifest.model_dump(mode="json"))
        return TrainingReferenceWorkflowResult(manifest=manifest)
    except Exception as exc:
        message = str(exc)[:4000] or type(exc).__name__
        failed = _terminal_manifest(
            manifest,
            status="failed",
            stop_reason="stage_failed",
            output_artifacts=output_artifacts,
            iterations=iterations,
            errors=[message],
        )
        _write_json(manifest_path, failed.model_dump(mode="json"))
        if isinstance(exc, TrainingReferenceError):
            raise
        raise TrainingReferenceError(
            f"reference workflow failed during {manifest.stage}: {message}"
        ) from exc


def reference_campaign_manifest(campaign_directory: str | Path) -> Path:
    return Path(campaign_directory).expanduser().resolve() / CAMPAIGN_MANIFEST_FILENAME
