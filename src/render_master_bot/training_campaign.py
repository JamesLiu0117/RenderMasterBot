"""Resumable, expectation-gated workflow campaigns for model training evidence."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from render_master_bot.contracts import (
    RenderWorkflowManifest,
    TrainingBaselineCaseOutcome,
    TrainingBaselineCategoryMetrics,
    TrainingBaselineReport,
    TrainingCampaignAttempt,
    TrainingCampaignCase,
    TrainingCampaignCaseResult,
    TrainingCampaignRun,
    TrainingCampaignSuite,
)
from render_master_bot.models import RenderSpec
from render_master_bot.serialization import canonical_sha256


CAMPAIGN_MANIFEST_FILENAME = "campaign_manifest.json"
SUITE_SNAPSHOT_FILENAME = "suite_snapshot.json"


class TrainingCampaignError(RuntimeError):
    """Raised when campaign identity, state, or evidence cannot be trusted."""


CampaignCaseRunner = Callable[[TrainingCampaignCase, Path, str], Path]
CampaignProgress = Callable[[str], None]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _bounded_error(value: object) -> str:
    text = str(value).strip() or "unspecified campaign error"
    return text[:4000]


def _portable_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise TrainingCampaignError(f"path escapes campaign root: {path}") from exc


def load_training_campaign_suite(path: str | Path) -> TrainingCampaignSuite:
    suite_path = Path(path).expanduser().resolve()
    try:
        return TrainingCampaignSuite.model_validate_json(
            suite_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise TrainingCampaignError(f"invalid training campaign suite {suite_path}: {exc}") from exc


def load_training_campaign_run(path: str | Path) -> TrainingCampaignRun:
    run_path = Path(path).expanduser().resolve()
    try:
        return TrainingCampaignRun.model_validate_json(
            run_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise TrainingCampaignError(f"invalid training campaign run {run_path}: {exc}") from exc


def _validate_suite_sources(
    suite: TrainingCampaignSuite,
    *,
    project_path: Path,
    asset_catalog_path: Path,
) -> None:
    if project_path.suffix.casefold() != ".uproject" or not project_path.is_file():
        raise TrainingCampaignError(f"not an existing .uproject file: {project_path}")
    if not asset_catalog_path.is_file():
        raise TrainingCampaignError(f"asset catalog does not exist: {asset_catalog_path}")
    if project_path.stem != suite.project_name:
        raise TrainingCampaignError(
            f"suite project {suite.project_name!r} does not match {project_path.stem!r}"
        )
    if _sha256_file(project_path) != suite.project_descriptor_sha256:
        raise TrainingCampaignError("suite project descriptor SHA-256 is stale")
    if _sha256_file(asset_catalog_path) != suite.asset_catalog_sha256:
        raise TrainingCampaignError("suite asset catalog SHA-256 is stale")


def validate_training_campaign_runtime(
    *,
    campaign_manifest_path: str | Path,
    project_path: str | Path,
    asset_catalog_path: str | Path,
) -> tuple[TrainingCampaignRun, TrainingCampaignSuite]:
    """Revalidate mutable runtime inputs against a frozen campaign plan."""

    manifest_path = Path(campaign_manifest_path).expanduser().resolve()
    run = load_training_campaign_run(manifest_path)
    suite = _load_campaign_snapshot(manifest_path.parent)
    if run.suite_id != suite.suite_id or run.suite_sha256 != canonical_sha256(suite):
        raise TrainingCampaignError("campaign suite snapshot does not match its run manifest")
    project = Path(project_path).expanduser().resolve()
    catalog = Path(asset_catalog_path).expanduser().resolve()
    _validate_suite_sources(suite, project_path=project, asset_catalog_path=catalog)
    if (
        run.project_name != suite.project_name
        or run.project_descriptor_sha256 != suite.project_descriptor_sha256
        or run.asset_catalog_sha256 != suite.asset_catalog_sha256
    ):
        raise TrainingCampaignError("campaign run source identity does not match its suite")
    return run, suite


def create_training_campaign_plan(
    *,
    suite_path: str | Path,
    campaign_directory: str | Path,
    campaign_id: str,
    project_path: str | Path,
    asset_catalog_path: str | Path,
    planner_model: str,
    evaluator_model: str,
    planner_provider: Literal["ollama", "local", "external", "human"] = "ollama",
    reference_suite_sha256: str | None = None,
    max_cases: int | None = None,
    case_ids: list[str] | None = None,
    max_attempts_per_case: int = 2,
) -> tuple[Path, TrainingCampaignRun]:
    """Freeze a source suite and create a non-executing campaign plan."""

    suite = load_training_campaign_suite(suite_path)
    project = Path(project_path).expanduser().resolve()
    catalog = Path(asset_catalog_path).expanduser().resolve()
    root = Path(campaign_directory).expanduser().resolve()
    _validate_suite_sources(suite, project_path=project, asset_catalog_path=catalog)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise TrainingCampaignError(f"campaign directory is not empty: {root}")
    if max_cases is not None and case_ids:
        raise TrainingCampaignError("max_cases and case_ids cannot be used together")
    if case_ids:
        if len(case_ids) != len(set(case_ids)):
            raise TrainingCampaignError("case_ids must be unique")
        case_map = _campaign_case_map(suite)
        missing = [case_id for case_id in case_ids if case_id not in case_map]
        if missing:
            raise TrainingCampaignError(
                "case_ids are missing from the suite: " + ", ".join(missing)
            )
        selected_cases = [case_map[case_id] for case_id in case_ids]
    elif max_cases is None:
        selected_cases = suite.cases
    else:
        if not 1 <= max_cases <= len(suite.cases):
            raise TrainingCampaignError(
                f"max_cases must be between 1 and {len(suite.cases)}"
            )
        selected_cases = suite.cases[:max_cases]

    try:
        run = TrainingCampaignRun(
            campaign_id=campaign_id,
            suite_id=suite.suite_id,
            suite_sha256=canonical_sha256(suite),
            project_name=suite.project_name,
            project_descriptor_sha256=suite.project_descriptor_sha256,
            asset_catalog_sha256=suite.asset_catalog_sha256,
            reference_suite_sha256=reference_suite_sha256,
            planner={"provider": planner_provider, "model": planner_model},
            evaluator={"provider": "ollama", "model": evaluator_model},
            status="planned",
            selected_case_ids=[case.case_id for case in selected_cases],
            max_attempts_per_case=max_attempts_per_case,
        )
    except ValidationError as exc:
        raise TrainingCampaignError(f"invalid campaign plan identity: {exc}") from exc
    root.mkdir(parents=True, exist_ok=True)
    snapshot_path = root / SUITE_SNAPSHOT_FILENAME
    _write_json_atomic(snapshot_path, suite.model_dump(mode="json"))
    manifest_path = root / CAMPAIGN_MANIFEST_FILENAME
    _write_json_atomic(manifest_path, run.model_dump(mode="json"))
    return manifest_path, run


def _updated_run(run: TrainingCampaignRun, **updates: object) -> TrainingCampaignRun:
    value = run.model_dump(mode="json")
    value.update(updates)
    return TrainingCampaignRun.model_validate(value)


def _workflow_id(campaign_id: str, case_id: str) -> str:
    value = f"{campaign_id}_{case_id}"
    if len(value) <= 64:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{campaign_id[:42]}_{digest}"[:64]


def _workflow_artifact_path(
    *,
    manifest: RenderWorkflowManifest,
    workflow_root: Path,
    role: str,
) -> Path | None:
    matches = [artifact for artifact in manifest.output_artifacts if artifact.role == role]
    if len(matches) != 1 or matches[0].sha256 is None:
        return None
    path = (workflow_root / matches[0].path).resolve()
    try:
        path.relative_to(workflow_root.resolve())
    except ValueError:
        return None
    if not path.is_file() or _sha256_file(path) != matches[0].sha256:
        return None
    return path


def _effective_render_spec(
    manifest: RenderWorkflowManifest,
    workflow_root: Path,
) -> RenderSpec | None:
    roles = [artifact.role for artifact in manifest.output_artifacts]
    candidates: list[str] = []
    if "final_render_spec" in roles:
        candidates.append("final_render_spec")
    iteration_roles = sorted(
        (
            role
            for role in roles
            if role.startswith("iteration_") and role.endswith("_render_spec")
        ),
        reverse=True,
    )
    candidates.extend(iteration_roles)
    if "initial_render_spec" in roles:
        candidates.append("initial_render_spec")
    for role in candidates:
        path = _workflow_artifact_path(
            manifest=manifest,
            workflow_root=workflow_root,
            role=role,
        )
        if path is None:
            continue
        try:
            return RenderSpec.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError):
            continue
    return None


def _analyze_attempt(
    *,
    case: TrainingCampaignCase,
    campaign_root: Path,
    workflow_directory: Path,
    workflow_id: str,
    attempt_number: int,
    started_at: datetime,
    started_monotonic: float | None,
    runner_error: str | None = None,
) -> TrainingCampaignAttempt:
    finished_at = datetime.now(UTC)
    duration_seconds = (
        max(0.0, time.perf_counter() - started_monotonic)
        if started_monotonic is not None
        else max(0.0, (finished_at - started_at).total_seconds())
    )
    manifest_path = workflow_directory / "workflow_manifest.json"
    manifest: RenderWorkflowManifest | None = None
    manifest_error: str | None = None
    if manifest_path.is_file():
        try:
            manifest = RenderWorkflowManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError) as exc:
            manifest_error = f"invalid workflow manifest: {_bounded_error(exc)}"

    if manifest is None:
        error = runner_error or manifest_error or "workflow manifest was not produced"
        return TrainingCampaignAttempt(
            attempt=attempt_number,
            workflow_id=workflow_id,
            workflow_directory=_portable_relative(workflow_directory, campaign_root),
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
            outcome="failed",
            workflow_manifest_path=(
                _portable_relative(manifest_path, campaign_root)
                if manifest_path.is_file()
                else None
            ),
            workflow_manifest_sha256=(
                _sha256_file(manifest_path) if manifest_path.is_file() else None
            ),
            expectation_passed=False,
            expectation_failures=["workflow did not produce a valid terminal manifest"],
            training_eligible=False,
            error=_bounded_error(error),
        )

    if manifest.status == "running":
        outcome = "interrupted"
    else:
        outcome = manifest.status
    visual_verdict = (
        manifest.iterations[-1].evaluation_verdict if manifest.iterations else None
    )
    spec = _effective_render_spec(manifest, workflow_directory)
    observed_scene_assets: list[str] = []
    observed_material_assets: list[str] = []
    observed_object_count = 0
    material_assignment_count = 0
    failures: list[str] = []
    if spec is None:
        failures.append("no hash-verified RenderSpec evidence was available")
    else:
        observed_object_count = len(spec.objects)
        observed_scene_assets = sorted({item.asset.asset_id for item in spec.objects})
        observed_material_assets = sorted(
            {
                assignment.material.asset_id
                for item in spec.objects
                for assignment in item.materials
            }
        )
        material_assignment_count = sum(len(item.materials) for item in spec.objects)
        missing_scene = sorted(
            set(case.expectation.required_scene_asset_ids) - set(observed_scene_assets)
        )
        missing_materials = sorted(
            set(case.expectation.required_material_asset_ids)
            - set(observed_material_assets)
        )
        forbidden = sorted(
            set(case.expectation.forbidden_asset_ids)
            & (set(observed_scene_assets) | set(observed_material_assets))
        )
        if missing_scene:
            failures.append("missing required scene assets: " + ", ".join(missing_scene))
        if missing_materials:
            failures.append(
                "missing required material assets: " + ", ".join(missing_materials)
            )
        if forbidden:
            failures.append("observed forbidden assets: " + ", ".join(forbidden))
        if not (
            case.expectation.minimum_object_count
            <= observed_object_count
            <= case.expectation.maximum_object_count
        ):
            failures.append(
                "object count outside expected range: "
                f"{observed_object_count} not in "
                f"[{case.expectation.minimum_object_count}, "
                f"{case.expectation.maximum_object_count}]"
            )
        if material_assignment_count < case.expectation.minimum_material_assignment_count:
            failures.append(
                "material assignment count below minimum: "
                f"{material_assignment_count} < "
                f"{case.expectation.minimum_material_assignment_count}"
            )
        if material_assignment_count > case.expectation.maximum_material_assignment_count:
            failures.append(
                "material assignment count above maximum: "
                f"{material_assignment_count} > "
                f"{case.expectation.maximum_material_assignment_count}"
            )
    if case.expectation.require_visual_pass and visual_verdict != "pass":
        failures.append(f"visual verdict must be pass; observed {visual_verdict or 'none'}")

    error_parts = [value for value in (runner_error, manifest_error) if value]
    error_parts.extend(manifest.errors)
    error = " | ".join(error_parts)[:4000] or None
    return TrainingCampaignAttempt(
        attempt=attempt_number,
        workflow_id=workflow_id,
        workflow_directory=_portable_relative(workflow_directory, campaign_root),
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        outcome=outcome,
        stop_reason=(
            manifest.stop_reason
            if manifest.status != "running"
            else "nonterminal workflow manifest"
        ),
        workflow_manifest_path=_portable_relative(manifest_path, campaign_root),
        workflow_manifest_sha256=_sha256_file(manifest_path),
        visual_verdict=visual_verdict,
        observed_scene_asset_ids=observed_scene_assets,
        observed_material_asset_ids=observed_material_assets,
        observed_object_count=observed_object_count,
        expectation_passed=not failures,
        expectation_failures=failures,
        training_eligible=(
            outcome == "succeeded" and not failures and visual_verdict == "pass"
        ),
        error=error,
    )


def _append_attempt_result(
    run: TrainingCampaignRun,
    *,
    case: TrainingCampaignCase,
    attempt: TrainingCampaignAttempt,
) -> TrainingCampaignRun:
    results = list(run.case_results)
    existing_index = next(
        (index for index, result in enumerate(results) if result.case_id == case.case_id),
        None,
    )
    attempts = (
        [*results[existing_index].attempts, attempt]
        if existing_index is not None
        else [attempt]
    )
    result = TrainingCampaignCaseResult(
        case_id=case.case_id,
        category=case.category,
        status=attempt.outcome,
        attempts=attempts,
        training_eligible=attempt.training_eligible,
    )
    if existing_index is None:
        results.append(result)
    else:
        results[existing_index] = result
    return _updated_run(
        run,
        case_results=[item.model_dump(mode="json") for item in results],
        active_case_id=None,
        active_attempt=None,
        active_started_at=None,
    )


def _campaign_case_map(suite: TrainingCampaignSuite) -> dict[str, TrainingCampaignCase]:
    return {case.case_id: case for case in suite.cases}


def _load_campaign_snapshot(campaign_root: Path) -> TrainingCampaignSuite:
    return load_training_campaign_suite(campaign_root / SUITE_SNAPSHOT_FILENAME)


def run_training_campaign(
    *,
    campaign_manifest_path: str | Path,
    case_runner: CampaignCaseRunner,
    retry_failed: bool = False,
    progress: CampaignProgress | None = None,
) -> TrainingCampaignRun:
    """Execute or resume selected cases without overwriting prior attempts."""

    manifest_path = Path(campaign_manifest_path).expanduser().resolve()
    campaign_root = manifest_path.parent
    run = load_training_campaign_run(manifest_path)
    suite = _load_campaign_snapshot(campaign_root)
    if run.suite_id != suite.suite_id or run.suite_sha256 != canonical_sha256(suite):
        raise TrainingCampaignError("campaign suite snapshot does not match its run manifest")
    case_map = _campaign_case_map(suite)
    if any(case_id not in case_map for case_id in run.selected_case_ids):
        raise TrainingCampaignError("campaign selected a case missing from its suite snapshot")
    if run.status == "completed" and not retry_failed:
        return run
    if run.status == "failed":
        raise TrainingCampaignError("failed campaign state requires operator inspection")

    if run.started_at is None:
        started_at = datetime.now(UTC)
    else:
        started_at = run.started_at
    run = _updated_run(
        run,
        status="running",
        started_at=started_at,
        finished_at=None,
    )
    _write_json_atomic(manifest_path, run.model_dump(mode="json"))

    if run.active_case_id is not None:
        case = case_map[run.active_case_id]
        workflow_directory = (
            campaign_root
            / "workflows"
            / case.case_id
            / f"attempt-{run.active_attempt:03d}"
        )
        recovered = _analyze_attempt(
            case=case,
            campaign_root=campaign_root,
            workflow_directory=workflow_directory,
            workflow_id=_workflow_id(run.campaign_id, case.case_id),
            attempt_number=run.active_attempt,
            started_at=run.active_started_at,
            started_monotonic=None,
            runner_error=None,
        )
        run = _append_attempt_result(run, case=case, attempt=recovered)
        _write_json_atomic(manifest_path, run.model_dump(mode="json"))

    for case_id in run.selected_case_ids:
        case = case_map[case_id]
        current = next(
            (result for result in run.case_results if result.case_id == case_id),
            None,
        )
        if current is not None:
            if current.training_eligible:
                continue
            if not retry_failed or len(current.attempts) >= run.max_attempts_per_case:
                continue
        attempt_number = len(current.attempts) + 1 if current is not None else 1
        workflow_directory = (
            campaign_root / "workflows" / case.case_id / f"attempt-{attempt_number:03d}"
        )
        if workflow_directory.exists() and any(workflow_directory.iterdir()):
            raise TrainingCampaignError(
                f"new campaign attempt directory is not empty: {workflow_directory}"
            )
        workflow_directory.mkdir(parents=True, exist_ok=True)
        active_started_at = datetime.now(UTC)
        started_monotonic = time.perf_counter()
        run = _updated_run(
            run,
            active_case_id=case.case_id,
            active_attempt=attempt_number,
            active_started_at=active_started_at,
        )
        _write_json_atomic(manifest_path, run.model_dump(mode="json"))
        if progress:
            progress(
                f"START {case.case_id} attempt={attempt_number} "
                f"category={case.category}"
            )
        runner_error: str | None = None
        try:
            case_runner(
                case,
                workflow_directory,
                _workflow_id(run.campaign_id, case.case_id),
            )
        except KeyboardInterrupt:
            attempt = _analyze_attempt(
                case=case,
                campaign_root=campaign_root,
                workflow_directory=workflow_directory,
                workflow_id=_workflow_id(run.campaign_id, case.case_id),
                attempt_number=attempt_number,
                started_at=active_started_at,
                started_monotonic=started_monotonic,
                runner_error="campaign interrupted by operator",
            )
            run = _append_attempt_result(run, case=case, attempt=attempt)
            run = _updated_run(run, status="cancelled", finished_at=datetime.now(UTC))
            _write_json_atomic(manifest_path, run.model_dump(mode="json"))
            raise
        except Exception as exc:  # isolate one failed case from the remaining budget
            runner_error = _bounded_error(exc)
        attempt = _analyze_attempt(
            case=case,
            campaign_root=campaign_root,
            workflow_directory=workflow_directory,
            workflow_id=_workflow_id(run.campaign_id, case.case_id),
            attempt_number=attempt_number,
            started_at=active_started_at,
            started_monotonic=started_monotonic,
            runner_error=runner_error,
        )
        run = _append_attempt_result(run, case=case, attempt=attempt)
        _write_json_atomic(manifest_path, run.model_dump(mode="json"))
        if progress:
            progress(
                f"END {case.case_id} outcome={attempt.outcome} "
                f"expectation={'pass' if attempt.expectation_passed else 'fail'} "
                f"eligible={str(attempt.training_eligible).lower()}"
            )

    result_ids = {result.case_id for result in run.case_results}
    status = (
        "completed"
        if all(case_id in result_ids for case_id in run.selected_case_ids)
        else "cancelled"
    )
    run = _updated_run(run, status=status, finished_at=datetime.now(UTC))
    _write_json_atomic(manifest_path, run.model_dump(mode="json"))
    return run


def campaign_status_summary(run: TrainingCampaignRun) -> dict[str, object]:
    latest_attempts = [result.attempts[-1] for result in run.case_results]
    return {
        "campaign_id": run.campaign_id,
        "suite_id": run.suite_id,
        "status": run.status,
        "selected_case_count": len(run.selected_case_ids),
        "completed_case_count": len(run.case_results),
        "workflow_succeeded_count": sum(
            attempt.outcome == "succeeded" for attempt in latest_attempts
        ),
        "expectation_passed_count": sum(
            attempt.expectation_passed for attempt in latest_attempts
        ),
        "training_eligible_count": sum(
            attempt.training_eligible for attempt in latest_attempts
        ),
        "attempt_count": sum(len(result.attempts) for result in run.case_results),
        "active_case_id": run.active_case_id,
        "errors": run.errors,
    }


def _primary_failure_reason(attempt: TrainingCampaignAttempt) -> str:
    if attempt.outcome == "failed":
        detail = (attempt.error or "").casefold()
        if "no meaningful visible pixels" in detail:
            return "black_or_blank_preview"
        if "correction model proposed an invalid patch" in detail:
            return "invalid_correction_patch"
        if "global exposure correction is unsupported" in detail:
            return "unsupported_exposure_correction"
        if "empty message.content" in detail:
            return "ollama_empty_content"
        if "has no dimensions_cm evidence" in detail:
            return "dimensionless_asset_selected"
        return "workflow_failed"
    if attempt.outcome == "stopped":
        if attempt.stop_reason in {
            "correction_unresolved",
            "max_iterations_reached",
            "reference_quality_rejected",
        }:
            return attempt.stop_reason
        return "workflow_stopped"
    if attempt.outcome == "interrupted":
        return "workflow_interrupted"
    nonvisual_failures = [
        failure
        for failure in attempt.expectation_failures
        if not failure.startswith("visual verdict must be pass")
    ]
    if nonvisual_failures:
        return "expectation_failed"
    if attempt.visual_verdict != "pass":
        return "visual_not_passed"
    return "none"


def build_training_baseline_report(
    run: TrainingCampaignRun,
    *,
    report_id: str,
) -> TrainingBaselineReport:
    """Freeze aggregate base-model outcomes from one completed campaign."""

    if run.status != "completed":
        raise TrainingCampaignError("baseline requires a completed campaign")
    outcomes = [
        TrainingBaselineCaseOutcome(
            case_id=result.case_id,
            category=result.category,
            status=result.status,
            attempt_count=len(result.attempts),
            expectation_passed=result.attempts[-1].expectation_passed,
            visual_passed=result.attempts[-1].visual_verdict == "pass",
            training_eligible=result.training_eligible,
            primary_failure_reason=_primary_failure_reason(result.attempts[-1]),
        )
        for result in run.case_results
    ]
    categories = sorted({outcome.category for outcome in outcomes})
    category_metrics = []
    for category in categories:
        matching = [outcome for outcome in outcomes if outcome.category == category]
        category_metrics.append(
            TrainingBaselineCategoryMetrics(
                category=category,
                case_count=len(matching),
                workflow_succeeded_count=sum(
                    outcome.status == "succeeded" for outcome in matching
                ),
                expectation_passed_count=sum(
                    outcome.expectation_passed for outcome in matching
                ),
                training_eligible_count=sum(
                    outcome.training_eligible for outcome in matching
                ),
            )
        )
    total = len(outcomes)
    succeeded = sum(outcome.status == "succeeded" for outcome in outcomes)
    expectation_passed = sum(outcome.expectation_passed for outcome in outcomes)
    eligible = sum(outcome.training_eligible for outcome in outcomes)
    failure_counts = dict(
        Counter(
            outcome.primary_failure_reason
            for outcome in outcomes
            if outcome.primary_failure_reason != "none"
        )
    )
    payload = {
        "schema_version": "0.1",
        "report_id": report_id,
        "frozen": True,
        "campaign_id": run.campaign_id,
        "suite_id": run.suite_id,
        "campaign_run_sha256": canonical_sha256(run),
        "planner": run.planner.model_dump(mode="json"),
        "evaluator": run.evaluator.model_dump(mode="json"),
        "case_outcomes": [outcome.model_dump(mode="json") for outcome in outcomes],
        "category_metrics": [
            metric.model_dump(mode="json") for metric in category_metrics
        ],
        "total_case_count": total,
        "workflow_succeeded_count": succeeded,
        "expectation_passed_count": expectation_passed,
        "training_eligible_count": eligible,
        "workflow_success_rate": succeeded / total,
        "expectation_pass_rate": expectation_passed / total,
        "training_eligibility_rate": eligible / total,
        "failure_reason_counts": failure_counts,
    }
    payload["report_sha256"] = canonical_sha256(payload)
    return TrainingBaselineReport.model_validate(payload)
