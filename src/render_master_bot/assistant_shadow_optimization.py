"""Deterministic Cast Shadow optimization decisions from eight Unreal traces."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.contracts import (
    AssistantShadowOptimizationReport,
    PerformanceActorAction,
    PerformancePropertyChange,
    ShadowOptimizationDecisionThresholds,
    UnrealShadowOptimizationCaptureSet,
    build_shadow_optimization_aggregates,
    shadow_optimization_qualifying_aggregates,
)


class ShadowOptimizationAnalysisError(RuntimeError):
    """Raised when the bounded shadow experiment cannot be reconstructed."""


def load_shadow_optimization_capture_set(
    path: str | Path,
) -> UnrealShadowOptimizationCaptureSet:
    try:
        return UnrealShadowOptimizationCaptureSet.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise ShadowOptimizationAnalysisError(
            f"invalid shadow optimization capture set: {exc}"
        ) from exc


def analyze_shadow_optimization_capture_set(
    capture_set: UnrealShadowOptimizationCaptureSet,
    *,
    capture_set_file_sha256: str,
    report_id: str | None = None,
) -> AssistantShadowOptimizationReport:
    """Recompute trace aggregates and prepare at most one approval-gated action."""

    thresholds = ShadowOptimizationDecisionThresholds()
    aggregates = build_shadow_optimization_aggregates(capture_set)
    qualifying = shadow_optimization_qualifying_aggregates(aggregates, thresholds)
    if not aggregates:
        status = "unresolved"
        decision = "unresolved"
        primary_id = None
        action = None
        reasons = [
            "No queue-local GPU scope was common to all eight Top-64 trace sets."
        ]
        summary = (
            f"The Cast Shadow experiment for {capture_set.target.actor_name} was "
            "restored safely, but no common eight-trace GPU scope was available "
            "for a controlled decision. No Editor change is proposed."
        )
    elif qualifying:
        primary = qualifying[0]
        status = "proposed"
        decision = "propose_apply"
        primary_id = primary.aggregate_id
        reasons = []
        after = capture_set.target.performance.model_copy(
            update={"cast_shadow": False}
        )
        action = PerformanceActorAction(
            target=capture_set.target,
            before=capture_set.target.performance,
            after=after,
            changes=[
                PerformancePropertyChange(
                    property="cast_shadow",
                    before=True,
                    after=False,
                )
            ],
            rationale=(
                f"Temporary PIE/SIE Cast Shadow disabling reduced {primary.scope_name} "
                f"by a three-trial median {primary.median_baseline_minus_candidate_ms_per_second:.3f} "
                f"ms/s, exceeded A/A noise by {primary.signal_excess_over_control_noise_ms_per_second:.3f} "
                f"ms/s, and produced {primary.relative_reduction_percent:.2f}% relative reduction."
            ),
        )
        summary = (
            f"The temporary Cast Shadow candidate for {capture_set.target.actor_name} "
            f"passed deterministic profiling gates on {primary.scope_name}: median "
            f"GPU-scope reduction {primary.median_baseline_minus_candidate_ms_per_second:.3f} "
            f"ms/s, A/A noise {primary.control_noise_abs_ms_per_second:.3f} ms/s, "
            f"signal excess {primary.signal_excess_over_control_noise_ms_per_second:.3f} "
            f"ms/s, and {primary.decreased_vote_count}/3 decreased votes. Runtime "
            "state was restored. The formal level remains unchanged until explicit approval."
        )
    else:
        status = "rejected"
        decision = "reject"
        primary_id = None
        action = None
        reasons = [
            "No common GPU scope met every fixed stability, noise, effect-size, and dispersion gate."
        ]
        summary = (
            f"The temporary Cast Shadow candidate for {capture_set.target.actor_name} "
            "did not produce a repeatable GPU improvement above the fixed decision "
            "thresholds. Runtime state was restored and no Editor change is proposed."
        )

    return AssistantShadowOptimizationReport(
        report_id=report_id or f"{capture_set.experiment_id}_report",
        status=status,
        decision=decision,
        capture_set=capture_set,
        capture_set_sha256=hashlib.sha256(
            capture_set.model_dump_json().encode("utf-8")
        ).hexdigest(),
        capture_set_file_sha256=capture_set_file_sha256,
        thresholds=thresholds,
        aggregates=aggregates,
        primary_aggregate_id=primary_id,
        summary=summary,
        proposed_action=action,
        rejection_reasons=reasons,
        approval_required=status == "proposed",
    )


def analyze_shadow_optimization_file(
    path: str | Path,
    *,
    report_id: str | None = None,
) -> AssistantShadowOptimizationReport:
    capture_path = Path(path)
    try:
        file_hash = hashlib.sha256(capture_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ShadowOptimizationAnalysisError(
            f"could not hash shadow optimization capture set: {exc}"
        ) from exc
    return analyze_shadow_optimization_capture_set(
        load_shadow_optimization_capture_set(capture_path),
        capture_set_file_sha256=file_hash,
        report_id=report_id,
    )
