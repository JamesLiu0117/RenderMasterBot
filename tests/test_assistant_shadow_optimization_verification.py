from __future__ import annotations

import hashlib
import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_level_profiling import (
    analyze_level_profiling_run,
    build_level_profiling_run,
)
from render_master_bot.assistant_shadow_optimization import (
    analyze_shadow_optimization_capture_set,
)
from render_master_bot.assistant_shadow_optimization_verification import (
    analyze_shadow_optimization_verification,
    analyze_shadow_optimization_verification_files,
)
from render_master_bot.contracts import (
    AssistantInsightsGpuReport,
    AssistantLevelAuditReport,
    AssistantLevelProfilingReport,
    AssistantRuntimePerformanceReport,
    AssistantShadowOptimizationReport,
    AssistantShadowOptimizationVerificationReport,
    ModelIdentity,
    UnrealInsightsGpuCapture,
    UnrealLevelAuditContext,
    UnrealRuntimePerformanceCapture,
    UnrealShadowOptimizationCaptureSet,
    build_level_audit_assessments,
    build_level_audit_statistics,
)
from tests.test_assistant_level_profiling import (
    HASH_A,
    _audit_report,
    _canonical_hash,
    _gpu_report,
    _runtime_report,
    _study_report,
)
from tests.test_assistant_shadow_optimization import capture_set_value


HASH_B = "b" * 64
HASH_C = "c" * 64
def _timing_summary(values: list[float]) -> dict:
    ordered = sorted(values)
    count = len(ordered)
    return {
        "available": True,
        "sample_count": count,
        "mean_ms": sum(ordered) / count,
        "p50_ms": ordered[max(0, math.ceil(0.50 * count) - 1)],
        "p95_ms": ordered[max(0, math.ceil(0.95 * count) - 1)],
        "max_ms": ordered[-1],
    }


def _shadow_report(
    baseline: AssistantLevelProfilingReport,
) -> AssistantShadowOptimizationReport:
    value = capture_set_value()
    stable = next(
        finding
        for finding in baseline.actor_findings
        if finding.outcome == "stable_candidate"
    )
    target = next(
        actor
        for actor in baseline.run.level_audit_report.context.actors
        if actor.actor_path == stable.actor_path
    )
    runtime = baseline.run.runtime_performance_report.capture
    value["target"] = target.model_dump(mode="json")
    captures = [
        *value["noise_control_baseline"],
        *[
            capture
            for trial in value["trials"]
            for capture in (trial["baseline"], trial["candidate"])
        ],
    ]
    for capture in captures:
        capture.update(
            project_name=runtime.project_name,
            world_path=runtime.world_path,
            capture_mode=runtime.capture_mode,
            viewport_width_px=runtime.viewport_width_px,
            viewport_height_px=runtime.viewport_height_px,
            gpu_name=runtime.gpu_name,
        )
    capture_set = UnrealShadowOptimizationCaptureSet.model_validate(value)
    return analyze_shadow_optimization_capture_set(
        capture_set,
        capture_set_file_sha256=HASH_A,
    )


def _baseline_report() -> AssistantLevelProfilingReport:
    audit = _audit_report()
    runtime = _runtime_report()
    gpu = _gpu_report(runtime)
    first = _study_report(audit, runtime, 0)
    second = audit.assessments[1]
    run = build_level_profiling_run(
        profiling_run_id="level_profile_before",
        created_at_utc=datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc),
        request="Profile the complete level before the shadow optimization.",
        level_audit_report=audit,
        runtime_performance_report=runtime,
        gpu_scope_report=gpu,
        actor_study_reports=[first],
        skipped_actor_studies={
            second.assessment_id: "No matching runtime Actor was available."
        },
    )
    return analyze_level_profiling_run(run, report_id="level_profile_before_report")


def _candidate_audit(
    baseline: AssistantLevelProfilingReport,
    *,
    target_changed: bool,
) -> AssistantLevelAuditReport:
    baseline_context = baseline.run.level_audit_report.context
    actors = []
    target_path = next(
        finding.actor_path
        for finding in baseline.actor_findings
        if finding.outcome == "stable_candidate"
    )
    for actor in baseline_context.actors:
        if actor.actor_path == target_path and target_changed:
            actors.append(
                actor.model_copy(
                    update={
                        "performance": actor.performance.model_copy(
                            update={"cast_shadow": False}
                        )
                    }
                )
            )
        else:
            actors.append(actor)
    context = UnrealLevelAuditContext.model_validate(
        {
            **baseline_context.model_dump(mode="json"),
            "audit_id": "level_audit_after_001",
            "captured_at_utc": "2026-08-19T19:00:00Z",
            "actors": [actor.model_dump(mode="json") for actor in actors],
        }
    )
    assessments = build_level_audit_assessments(context)
    return AssistantLevelAuditReport(
        audit_id=context.audit_id,
        status="review_only",
        request="Profile this level after the approved shadow change.",
        context=context,
        context_sha256=_canonical_hash(context),
        statistics=build_level_audit_statistics(context),
        assessments=assessments,
        analyzed_by=ModelIdentity(provider="ollama", model="test-model"),
        summary="Post-apply static triage completed.",
        primary_assessment_id=assessments[0].assessment_id,
        modifies_editor_scene=False,
    )


