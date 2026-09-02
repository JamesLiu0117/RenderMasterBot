"""Deterministic synthesis for profiling-first whole-level Unreal audits."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from render_master_bot.contracts import (
    AssistantActorGpuImpactStudyReport,
    AssistantInsightsGpuReport,
    AssistantLevelAuditReport,
    AssistantLevelProfilingReport,
    AssistantRuntimePerformanceReport,
    LevelProfilingActorAttempt,
    LevelProfilingActorFinding,
    UnrealLevelProfilingRun,
)


class LevelProfilingSynthesisError(RuntimeError):
    """Raised when a composite profiling run cannot be safely reconstructed."""


def _load_report(path: str | Path, model: type[Any], label: str) -> Any:
    try:
        return model.model_validate_json(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValidationError) as exc:
        raise LevelProfilingSynthesisError(f"invalid {label}: {exc}") from exc


def load_level_audit_report(path: str | Path) -> AssistantLevelAuditReport:
    return _load_report(path, AssistantLevelAuditReport, "whole-level audit report")


def load_runtime_performance_report(
    path: str | Path,
) -> AssistantRuntimePerformanceReport:
    return _load_report(path, AssistantRuntimePerformanceReport, "runtime report")


def load_gpu_scope_report(path: str | Path) -> AssistantInsightsGpuReport:
    return _load_report(path, AssistantInsightsGpuReport, "GPU scope report")


def load_actor_study_report(
    path: str | Path,
) -> AssistantActorGpuImpactStudyReport:
    return _load_report(path, AssistantActorGpuImpactStudyReport, "Actor GPU study report")


def load_level_profiling_skips(path: str | Path | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LevelProfilingSynthesisError(
            f"invalid Actor study skip manifest: {exc}"
        ) from exc
    if not isinstance(value, dict) or set(value) != {"skips"}:
        raise LevelProfilingSynthesisError(
            "Actor study skip manifest must contain only a skips list"
        )
    rows = value["skips"]
    if not isinstance(rows, list) or len(rows) > 2:
        raise LevelProfilingSynthesisError("Actor study skip manifest is not bounded")
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"assessment_id", "reason"}:
            raise LevelProfilingSynthesisError("Actor study skip entry is invalid")
        assessment_id = row["assessment_id"]
        reason = row["reason"]
        if (
            not isinstance(assessment_id, str)
            or not assessment_id
            or len(assessment_id) > 120
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason.strip()) > 240
        ):
            raise LevelProfilingSynthesisError("Actor study skip fields are invalid")
        if assessment_id in result:
            raise LevelProfilingSynthesisError("Actor study skip IDs must be unique")
        result[assessment_id] = reason.strip()
    return result


def build_level_profiling_run(
    *,
    profiling_run_id: str,
    created_at_utc: datetime,
    request: str,
    level_audit_report: AssistantLevelAuditReport,
    runtime_performance_report: AssistantRuntimePerformanceReport,
    gpu_scope_report: AssistantInsightsGpuReport,
    actor_study_reports: list[AssistantActorGpuImpactStudyReport],
    skipped_actor_studies: dict[str, str],
) -> UnrealLevelProfilingRun:
    """Bind the fixed baseline, trace, and deterministic Top-2 Actor attempts."""

    candidates = [
        assessment
        for assessment in level_audit_report.assessments
        if assessment.recommended_next_test == "repeatable_actor_gpu_study"
    ][:2]
    actors_by_path = {
        actor.actor_path: actor for actor in level_audit_report.context.actors
    }
    reports_by_path: dict[str, AssistantActorGpuImpactStudyReport] = {}
    for report in actor_study_reports:
        actor_path = report.capture_set.target.editor_actor_path
        if actor_path in reports_by_path:
            raise LevelProfilingSynthesisError(
                f"duplicate Actor GPU study report for {actor_path}"
            )
        reports_by_path[actor_path] = report

    expected_ids = {candidate.assessment_id for candidate in candidates}
    unexpected_skips = set(skipped_actor_studies) - expected_ids
    if unexpected_skips:
        raise LevelProfilingSynthesisError(
            "skip manifest cites non-selected assessments: "
            + ", ".join(sorted(unexpected_skips))
        )
    attempts: list[LevelProfilingActorAttempt] = []
    for candidate in candidates:
        actor = actors_by_path[candidate.actor_path]
        report = reports_by_path.pop(candidate.actor_path, None)
        skip_reason = skipped_actor_studies.get(candidate.assessment_id)
        if report is not None and skip_reason is not None:
            raise LevelProfilingSynthesisError(
                f"candidate {candidate.assessment_id} is both measured and skipped"
            )
        if report is not None:
            attempts.append(
                LevelProfilingActorAttempt(
                    assessment_id=candidate.assessment_id,
                    actor_path=candidate.actor_path,
                    actor_name=actor.actor_name,
                    outcome="measured",
                    study_report=report,
                )
            )
        elif skip_reason is not None:
            attempts.append(
                LevelProfilingActorAttempt(
                    assessment_id=candidate.assessment_id,
                    actor_path=candidate.actor_path,
                    actor_name=actor.actor_name,
                    outcome="skipped",
                    skip_reason=skip_reason,
                )
            )
        else:
            raise LevelProfilingSynthesisError(
                f"selected candidate {candidate.assessment_id} has no study outcome"
            )
    if reports_by_path:
        raise LevelProfilingSynthesisError(
            "Actor GPU study reports target non-selected Actors: "
            + ", ".join(sorted(reports_by_path))
        )

    try:
        return UnrealLevelProfilingRun(
            profiling_run_id=profiling_run_id,
            created_at_utc=created_at_utc,
            request=request.strip(),
            level_audit_report=level_audit_report,
            runtime_performance_report=runtime_performance_report,
            gpu_scope_report=gpu_scope_report,
            actor_attempts=attempts,
        )
    except ValidationError as exc:
        raise LevelProfilingSynthesisError(
            f"profiling evidence bundle is inconsistent: {exc}"
        ) from exc


def _actor_finding(
    run: UnrealLevelProfilingRun,
    attempt: LevelProfilingActorAttempt,
) -> LevelProfilingActorFinding:
    assessment = next(
        item
        for item in run.level_audit_report.assessments
        if item.assessment_id == attempt.assessment_id
    )
    if attempt.outcome == "skipped":
        return LevelProfilingActorFinding(
            assessment_id=attempt.assessment_id,
            actor_path=attempt.actor_path,
            actor_name=attempt.actor_name,
            static_risk_score=assessment.risk_score,
            static_risk_band=assessment.risk_band,
            outcome="skipped",
            conclusion=f"Study skipped safely: {attempt.skip_reason}",
        )
    assert attempt.study_report is not None
    report = attempt.study_report
    if report.status == "unresolved":
        return LevelProfilingActorFinding(
            assessment_id=attempt.assessment_id,
            actor_path=attempt.actor_path,
            actor_name=attempt.actor_name,
            static_risk_score=assessment.risk_score,
            static_risk_band=assessment.risk_band,
            outcome="unresolved",
            conclusion=(
                "Eight captures were preserved, but the constrained review remained "
                "unresolved: " + "; ".join(report.missing_capabilities)
            ),
        )

    primary = report.aggregates[0]
    direction = primary.dominant_direction_when_hidden.replace("_", " ")
    return LevelProfilingActorFinding(
        assessment_id=attempt.assessment_id,
        actor_path=attempt.actor_path,
        actor_name=attempt.actor_name,
        static_risk_score=assessment.risk_score,
        static_risk_band=assessment.risk_band,
        outcome=primary.classification,
        primary_aggregate_id=primary.aggregate_id,
        scope_name=primary.scope_name,
        median_delta_ms_per_second=(
            primary.median_baseline_minus_hidden_ms_per_second
        ),
        control_noise_ms_per_second=primary.control_noise_abs_ms_per_second,
        signal_excess_ms_per_second=(
            primary.signal_excess_over_control_noise_ms_per_second
        ),
        relative_reduction_percent=primary.relative_reduction_percent,
        conclusion=(
            f"Strongest common inclusive scope was {primary.scope_name}; hiding the "
            f"Actor produced a {direction} three-trial median change of "
            f"{primary.median_baseline_minus_hidden_ms_per_second:.3f} ms/s versus "
            f"{primary.control_noise_abs_ms_per_second:.3f} ms/s A/A noise."
        ),
    )


def analyze_level_profiling_run(
    run: UnrealLevelProfilingRun,
    *,
    report_id: str | None = None,
) -> AssistantLevelProfilingReport:
    """Produce a model-free, recomputable cross-stage profiling summary."""

    runtime_report = run.runtime_performance_report
    runtime = runtime_report.capture
    gpu = run.gpu_scope_report.capture
    primary_scope = gpu.scopes[0]
    findings = [_actor_finding(run, attempt) for attempt in run.actor_attempts]
    measured_count = sum(attempt.outcome == "measured" for attempt in run.actor_attempts)
    skipped_count = len(run.actor_attempts) - measured_count
    stable_count = sum(finding.outcome == "stable_candidate" for finding in findings)
    partial = (
        skipped_count > 0
        or runtime_report.status == "unresolved"
        or run.gpu_scope_report.status == "unresolved"
        or any(
            attempt.study_report is not None
            and attempt.study_report.status == "unresolved"
            for attempt in run.actor_attempts
        )
    )
    gpu_p95 = runtime.gpu.p95_ms if runtime.gpu.available else None
    summary = (
        f"Profiling-first audit captured {runtime.sample_count} runtime frames and "
        f"{gpu.captured_duration_seconds:.2f} seconds of GPU scopes at "
        f"{runtime.viewport_width_px}x{runtime.viewport_height_px}. Frame p95 was "
        f"{runtime.frame_time.p95_ms:.3f} ms with "
        f"{runtime.frame_budget_miss_fraction * 100.0:.1f}% over the "
        f"{runtime.target_frame_ms:.3f} ms budget. {measured_count} Top-N Actor "
        f"studies completed, {skipped_count} were skipped, and {stable_count} "
        "produced a signal above their A/A control noise."
    )
    limitations = [
        "PIE/SIE measurements include Unreal Editor overhead and do not prove packaged-build parity.",
        "GPU scope timings are inclusive and do not provide direct per-draw, material, shader, or asset attribution.",
        "Temporary Actor hiding estimates contribution but can change occlusion, shadows, and downstream GPU work.",
        "Images are auxiliary regression guards only; they are not used as evidence of rendering cost.",
        "No production level property was changed or saved by this profiling run.",
    ]
    return AssistantLevelProfilingReport(
        report_id=report_id or f"{run.profiling_run_id}_report",
        status="partial" if partial else "complete",
        run=run,
        run_sha256=hashlib.sha256(run.model_dump_json().encode("utf-8")).hexdigest(),
        level_audit_id=run.level_audit_report.audit_id,
        runtime_capture_id=runtime.capture_id,
        gpu_capture_id=gpu.capture_id,
        actor_study_ids=[
            attempt.study_report.capture_set.study_id
            for attempt in run.actor_attempts
            if attempt.study_report is not None
        ],
        summary=summary,
        primary_bottleneck=runtime_report.primary_bottleneck,
        frame_p95_ms=runtime.frame_time.p95_ms,
        frame_budget_miss_fraction=runtime.frame_budget_miss_fraction,
        gpu_p95_ms=gpu_p95,
        primary_gpu_scope_id=primary_scope.scope_id,
        primary_gpu_scope_name=primary_scope.name,
        primary_gpu_scope_ms_per_second=(
            primary_scope.total_inclusive_ms / gpu.captured_duration_seconds
        ),
        measured_actor_count=measured_count,
        skipped_actor_count=skipped_count,
        stable_actor_candidate_count=stable_count,
        actor_findings=findings,
        limitations=limitations,
    )
