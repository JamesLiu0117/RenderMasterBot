"""Bounded end-to-end orchestration for one local RenderMasterBot workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from render_master_bot.asset_index import (
    AssetSearchHit,
    AssetIndexError,
    load_asset_card_catalog,
)
from render_master_bot.camera_framing import (
    VIEW_AXES,
    CameraFramingError,
    ViewAxis,
    frame_camera,
)
from render_master_bot.contracts import (
    ArtifactRecord,
    RenderWorkflowManifest,
    WorkflowIteration,
)
from render_master_bot.image_comparison import compare_image_statistics
from render_master_bot.correction_planner import (
    CorrectionPlanningError,
    CorrectionPlanningResult,
    DETERMINISTIC_FRAMING_PATHS,
    plan_correction,
)
from render_master_bot.models import RenderSpec
from render_master_bot.ollama import StructuredResponse
from render_master_bot.planner import PlanningError, ScenePlanner
from render_master_bot.preflight import run_preflight
from render_master_bot.serialization import canonical_sha256
from render_master_bot.studio_calibration import calibrate_studio_preview
from render_master_bot.unreal_preview import run_unreal_preview
from render_master_bot.visual_evaluator import PreviewEvaluationResult, evaluate_preview_run


class WorkflowError(RuntimeError):
    """Raised when a workflow stage fails after preserving available evidence."""


class AssetSearcher(Protocol):
    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        asset_types: list[str] | None = None,
    ) -> list[AssetSearchHit]: ...


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    manifest: RenderWorkflowManifest


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def _artifact(role: str, path: Path, workflow_root: Path) -> ArtifactRecord:
    relative = path.resolve().relative_to(workflow_root.resolve()).as_posix()
    return ArtifactRecord(role=role, path=relative, sha256=_sha256_file(path))


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


def _updated(
    manifest: RenderWorkflowManifest,
    **updates: object,
) -> RenderWorkflowManifest:
    value = manifest.model_dump(mode="json")
    value.update(updates)
    return RenderWorkflowManifest.model_validate(value)


def _write_manifest(path: Path, manifest: RenderWorkflowManifest) -> None:
    _write_json(path, manifest.model_dump(mode="json"))


def _iteration_run_id(workflow_id: str, iteration: int) -> str:
    suffix = f"_i{iteration:02d}"
    return workflow_id[: 64 - len(suffix)] + suffix


def _unique_hits(hits: list[AssetSearchHit]) -> list[AssetSearchHit]:
    unique: list[AssetSearchHit] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.asset_id not in seen:
            unique.append(hit)
            seen.add(hit.asset_id)
    return unique


def _terminal_manifest(
    manifest: RenderWorkflowManifest,
    *,
    status: str,
    stop_reason: str,
    output_artifacts: list[ArtifactRecord],
    iterations: list[WorkflowIteration],
    errors: list[str] | None = None,
) -> RenderWorkflowManifest:
    return _updated(
        manifest,
        status=status,
        stage="complete",
        stop_reason=stop_reason,
        finished_at=datetime.now(UTC),
        output_artifacts=[item.model_dump(mode="json") for item in output_artifacts],
        iterations=[item.model_dump(mode="json") for item in iterations],
        errors=errors or [],
    )


def run_render_workflow(
    *,
    planner_client,
    vision_client,
    correction_client,
    asset_searcher: AssetSearcher,
    planner_model: str,
    vision_model: str,
    prompt: str,
    uproject_path: str | Path,
    engine_root: str | Path,
    asset_catalog_path: str | Path,
    workflow_directory: str | Path,
    workflow_id: str,
    retrieve_assets: int = 8,
    retrieve_materials: int = 5,
    max_iterations: int = 2,
    view_axis: ViewAxis = "auto-product",
    margin_fraction: float = 0.02,
    timeout_seconds: int = 600,
    fail_on_warning: bool = False,
    studio_calibration: bool = True,
    preview_runner=run_unreal_preview,
    preview_evaluator=evaluate_preview_run,
    correction_planner=plan_correction,
) -> WorkflowResult:
    """Run retrieval through bounded correction without overwriting prior evidence."""

    project = Path(uproject_path).expanduser().resolve()
    engine = Path(engine_root).expanduser().resolve()
    source_catalog_path = Path(asset_catalog_path).expanduser().resolve()
    root = Path(workflow_directory).expanduser().resolve()
    if project.suffix.casefold() != ".uproject" or not project.is_file():
        raise WorkflowError(f"not an existing .uproject file: {project}")
    if not source_catalog_path.is_file():
        raise WorkflowError(f"asset catalog does not exist: {source_catalog_path}")
    if not prompt.strip():
        raise WorkflowError("workflow prompt cannot be empty")
    if not 1 <= retrieve_assets <= 50:
        raise WorkflowError("retrieve_assets must be between 1 and 50")
    if not 0 <= retrieve_materials <= 50:
        raise WorkflowError("retrieve_materials must be between 0 and 50")
    if not 1 <= max_iterations <= 5:
        raise WorkflowError("max_iterations must be between 1 and 5")
    if view_axis not in VIEW_AXES:
        raise WorkflowError(f"unsupported view axis: {view_axis!r}")
    if not 0 <= margin_fraction < 0.45:
        raise WorkflowError("margin_fraction must be at least 0 and below 0.45")
    if not 1 <= timeout_seconds <= 3600:
        raise WorkflowError("timeout_seconds must be between 1 and 3600")
    if root.exists() and any(root.iterdir()):
        raise WorkflowError(f"workflow directory is not empty: {root}")

    try:
        cards = load_asset_card_catalog(source_catalog_path)
    except AssetIndexError as exc:
        raise WorkflowError(str(exc)) from exc
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "workflow_manifest.json"
    request_path = root / "inputs" / "request.json"
    catalog_path = root / "inputs" / "asset_cards.json"
    _write_json(
        request_path,
        {
            "workflow_id": workflow_id,
            "prompt": prompt.strip(),
            "uproject_path": str(project),
            "engine_root": str(engine),
            "planner_model": planner_model,
            "vision_model": vision_model,
            "retrieve_assets": retrieve_assets,
            "retrieve_materials": retrieve_materials,
            "max_iterations": max_iterations,
            "view_axis": view_axis,
            "margin_fraction": margin_fraction,
            "timeout_seconds": timeout_seconds,
            "fail_on_warning": fail_on_warning,
            "studio_calibration": studio_calibration,
        },
    )
    _write_json(catalog_path, [card.model_dump(mode="json") for card in cards])
    input_artifacts = [
        _artifact("workflow_request", request_path, root),
        _artifact("asset_catalog", catalog_path, root),
    ]
    manifest = RenderWorkflowManifest(
        workflow_id=workflow_id,
        status="running",
        stage="initializing",
        prompt=prompt.strip(),
        project_name=project.stem,
        project_descriptor_sha256=_sha256_file(project),
        planner={"provider": "ollama", "model": planner_model},
        evaluator={"provider": "ollama", "model": vision_model},
        max_iterations=max_iterations,
        started_at=datetime.now(UTC),
        input_artifacts=input_artifacts,
    )
    _write_manifest(manifest_path, manifest)
    output_artifacts: list[ArtifactRecord] = []
    iterations: list[WorkflowIteration] = []
    previous_statistics = None

    try:
        manifest = _updated(manifest, stage="retrieval")
        _write_manifest(manifest_path, manifest)
        mesh_hits = asset_searcher.search(
            prompt,
            limit=retrieve_assets,
            asset_types=["static_mesh"],
        )
        material_hits = (
            asset_searcher.search(
                prompt,
                limit=retrieve_materials,
                asset_types=["material"],
            )
            if retrieve_materials
            else []
        )
        hits = _unique_hits([*mesh_hits, *material_hits])
        if not mesh_hits:
            raise WorkflowError("retrieval returned no static mesh candidates")
        catalog_ids = {card.asset_id for card in cards}
        unknown_ids = sorted({hit.asset_id for hit in hits} - catalog_ids)
        if unknown_ids:
            raise WorkflowError(
                "retrieval returned IDs outside the supplied asset catalog: "
                + ", ".join(unknown_ids)
            )
        retrieval_path = root / "planning" / "retrieval.json"
        retrieved_catalog_path = root / "planning" / "retrieved_asset_cards.json"
        _write_json(retrieval_path, [asdict(hit) for hit in hits])
        asset_ids = [hit.asset_id for hit in hits]
        cards_by_id = {card.asset_id: card for card in cards}
        retrieved_cards = [cards_by_id[asset_id] for asset_id in asset_ids]
        _write_json(
            retrieved_catalog_path,
            [card.model_dump(mode="json") for card in retrieved_cards],
        )
        output_artifacts.extend((
            _artifact("retrieval_results", retrieval_path, root),
            _artifact("retrieved_asset_catalog", retrieved_catalog_path, root),
        ))
        asset_context = [hit.planner_context() for hit in hits]
        manifest = _updated(
            manifest,
            retrieved_asset_ids=asset_ids,
            output_artifacts=[item.model_dump(mode="json") for item in output_artifacts],
        )

        manifest = _updated(manifest, stage="planning")
        _write_manifest(manifest_path, manifest)
        try:
            plan_result = ScenePlanner(planner_client).plan(
                model=planner_model,
                prompt=prompt,
                asset_ids=asset_ids,
                asset_context=asset_context,
            )
        except PlanningError as exc:
            if exc.response is not None:
                invalid_raw_path = root / "planning" / "planner_invalid_raw.json"
                invalid_metrics_path = root / "planning" / "planner_invalid_metrics.json"
                _write_text(invalid_raw_path, exc.response.content)
                invalid_metrics = _response_metrics(exc.response)
                invalid_metrics.update({
                    "status": "invalid",
                    "attempt_count": exc.attempt_count,
                    "error": str(exc),
                })
                _write_json(invalid_metrics_path, invalid_metrics)
                output_artifacts.extend((
                    _artifact("planner_invalid_raw", invalid_raw_path, root),
                    _artifact("planner_invalid_metrics", invalid_metrics_path, root),
                ))
            raise
        raw_plan_path = root / "planning" / "planner_raw.json"
        planner_metrics_path = root / "planning" / "planner_metrics.json"
        initial_spec_path = root / "planning" / "render_spec.json"
        _write_text(raw_plan_path, plan_result.response.content)
        planner_metrics = _response_metrics(plan_result.response)
        planner_metrics["attempt_count"] = plan_result.attempt_count
        _write_json(planner_metrics_path, planner_metrics)
        _write_json(initial_spec_path, plan_result.spec.model_dump(mode="json"))
        output_artifacts.extend((
            _artifact("planner_raw", raw_plan_path, root),
            _artifact("planner_metrics", planner_metrics_path, root),
            _artifact("initial_render_spec", initial_spec_path, root),
        ))

        planned_spec = plan_result.spec
        if studio_calibration:
            calibration = calibrate_studio_preview(planned_spec)
            planned_spec = calibration.spec
            if calibration.patch is not None:
                calibration_patch_path = root / "planning" / "studio_calibration.patch.json"
                _write_json(
                    calibration_patch_path,
                    calibration.patch.model_dump(mode="json"),
                )
                output_artifacts.append(
                    _artifact("studio_calibration_patch", calibration_patch_path, root)
                )

        try:
            framing = frame_camera(
                planned_spec,
                retrieved_cards,
                margin_fraction=margin_fraction,
                view_axis=view_axis,
            )
            framed_spec = framing.spec
            framing_patch_path = root / "planning" / "camera_framing.patch.json"
            _write_json(framing_patch_path, framing.patch.model_dump(mode="json"))
            output_artifacts.append(_artifact("camera_framing_patch", framing_patch_path, root))
        except CameraFramingError as exc:
            if "already matches the requested deterministic framing" not in str(exc):
                raise
            framed_spec = planned_spec

        current_spec: RenderSpec = framed_spec
        current_spec_path = root / "specs" / "iteration-001.json"
        _write_json(current_spec_path, current_spec.model_dump(mode="json"))
        seen_spec_hashes = {canonical_sha256(current_spec)}

        for iteration_number in range(1, max_iterations + 1):
            manifest = _updated(
                manifest,
                stage="preflight",
                output_artifacts=[item.model_dump(mode="json") for item in output_artifacts],
                iterations=[item.model_dump(mode="json") for item in iterations],
            )
            _write_manifest(manifest_path, manifest)
            preflight = run_preflight(current_spec)
            preflight_path = root / "specs" / f"iteration-{iteration_number:03d}.preflight.json"
            _write_json(preflight_path, preflight.model_dump(mode="json"))
            output_artifacts.extend((
                _artifact(f"iteration_{iteration_number:03d}_render_spec", current_spec_path, root),
                _artifact(f"iteration_{iteration_number:03d}_preflight", preflight_path, root),
            ))
            preflight_rejected = preflight.verdict == "fail" or (
                preflight.verdict == "needs_review" and fail_on_warning
            )
            if preflight_rejected:
                manifest = _terminal_manifest(
                    manifest,
                    status="stopped",
                    stop_reason="preflight_rejected",
                    output_artifacts=output_artifacts,
                    iterations=iterations,
                )
                _write_manifest(manifest_path, manifest)
                return WorkflowResult(manifest=manifest)

            iteration_root = root / "iterations" / f"iteration-{iteration_number:03d}"
            manifest = _updated(manifest, stage="rendering")
            _write_manifest(manifest_path, manifest)
            preview_manifest, _ = preview_runner(
                project,
                engine_root=engine,
                render_spec_path=current_spec_path,
                asset_catalog_path=retrieved_catalog_path,
                run_directory=iteration_root,
                run_id=_iteration_run_id(workflow_id, iteration_number),
                timeout_seconds=timeout_seconds,
                fail_on_warning=fail_on_warning,
            )
            preview_manifest_path = iteration_root / "run_manifest.json"
            output_artifacts.append(
                _artifact(
                    f"iteration_{iteration_number:03d}_preview_manifest",
                    preview_manifest_path,
                    root,
                )
            )
            for preview_artifact in preview_manifest.output_artifacts:
                artifact_path = iteration_root / preview_artifact.path
                if artifact_path.is_file():
                    output_artifacts.append(
                        _artifact(
                            f"iteration_{iteration_number:03d}_{preview_artifact.role}",
                            artifact_path,
                            root,
                        )
                    )

            manifest = _updated(manifest, stage="evaluation")
            _write_manifest(manifest_path, manifest)
            evaluation: PreviewEvaluationResult = preview_evaluator(
                vision_client,
                model=vision_model,
                run_directory=iteration_root,
            )
            evaluation_path = iteration_root / "evaluation.json"
            evaluation_metrics_path = iteration_root / "evaluation_metrics.json"
            _write_json(evaluation_path, evaluation.report.model_dump(mode="json"))
            _write_json(evaluation_metrics_path, _response_metrics(evaluation.response))
            output_artifacts.extend((
                _artifact(f"iteration_{iteration_number:03d}_evaluation", evaluation_path, root),
                _artifact(
                    f"iteration_{iteration_number:03d}_evaluation_metrics",
                    evaluation_metrics_path,
                    root,
                ),
            ))
            iteration_values: dict[str, Any] = {
                "iteration": iteration_number,
                "run_directory": iteration_root.relative_to(root).as_posix(),
                "render_spec_sha256": canonical_sha256(current_spec),
                "preflight_report_sha256": canonical_sha256(preflight),
                "preflight_verdict": preflight.verdict,
                "preview_manifest_sha256": canonical_sha256(preview_manifest),
                "evaluation_report_sha256": canonical_sha256(evaluation.report),
                "evaluation_verdict": evaluation.report.verdict,
            }
            comparison = None
            if evaluation.statistics is not None:
                statistics_path = iteration_root / "image_statistics.json"
                _write_json(
                    statistics_path,
                    evaluation.statistics.model_dump(mode="json"),
                )
                output_artifacts.append(_artifact(
                    f"iteration_{iteration_number:03d}_image_statistics",
                    statistics_path,
                    root,
                ))
                iteration_values["image_statistics_sha256"] = canonical_sha256(
                    evaluation.statistics
                )
                if previous_statistics is not None:
                    comparison = compare_image_statistics(
                        previous_statistics,
                        evaluation.statistics,
                    )
                    comparison_path = iteration_root / "image_comparison.json"
                    _write_json(comparison_path, comparison.model_dump(mode="json"))
                    output_artifacts.append(_artifact(
                        f"iteration_{iteration_number:03d}_image_comparison",
                        comparison_path,
                        root,
                    ))
                    iteration_values["image_comparison_sha256"] = canonical_sha256(
                        comparison
                    )
                previous_statistics = evaluation.statistics

            if comparison is not None and comparison.outcome == "regressed":
                iterations.append(WorkflowIteration.model_validate(iteration_values))
                manifest = _terminal_manifest(
                    manifest,
                    status="stopped",
                    stop_reason="image_quality_regressed",
                    output_artifacts=output_artifacts,
                    iterations=iterations,
                )
                _write_manifest(manifest_path, manifest)
                return WorkflowResult(manifest=manifest)
            if evaluation.report.verdict == "pass":
                iterations.append(WorkflowIteration.model_validate(iteration_values))
                output_artifacts.append(_artifact("final_render_spec", current_spec_path, root))
                manifest = _terminal_manifest(
                    manifest,
                    status="succeeded",
                    stop_reason="evaluator_passed",
                    output_artifacts=output_artifacts,
                    iterations=iterations,
                )
                _write_manifest(manifest_path, manifest)
                return WorkflowResult(manifest=manifest)

            if iteration_number == max_iterations:
                iterations.append(WorkflowIteration.model_validate(iteration_values))
                manifest = _terminal_manifest(
                    manifest,
                    status="stopped",
                    stop_reason="max_iterations_reached",
                    output_artifacts=output_artifacts,
                    iterations=iterations,
                )
                _write_manifest(manifest_path, manifest)
                return WorkflowResult(manifest=manifest)

            manifest = _updated(manifest, stage="correction")
            _write_manifest(manifest_path, manifest)
            try:
                correction: CorrectionPlanningResult = correction_planner(
                    correction_client,
                    model=planner_model,
                    run_directory=iteration_root,
                    evaluation_path="evaluation.json",
                    protected_paths=(
                        DETERMINISTIC_FRAMING_PATHS
                        if view_axis != "preserve"
                        else None
                    ),
                )
            except CorrectionPlanningError as exc:
                if exc.response is not None:
                    invalid_raw_path = iteration_root / "correction_invalid_raw.json"
                    invalid_metrics_path = iteration_root / "correction_invalid_metrics.json"
                    _write_text(invalid_raw_path, exc.response.content)
                    invalid_metrics = _response_metrics(exc.response)
                    invalid_metrics.update({"status": "invalid", "error": str(exc)})
                    _write_json(invalid_metrics_path, invalid_metrics)
                    output_artifacts.extend((
                        _artifact(
                            f"iteration_{iteration_number:03d}_correction_invalid_raw",
                            invalid_raw_path,
                            root,
                        ),
                        _artifact(
                            f"iteration_{iteration_number:03d}_correction_invalid_metrics",
                            invalid_metrics_path,
                            root,
                        ),
                    ))
                raise
            decision_path = iteration_root / "correction.json"
            correction_metrics_path = iteration_root / "correction_metrics.json"
            if correction.initial_response is not None:
                first_attempt_path = iteration_root / "correction_first_attempt_raw.json"
                retry_path = iteration_root / "correction_retry.json"
                _write_text(first_attempt_path, correction.initial_response.content)
                _write_json(retry_path, {
                    "reason": correction.retry_reason,
                    "first_attempt_metrics": _response_metrics(
                        correction.initial_response
                    ),
                })
                output_artifacts.extend((
                    _artifact(
                        f"iteration_{iteration_number:03d}_correction_first_attempt_raw",
                        first_attempt_path,
                        root,
                    ),
                    _artifact(
                        f"iteration_{iteration_number:03d}_correction_retry",
                        retry_path,
                        root,
                    ),
                ))
            _write_json(decision_path, correction.decision.model_dump(mode="json"))
            correction_metrics = _response_metrics(correction.response)
            correction_metrics["attempt_count"] = correction.attempt_count
            _write_json(correction_metrics_path, correction_metrics)
            output_artifacts.extend((
                _artifact(f"iteration_{iteration_number:03d}_correction", decision_path, root),
                _artifact(
                    f"iteration_{iteration_number:03d}_correction_metrics",
                    correction_metrics_path,
                    root,
                ),
            ))
            iteration_values.update({
                "correction_outcome": correction.decision.outcome,
                "correction_decision_sha256": canonical_sha256(correction.decision),
            })
            if correction.decision.outcome == "unresolved":
                iterations.append(WorkflowIteration.model_validate(iteration_values))
                manifest = _terminal_manifest(
                    manifest,
                    status="stopped",
                    stop_reason="correction_unresolved",
                    output_artifacts=output_artifacts,
                    iterations=iterations,
                )
                _write_manifest(manifest_path, manifest)
                return WorkflowResult(manifest=manifest)
            if correction.corrected_spec is None:
                raise WorkflowError("patch correction produced no corrected RenderSpec")

            corrected_spec = correction.corrected_spec
            corrected_spec_path = iteration_root / "corrected_render_spec.json"
            _write_json(corrected_spec_path, corrected_spec.model_dump(mode="json"))
            output_artifacts.append(
                _artifact(
                    f"iteration_{iteration_number:03d}_corrected_render_spec",
                    corrected_spec_path,
                    root,
                )
            )
            corrected_hash = canonical_sha256(corrected_spec)
            iteration_values["corrected_render_spec_sha256"] = corrected_hash
            iterations.append(WorkflowIteration.model_validate(iteration_values))
            if corrected_hash in seen_spec_hashes:
                manifest = _terminal_manifest(
                    manifest,
                    status="stopped",
                    stop_reason="correction_cycle_detected",
                    output_artifacts=output_artifacts,
                    iterations=iterations,
                )
                _write_manifest(manifest_path, manifest)
                return WorkflowResult(manifest=manifest)

            seen_spec_hashes.add(corrected_hash)
            current_spec = corrected_spec
            current_spec_path = root / "specs" / f"iteration-{iteration_number + 1:03d}.json"
            _write_json(current_spec_path, current_spec.model_dump(mode="json"))

        raise WorkflowError("workflow exhausted its bounded loop without a stop reason")
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
        _write_manifest(manifest_path, failed)
        if isinstance(exc, WorkflowError):
            raise
        raise WorkflowError(f"workflow failed during {manifest.stage}: {message}") from exc