def _candidate_runtime(
    baseline: AssistantLevelProfilingReport,
    *,
    regression_ms: float,
    viewport_offset: int,
) -> AssistantRuntimePerformanceReport:
    value = baseline.run.runtime_performance_report.capture.model_dump(mode="json")
    value.update(
        capture_id="runtime_capture_after_001",
        captured_at_utc="2026-08-19T19:05:00Z",
        viewport_width_px=value["viewport_width_px"] + viewport_offset,
    )
    for sample in value["samples"]:
        sample["frame_time_ms"] += regression_ms
        sample["game_thread_ms"] += regression_ms
        sample["render_thread_ms"] += regression_ms
        if sample["rhi_thread_ms"] is not None:
            sample["rhi_thread_ms"] += regression_ms
        if sample["gpu_ms"] is not None:
            sample["gpu_ms"] += regression_ms
    sources = {
        "frame_time": [sample["frame_time_ms"] for sample in value["samples"]],
        "game_thread": [sample["game_thread_ms"] for sample in value["samples"]],
        "render_thread": [sample["render_thread_ms"] for sample in value["samples"]],
        "rhi_thread": [
            sample["rhi_thread_ms"]
            for sample in value["samples"]
            if sample["rhi_thread_ms"] is not None
        ],
        "gpu": [
            sample["gpu_ms"]
            for sample in value["samples"]
            if sample["gpu_ms"] is not None
        ],
    }
    for name, values in sources.items():
        value[name] = _timing_summary(values) if values else {
            "available": False,
            "sample_count": 0,
            "mean_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
        }
    misses = sum(
        sample["frame_time_ms"] > value["target_frame_ms"]
        for sample in value["samples"]
    )
    value["frame_budget_miss_count"] = misses
    value["frame_budget_miss_fraction"] = misses / value["sample_count"]
    value["largest_measured_component"] = max(
        ("game_thread", "render_thread", "rhi_thread", "gpu"),
        key=lambda name: value[name]["p95_ms"]
        if value[name]["available"]
        else -1.0,
    )
    capture = UnrealRuntimePerformanceCapture.model_validate(value)
    return AssistantRuntimePerformanceReport(
        report_id="runtime_report_after_001",
        status="review_complete",
        request="Capture the fixed post-apply runtime workload.",
        capture=capture,
        capture_sha256=_canonical_hash(capture),
        analyzed_by=ModelIdentity(provider="ollama", model="test-model"),
        summary="Post-apply runtime capture completed.",
        primary_bottleneck=capture.largest_measured_component,
    )


def _candidate_gpu(
    baseline: AssistantLevelProfilingReport,
    runtime: AssistantRuntimePerformanceReport,
) -> AssistantInsightsGpuReport:
    value = baseline.run.gpu_scope_report.capture.model_dump(mode="json")
    value.update(
        capture_id="gpu_capture_after_001",
        captured_at_utc="2026-08-19T19:10:00Z",
        viewport_width_px=runtime.capture.viewport_width_px,
        trace_file_name="gpu_capture_after_001.utrace",
        trace_sha256=HASH_B,
    )
    capture = UnrealInsightsGpuCapture.model_validate(value)
    return AssistantInsightsGpuReport(
        report_id="gpu_report_after_001",
        status="review_complete",
        request="Capture the fixed post-apply GPU workload.",
        capture=capture,
        capture_sha256=_canonical_hash(capture),
        capture_file_sha256=HASH_B,
        analyzed_by=ModelIdentity(provider="ollama", model="test-model"),
        summary="Post-apply GPU capture completed.",
        primary_scope_id=capture.scopes[0].scope_id,
    )


