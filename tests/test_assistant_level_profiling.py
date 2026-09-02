from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_level_profiling import (
    LevelProfilingSynthesisError,
    analyze_level_profiling_run,
    build_level_profiling_run,
    load_level_profiling_skips,
)
from render_master_bot.contracts import (
    AssistantActorGpuImpactStudyReport,
    AssistantInsightsGpuReport,
    AssistantLevelAuditReport,
    AssistantRuntimePerformanceReport,
    ModelIdentity,
    UnrealActorGpuImpactStudyCaptureSet,
    UnrealInsightsGpuCapture,
    UnrealLevelAuditContext,
    UnrealRuntimePerformanceCapture,
    build_actor_gpu_impact_study_aggregates,
    build_level_audit_assessments,
    build_level_audit_statistics,
)


ROOT = Path(__file__).resolve().parents[1]
HASH_A = "a" * 64


def _canonical_hash(value) -> str:
    return hashlib.sha256(value.model_dump_json().encode("utf-8")).hexdigest()


def _audit_report() -> AssistantLevelAuditReport:
    context = UnrealLevelAuditContext.model_validate_json(
        (ROOT / "examples" / "unreal_level_audit_context.json").read_text()
    )
    assessments = build_level_audit_assessments(context)
    return AssistantLevelAuditReport(
        audit_id=context.audit_id,
        status="review_only",
        request="Profile this level without changing it.",
        context=context,
        context_sha256=_canonical_hash(context),
        statistics=build_level_audit_statistics(context),
        assessments=assessments,
        analyzed_by=ModelIdentity(provider="ollama", model="test-model"),
        summary="Static triage completed.",
        primary_assessment_id=assessments[0].assessment_id,
        modifies_editor_scene=False,
    )


def _runtime_report() -> AssistantRuntimePerformanceReport:
    value = json.loads(
        (ROOT / "examples" / "unreal_runtime_performance_capture.json").read_text()
    )
    original_samples = value["samples"]
    value["samples"] = [
        {**original_samples[index % len(original_samples)], "frame_index": index}
        for index in range(120)
    ]
    value["sample_count"] = 120
    value["warmup_frames"] = 30
    value["frame_budget_miss_count"] *= 120 // len(original_samples)
    for name in ("frame_time", "game_thread", "render_thread", "rhi_thread", "gpu"):
        if value[name]["available"]:
            value[name]["sample_count"] = 120
    capture = UnrealRuntimePerformanceCapture.model_validate(value)
    return AssistantRuntimePerformanceReport(
        report_id="runtime_report_001",
        status="review_complete",
        request="Capture the fixed runtime workload.",
        capture=capture,
        capture_sha256=_canonical_hash(capture),
        analyzed_by=ModelIdentity(provider="ollama", model="test-model"),
        summary="Runtime capture completed.",
        primary_bottleneck=capture.largest_measured_component,
    )


def _gpu_report(runtime: AssistantRuntimePerformanceReport) -> AssistantInsightsGpuReport:
    value = json.loads(
        (ROOT / "examples" / "unreal_insights_gpu_capture.json").read_text()
    )
    capture = runtime.capture
    value.update(
        project_name=capture.project_name,
        world_path=capture.world_path,
        capture_mode=capture.capture_mode,
        viewport_width_px=capture.viewport_width_px,
        viewport_height_px=capture.viewport_height_px,
        gpu_name=capture.gpu_name,
    )
    gpu = UnrealInsightsGpuCapture.model_validate(value)
    return AssistantInsightsGpuReport(
        report_id="gpu_report_001",
        status="review_complete",
        request="Capture the fixed GPU workload.",
        capture=gpu,
        capture_sha256=_canonical_hash(gpu),
        capture_file_sha256=HASH_A,
        analyzed_by=ModelIdentity(provider="ollama", model="test-model"),
        summary="GPU scope capture completed.",
        primary_scope_id=gpu.scopes[0].scope_id,
    )


def _study_report(
    audit: AssistantLevelAuditReport,
    runtime: AssistantRuntimePerformanceReport,
    candidate_index: int,
) -> AssistantActorGpuImpactStudyReport:
    value = json.loads(
        (ROOT / "examples" / "unreal_actor_gpu_impact_study_capture_set.json").read_text()
    )
    assessment = audit.assessments[candidate_index]
    actor = next(
        item for item in audit.context.actors if item.actor_path == assessment.actor_path
    )
    suffix = str(candidate_index + 1)
    value["study_id"] = f"profiling_study_{suffix}"
    value["target"]["editor_actor_path"] = actor.actor_path
    value["target"]["actor_label"] = actor.actor_name
    captures = [
        *value["noise_control_visible"],
        *[
            capture
            for trial in value["trials"]
            for capture in (trial["visible"], trial["hidden"])
        ],
    ]
    id_map = {}
    for capture_value in captures:
        old_id = capture_value["capture_id"]
        new_id = f"{old_id}_{suffix}"
        id_map[old_id] = new_id
        capture_value.update(
            capture_id=new_id,
            project_name=runtime.capture.project_name,
            world_path=runtime.capture.world_path,
            capture_mode=runtime.capture.capture_mode,
            viewport_width_px=runtime.capture.viewport_width_px,
            viewport_height_px=runtime.capture.viewport_height_px,
            gpu_name=runtime.capture.gpu_name,
            trace_file_name=f"{new_id}.utrace",
        )
    value["sequence_capture_ids"] = [
        id_map[capture_id] for capture_id in value["sequence_capture_ids"]
    ]
    capture_set = UnrealActorGpuImpactStudyCaptureSet.model_validate(value)
    aggregates = build_actor_gpu_impact_study_aggregates(capture_set)
    return AssistantActorGpuImpactStudyReport(
        report_id=f"study_report_{suffix}",
        status="review_complete",
        request="Measure this Actor with the fixed repeatable study.",
        capture_set=capture_set,
        capture_set_sha256=_canonical_hash(capture_set),
        capture_set_file_sha256=HASH_A,
        analyzed_by=ModelIdentity(provider="ollama", model="test-model"),
        aggregates=aggregates,
        summary="Repeatable Actor study completed.",
        primary_aggregate_id=aggregates[0].aggregate_id,
    )


