from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_shadow_optimization import (
    analyze_shadow_optimization_capture_set,
    analyze_shadow_optimization_file,
)
from render_master_bot.contracts import (
    AssistantShadowOptimizationReport,
    UnrealShadowOptimizationCaptureSet,
)


ROOT = Path(__file__).resolve().parents[1]


def capture_set_value() -> dict:
    actor_study = json.loads(
        (ROOT / "examples" / "unreal_actor_gpu_impact_study_capture_set.json").read_text(
            encoding="utf-8"
        )
    )
    selection = json.loads(
        (ROOT / "examples" / "unreal_performance_selection_context.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "schema_version": "0.1",
        "experiment_id": "shadow_optimization_example_001",
        "created_at_utc": "2026-08-19T18:30:00Z",
        "request": "Test whether disabling this Actor's shadows reduces GPU cost.",
        "target": selection["actors"][0],
        "runtime_actor_path": actor_study["target"]["runtime_actor_path"],
        "candidate_cast_shadow": False,
        "noise_control_baseline": actor_study["noise_control_visible"],
        "trials": [
            {
                "trial_id": "trial_001",
                "order": "baseline_then_candidate",
                "baseline": actor_study["trials"][0]["visible"],
                "candidate": actor_study["trials"][0]["hidden"],
            },
            {
                "trial_id": "trial_002",
                "order": "candidate_then_baseline",
                "baseline": actor_study["trials"][1]["visible"],
                "candidate": actor_study["trials"][1]["hidden"],
            },
            {
                "trial_id": "trial_003",
                "order": "baseline_then_candidate",
                "baseline": actor_study["trials"][2]["visible"],
                "candidate": actor_study["trials"][2]["hidden"],
            },
        ],
        "requested_capture_duration_seconds": 5.0,
        "warmup_seconds_per_capture": 1.0,
        "schedule": "AA_AB_BA_AB",
        "runtime_state_restored": True,
        "modifies_editor_scene": False,
    }


class ShadowOptimizationTests(unittest.TestCase):
    def test_stable_reduction_proposes_one_bounded_action(self) -> None:
        capture_set = UnrealShadowOptimizationCaptureSet.model_validate(
            capture_set_value()
        )
        report = analyze_shadow_optimization_capture_set(
            capture_set,
            capture_set_file_sha256="a" * 64,
        )
        self.assertEqual(report.status, "proposed")
        self.assertEqual(report.decision, "propose_apply")
        self.assertTrue(report.approval_required)
        self.assertIsNotNone(report.proposed_action)
        assert report.proposed_action is not None
        self.assertTrue(report.proposed_action.before.cast_shadow)
        self.assertFalse(report.proposed_action.after.cast_shadow)
        self.assertEqual(
            [change.property for change in report.proposed_action.changes],
            ["cast_shadow"],
        )
        self.assertTrue(report.runtime_state_restored)
        self.assertFalse(report.modifies_editor_scene)

    def test_noise_limited_result_is_rejected_without_action(self) -> None:
        value = capture_set_value()
        candidate_totals = [50.5, 50.0, 50.2]
        for trial, total in zip(value["trials"], candidate_totals, strict=True):
            scope = trial["candidate"]["scopes"][0]
            scope["total_inclusive_ms"] = total
            scope["mean_inclusive_ms"] = total / scope["instance_count"]
            scope["max_inclusive_ms"] = total / scope["instance_count"] + 1.0
        capture_set = UnrealShadowOptimizationCaptureSet.model_validate(value)
        report = analyze_shadow_optimization_capture_set(
            capture_set,
            capture_set_file_sha256="b" * 64,
        )
        self.assertEqual(report.status, "rejected")
        self.assertEqual(report.decision, "reject")
        self.assertFalse(report.approval_required)
        self.assertIsNone(report.proposed_action)
        self.assertTrue(report.rejection_reasons)

    def test_mismatched_workload_is_rejected(self) -> None:
        value = capture_set_value()
        value["trials"][2]["candidate"]["viewport_width_px"] = 1280
        with self.assertRaisesRegex(ValidationError, "one fixed workload"):
            UnrealShadowOptimizationCaptureSet.model_validate(value)

    def test_unrestored_runtime_state_is_rejected(self) -> None:
        value = capture_set_value()
        value["runtime_state_restored"] = False
        with self.assertRaises(ValidationError):
            UnrealShadowOptimizationCaptureSet.model_validate(value)

    def test_tampered_aggregate_is_rejected(self) -> None:
        capture_set = UnrealShadowOptimizationCaptureSet.model_validate(
            capture_set_value()
        )
        report = analyze_shadow_optimization_capture_set(
            capture_set,
            capture_set_file_sha256="c" * 64,
        )
        value = report.model_dump(mode="json")
        value["aggregates"][0]["median_baseline_minus_candidate_ms_per_second"] += 2.0
        with self.assertRaises(ValidationError):
            AssistantShadowOptimizationReport.model_validate(value)

    def test_file_analysis_binds_exact_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture_set.json"
            path.write_text(json.dumps(capture_set_value()), encoding="utf-8")
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            report = analyze_shadow_optimization_file(path)
        self.assertEqual(report.capture_set_file_sha256, expected)

    def test_formal_action_cannot_be_injected_into_rejection(self) -> None:
        capture_set = UnrealShadowOptimizationCaptureSet.model_validate(
            capture_set_value()
        )
        proposed = analyze_shadow_optimization_capture_set(
            capture_set,
            capture_set_file_sha256="d" * 64,
        )
        value = copy.deepcopy(proposed.model_dump(mode="json"))
        value["status"] = "rejected"
        value["decision"] = "reject"
        value["approval_required"] = False
        value["rejection_reasons"] = ["tampered"]
        with self.assertRaisesRegex(ValidationError, "decision is not recomputable"):
            AssistantShadowOptimizationReport.model_validate(value)


if __name__ == "__main__":
    unittest.main()