def _candidate_report(
    baseline: AssistantLevelProfilingReport,
    *,
    regression_ms: float = 0.0,
    viewport_offset: int = 0,
    target_changed: bool = True,
) -> AssistantLevelProfilingReport:
    audit = _candidate_audit(baseline, target_changed=target_changed)
    runtime = _candidate_runtime(
        baseline,
        regression_ms=regression_ms,
        viewport_offset=viewport_offset,
    )
    gpu = _candidate_gpu(baseline, runtime)
    skips = {
        assessment.assessment_id: "Skipped after complete whole-level measurements for this verification fixture."
        for assessment in audit.assessments[:2]
    }
    run = build_level_profiling_run(
        profiling_run_id="level_profile_after",
        created_at_utc=datetime(2026, 8, 19, 19, 0, tzinfo=timezone.utc),
        request="Repeat the complete profiling workload after the shadow optimization.",
        level_audit_report=audit,
        runtime_performance_report=runtime,
        gpu_scope_report=gpu,
        actor_study_reports=[],
        skipped_actor_studies=skips,
    )
    return analyze_level_profiling_run(run, report_id="level_profile_after_report")


def _analyze(
    *,
    regression_ms: float = 0.0,
    viewport_offset: int = 0,
    target_changed: bool = True,
    candidate_file_hash: str = HASH_C,
) -> AssistantShadowOptimizationVerificationReport:
    baseline = _baseline_report()
    return analyze_shadow_optimization_verification(
        shadow_report=_shadow_report(baseline),
        shadow_report_file_sha256=HASH_A,
        baseline_report=baseline,
        baseline_report_file_sha256=HASH_B,
        candidate_report=_candidate_report(
            baseline,
            regression_ms=regression_ms,
            viewport_offset=viewport_offset,
            target_changed=target_changed,
        ),
        candidate_report_file_sha256=candidate_file_hash,
    )


class ShadowOptimizationVerificationTests(unittest.TestCase):
    def test_identical_fresh_workload_recommends_retain(self) -> None:
        report = _analyze()
        self.assertEqual(report.status, "complete")
        self.assertEqual(report.decision, "retain")
        self.assertTrue(report.workload_matches)
        self.assertTrue(report.target_transition_matches)
        self.assertTrue(report.sources_are_distinct)
        self.assertFalse(report.modifies_editor_scene)
        self.assertTrue(report.rollback_requires_approval)
        self.assertFalse(report.auto_save)

    def test_material_frame_regression_recommends_rollback(self) -> None:
        report = _analyze(regression_ms=5.0)
        self.assertEqual(report.status, "complete")
        self.assertEqual(report.decision, "rollback")
        self.assertGreater(report.metrics.frame_p95_delta_ms, 0.5)
        self.assertTrue(any("Frame p95 regressed" in item for item in report.decision_reasons))

    def test_changed_viewport_is_explicitly_unresolved(self) -> None:
        report = _analyze(viewport_offset=1)
        self.assertEqual(report.status, "unresolved")
        self.assertEqual(report.decision, "unresolved")
        self.assertFalse(report.workload_matches)

    def test_missing_formal_target_transition_is_unresolved(self) -> None:
        report = _analyze(target_changed=False)
        self.assertEqual(report.decision, "unresolved")
        self.assertFalse(report.target_transition_matches)

    def test_reused_source_file_hash_is_unresolved(self) -> None:
        report = _analyze(candidate_file_hash=HASH_B)
        self.assertEqual(report.decision, "unresolved")
        self.assertFalse(report.sources_are_distinct)

    def test_tampered_metrics_are_rejected(self) -> None:
        value = _analyze().model_dump(mode="json")
        value["metrics"]["candidate_frame_p95_ms"] += 1.0
        with self.assertRaisesRegex(ValidationError, "not recomputable"):
            AssistantShadowOptimizationVerificationReport.model_validate(value)

    def test_rollback_cannot_be_relabelled_as_retain(self) -> None:
        value = _analyze(regression_ms=5.0).model_dump(mode="json")
        value["decision"] = "retain"
        value["decision_reasons"] = ["tampered"]
        with self.assertRaisesRegex(ValidationError, "not recomputable"):
            AssistantShadowOptimizationVerificationReport.model_validate(value)

    def test_file_analysis_binds_all_three_exact_hashes(self) -> None:
        baseline = _baseline_report()
        candidate = _candidate_report(baseline)
        shadow = _shadow_report(baseline)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shadow_path = root / "shadow.json"
            baseline_path = root / "baseline.json"
            candidate_path = root / "candidate.json"
            shadow_path.write_text(shadow.model_dump_json(), encoding="utf-8")
            baseline_path.write_text(baseline.model_dump_json(), encoding="utf-8")
            candidate_path.write_text(candidate.model_dump_json(), encoding="utf-8")
            report = analyze_shadow_optimization_verification_files(
                shadow_report_path=shadow_path,
                baseline_report_path=baseline_path,
                candidate_report_path=candidate_path,
            )
            self.assertEqual(
                report.shadow_optimization_report_file_sha256,
                hashlib.sha256(shadow_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                report.baseline_level_profiling_report_file_sha256,
                hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                report.candidate_level_profiling_report_file_sha256,
                hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
