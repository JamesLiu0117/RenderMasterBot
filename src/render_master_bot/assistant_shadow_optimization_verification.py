"""Deterministic whole-level verification after one approved shadow optimization."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from render_master_bot.contracts import (
    AssistantLevelProfilingReport,
    AssistantShadowOptimizationReport,
    AssistantShadowOptimizationVerificationReport,
    ShadowOptimizationVerificationThresholds,
    build_shadow_optimization_verification_decision,
    build_shadow_optimization_verification_metrics,
    shadow_optimization_verification_sources_are_distinct,
    shadow_optimization_verification_target_matches,
    shadow_optimization_verification_workload_matches,
)
from render_master_bot.models import StrictModel


class ShadowOptimizationVerificationError(RuntimeError):
    """Raised when post-apply evidence cannot be loaded or compared safely."""


ReportT = TypeVar("ReportT", bound=StrictModel)


def _load_report(path: Path, report_type: type[ReportT], label: str) -> ReportT:
    try:
        return report_type.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValidationError) as exc:
        raise ShadowOptimizationVerificationError(
            f"invalid {label}: {exc}"
        ) from exc


def _file_sha256(path: Path, label: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ShadowOptimizationVerificationError(
            f"could not hash {label}: {exc}"
        ) from exc


def analyze_shadow_optimization_verification(
    *,
    shadow_report: AssistantShadowOptimizationReport,
    shadow_report_file_sha256: str,
    baseline_report: AssistantLevelProfilingReport,
    baseline_report_file_sha256: str,
    candidate_report: AssistantLevelProfilingReport,
    candidate_report_file_sha256: str,
    report_id: str | None = None,
) -> AssistantShadowOptimizationVerificationReport:
    """Recompute and classify one fresh whole-level post-apply profiling run."""

    thresholds = ShadowOptimizationVerificationThresholds()
    workload_matches = shadow_optimization_verification_workload_matches(
        shadow_report, baseline_report, candidate_report
    )
    target_matches = shadow_optimization_verification_target_matches(
        shadow_report, baseline_report, candidate_report
    )
    sources_are_distinct = shadow_optimization_verification_sources_are_distinct(
        baseline_report,
        candidate_report,
        baseline_report_file_sha256,
        candidate_report_file_sha256,
    )
    metrics = build_shadow_optimization_verification_metrics(
        shadow_report, baseline_report, candidate_report
    )
    decision, reasons = build_shadow_optimization_verification_decision(
        metrics=metrics,
        thresholds=thresholds,
        workload_matches=workload_matches,
        target_transition_matches=target_matches,
        sources_are_distinct=sources_are_distinct,
    )
    actor_name = shadow_report.capture_set.target.actor_name
    if decision == "retain":
        summary = (
            f"The fresh whole-level run for {actor_name} stayed inside every fixed "
            "frame, GPU, budget-miss, and available target-scope regression guard. "
            "Retain is recommended; the level remains unsaved and the original "
            "controlled A/B experiment remains the candidate-specific evidence."
        )
    elif decision == "rollback":
        summary = (
            f"The fresh whole-level run for {actor_name} exceeded at least one fixed "
            "regression guard. Rollback is recommended, but requires explicit "
            "operator approval and exact formal-state revalidation."
        )
    else:
        summary = (
            f"The post-apply verification for {actor_name} could not establish a "
            "fresh identical-workload comparison around the exact approved target. "
            "No retain claim is made; rollback remains an operator decision."
        )
    return AssistantShadowOptimizationVerificationReport(
        report_id=report_id
        or f"{shadow_report.capture_set.experiment_id}_verification",
        status="unresolved" if decision == "unresolved" else "complete",
        decision=decision,
        shadow_optimization_report=shadow_report,
        shadow_optimization_report_sha256=hashlib.sha256(
            shadow_report.model_dump_json().encode("utf-8")
        ).hexdigest(),
        shadow_optimization_report_file_sha256=shadow_report_file_sha256,
        baseline_level_profiling_report=baseline_report,
        baseline_level_profiling_report_sha256=hashlib.sha256(
            baseline_report.model_dump_json().encode("utf-8")
        ).hexdigest(),
        baseline_level_profiling_report_file_sha256=baseline_report_file_sha256,
        candidate_level_profiling_report=candidate_report,
        candidate_level_profiling_report_sha256=hashlib.sha256(
            candidate_report.model_dump_json().encode("utf-8")
        ).hexdigest(),
        candidate_level_profiling_report_file_sha256=candidate_report_file_sha256,
        thresholds=thresholds,
        workload_matches=workload_matches,
        target_transition_matches=target_matches,
        sources_are_distinct=sources_are_distinct,
        metrics=metrics,
        summary=summary,
        decision_reasons=reasons,
    )


def analyze_shadow_optimization_verification_files(
    *,
    shadow_report_path: str | Path,
    baseline_report_path: str | Path,
    candidate_report_path: str | Path,
    report_id: str | None = None,
) -> AssistantShadowOptimizationVerificationReport:
    shadow_path = Path(shadow_report_path)
    baseline_path = Path(baseline_report_path)
    candidate_path = Path(candidate_report_path)
    return analyze_shadow_optimization_verification(
        shadow_report=_load_report(
            shadow_path,
            AssistantShadowOptimizationReport,
            "shadow optimization report",
        ),
        shadow_report_file_sha256=_file_sha256(
            shadow_path, "shadow optimization report"
        ),
        baseline_report=_load_report(
            baseline_path,
            AssistantLevelProfilingReport,
            "baseline level profiling report",
        ),
        baseline_report_file_sha256=_file_sha256(
            baseline_path, "baseline level profiling report"
        ),
        candidate_report=_load_report(
            candidate_path,
            AssistantLevelProfilingReport,
            "candidate level profiling report",
        ),
        candidate_report_file_sha256=_file_sha256(
            candidate_path, "candidate level profiling report"
        ),
        report_id=report_id,
    )