class AssistantLevelProfilingTests(unittest.TestCase):
    def setUp(self):
        self.audit = _audit_report()
        self.runtime = _runtime_report()
        self.gpu = _gpu_report(self.runtime)

    def test_partial_run_is_recomputable_and_profiling_first(self):
        first = _study_report(self.audit, self.runtime, 0)
        second = self.audit.assessments[1]
        run = build_level_profiling_run(
            profiling_run_id="level_profile_001",
            created_at_utc=datetime.now(timezone.utc),
            request="Profile the full level.",
            level_audit_report=self.audit,
            runtime_performance_report=self.runtime,
            gpu_scope_report=self.gpu,
            actor_study_reports=[first],
            skipped_actor_studies={
                second.assessment_id: "No matching runtime Actor was available."
            },
        )
        report = analyze_level_profiling_run(run)

        self.assertEqual(report.status, "partial")
        self.assertEqual(report.run.measurement_priority, "profiling_first")
        self.assertEqual(report.run.visual_evidence_role, "auxiliary_regression_guard")
        self.assertEqual(report.measured_actor_count, 1)
        self.assertEqual(report.skipped_actor_count, 1)
        self.assertFalse(report.modifies_editor_scene)
        self.assertTrue(report.runtime_state_restored)
        self.assertEqual(report.actor_findings[1].outcome, "skipped")

    def test_complete_run_requires_top_two_in_deterministic_order(self):
        first = _study_report(self.audit, self.runtime, 0)
        second = _study_report(self.audit, self.runtime, 1)
        run = build_level_profiling_run(
            profiling_run_id="level_profile_002",
            created_at_utc=datetime.now(timezone.utc),
            request="Profile the full level.",
            level_audit_report=self.audit,
            runtime_performance_report=self.runtime,
            gpu_scope_report=self.gpu,
            actor_study_reports=[second, first],
            skipped_actor_studies={},
        )

        self.assertEqual(
            [attempt.assessment_id for attempt in run.actor_attempts],
            [item.assessment_id for item in self.audit.assessments[:2]],
        )
        self.assertEqual(analyze_level_profiling_run(run).status, "complete")

    def test_mismatched_gpu_workload_is_rejected(self):
        capture = self.gpu.capture.model_copy(
            update={"viewport_width_px": self.gpu.capture.viewport_width_px + 1}
        )
        bad_gpu = self.gpu.model_copy(
            update={"capture": capture, "capture_sha256": _canonical_hash(capture)}
        )
        with self.assertRaisesRegex(
            LevelProfilingSynthesisError, "one fixed PIE/SIE workload"
        ):
            build_level_profiling_run(
                profiling_run_id="level_profile_bad",
                created_at_utc=datetime.now(timezone.utc),
                request="Profile the full level.",
                level_audit_report=self.audit,
                runtime_performance_report=self.runtime,
                gpu_scope_report=bad_gpu,
                actor_study_reports=[],
                skipped_actor_studies={
                    item.assessment_id: "Skipped for test."
                    for item in self.audit.assessments[:2]
                },
            )

    def test_tampered_summary_metric_is_rejected(self):
        skips = {
            item.assessment_id: "Skipped for test."
            for item in self.audit.assessments[:2]
        }
        run = build_level_profiling_run(
            profiling_run_id="level_profile_003",
            created_at_utc=datetime.now(timezone.utc),
            request="Profile the full level.",
            level_audit_report=self.audit,
            runtime_performance_report=self.runtime,
            gpu_scope_report=self.gpu,
            actor_study_reports=[],
            skipped_actor_studies=skips,
        )
        value = analyze_level_profiling_run(run).model_dump(mode="json")
        value["frame_p95_ms"] += 1.0
        with self.assertRaisesRegex(ValidationError, "summary metrics"):
            from render_master_bot.contracts import AssistantLevelProfilingReport

            AssistantLevelProfilingReport.model_validate(value)

    def test_skip_manifest_is_strict_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skips.json"
            path.write_text(
                json.dumps(
                    {
                        "skips": [
                            {
                                "assessment_id": "audit_actor_0000",
                                "reason": "Runtime counterpart unavailable.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                load_level_profiling_skips(path),
                {"audit_actor_0000": "Runtime counterpart unavailable."},
            )
            path.write_text('{"skips": [], "extra": true}', encoding="utf-8")
            with self.assertRaisesRegex(
                LevelProfilingSynthesisError, "contain only"
            ):
                load_level_profiling_skips(path)


if __name__ == "__main__":
    unittest.main()
