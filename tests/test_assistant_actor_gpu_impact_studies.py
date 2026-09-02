import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_actor_gpu_impact_studies import (
    ActorGpuImpactStudyReviewError,
    hash_actor_gpu_impact_study_file,
    load_actor_gpu_impact_study_capture_set,
    review_actor_gpu_impact_study,
)
from render_master_bot.contracts import (
    AssistantActorGpuImpactStudyReport,
    UnrealActorGpuImpactStudyCaptureSet,
    build_actor_gpu_impact_study_aggregates,
)
from render_master_bot.ollama import StructuredResponse


class FakeClient:
    def __init__(self, contents):
        self.contents = iter(contents if isinstance(contents, list) else [contents])
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(
            content=next(self.contents), model="fake-actor-gpu-study-model"
        )


def example_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "unreal_actor_gpu_impact_study_capture_set.json"
    )


def capture_set() -> UnrealActorGpuImpactStudyCaptureSet:
    return UnrealActorGpuImpactStudyCaptureSet.model_validate_json(
        example_path().read_text(encoding="utf-8")
    )


def review_json(**overrides) -> str:
    value = {
        "schema_version": "0.1",
        "outcome": "review_complete",
        "summary": (
            "ShadowDepths is a stable measured impact candidate across all three "
            "counterbalanced trials and exceeds the visible-visible control noise."
        ),
        "primary_aggregate_id": "aggregate_000",
        "findings": [
            {
                "severity": "warning",
                "category": "stable_candidate",
                "evidence_aggregate_ids": ["aggregate_000"],
                "observation": (
                    "The paired median reduction exceeded control noise with three "
                    "decreased-direction votes."
                ),
                "recommendation": (
                    "Repeat the bounded study in a packaged benchmark before changing "
                    "the asset."
                ),
            }
        ],
        "missing_capabilities": [],
    }
    value.update(overrides)
    return json.dumps(value)


class ActorGpuImpactStudyTests(unittest.TestCase):
    def test_example_validates_fixed_schedule_and_restoration(self):
        current = capture_set()
        self.assertEqual(current.schedule, "AA_AB_BA_AB")
        self.assertEqual(len(current.sequence_capture_ids), 8)
        self.assertEqual(
            [trial.order for trial in current.trials],
            ["visible_then_hidden", "hidden_then_visible", "visible_then_hidden"],
        )
        self.assertTrue(current.runtime_state_restored)

    def test_aggregate_is_recomputed_from_all_eight_captures(self):
        aggregates = build_actor_gpu_impact_study_aggregates(capture_set())
        self.assertEqual(len(aggregates), 1)
        aggregate = aggregates[0]
        self.assertEqual(aggregate.aggregate_id, "aggregate_000")
        self.assertEqual(aggregate.classification, "stable_candidate")
        self.assertAlmostEqual(
            aggregate.control_noise_abs_ms_per_second, 0.2, places=6
        )
        self.assertAlmostEqual(
            aggregate.median_baseline_minus_hidden_ms_per_second, 4.0, places=6
        )
        self.assertAlmostEqual(aggregate.delta_mad_ms_per_second, 0.0, places=6)
        self.assertEqual(aggregate.decreased_vote_count, 3)

    def test_tampered_schedule_is_rejected(self):
        value = json.loads(example_path().read_text(encoding="utf-8"))
        value["sequence_capture_ids"][2], value["sequence_capture_ids"][3] = (
            value["sequence_capture_ids"][3],
            value["sequence_capture_ids"][2],
        )
        with self.assertRaisesRegex(ValidationError, "AA_AB_BA_AB"):
            UnrealActorGpuImpactStudyCaptureSet.model_validate(value)

    def test_mismatched_environment_is_rejected(self):
        value = json.loads(example_path().read_text(encoding="utf-8"))
        value["trials"][2]["hidden"]["viewport_width_px"] = 1280
        with self.assertRaisesRegex(ValidationError, "environments must match"):
            UnrealActorGpuImpactStudyCaptureSet.model_validate(value)

    def test_noise_limited_classification_uses_control_difference(self):
        value = json.loads(example_path().read_text(encoding="utf-8"))
        for trial, total in zip(value["trials"], [51.5, 50.5, 50.0], strict=True):
            trial["hidden"]["scopes"][0]["total_inclusive_ms"] = total
            trial["hidden"]["scopes"][0]["mean_inclusive_ms"] = total / 5.0
            trial["hidden"]["scopes"][0]["max_inclusive_ms"] = total / 5.0 + 1.0
        current = UnrealActorGpuImpactStudyCaptureSet.model_validate(value)
        aggregate = build_actor_gpu_impact_study_aggregates(current)[0]
        self.assertEqual(aggregate.classification, "noise_limited")
        self.assertAlmostEqual(
            aggregate.median_baseline_minus_hidden_ms_per_second, 0.1, places=6
        )

    def test_loader_and_exact_file_hash(self):
        self.assertEqual(load_actor_gpu_impact_study_capture_set(example_path()), capture_set())
        expected = hashlib.sha256(example_path().read_bytes()).hexdigest()
        self.assertEqual(hash_actor_gpu_impact_study_file(example_path()), expected)
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                ActorGpuImpactStudyReviewError, "invalid Actor GPU impact study"
            ):
                load_actor_gpu_impact_study_capture_set(invalid)

    def test_complete_review_preserves_raw_capture_set_and_host_aggregates(self):
        current = capture_set()
        client = FakeClient(review_json())
        result = review_actor_gpu_impact_study(
            prompt="Is the selected Actor's measured GPU impact repeatable?",
            capture_set=current,
            client=client,
            model="planner",
            report_id="actor_gpu_study_review_001",
            source_file_sha256=hash_actor_gpu_impact_study_file(example_path()),
        )
        self.assertEqual(result.report.capture_set, current)
        self.assertEqual(
            result.report.aggregates,
            build_actor_gpu_impact_study_aggregates(current),
        )
        self.assertFalse(result.report.modifies_editor_scene)
        self.assertEqual(result.attempt_count, 1)
        self.assertNotEqual(
            result.report.capture_set_sha256,
            result.report.capture_set_file_sha256,
        )
        model_input = client.requests[0]["messages"][1]["content"]
        self.assertIn("available_aggregates", model_input)
        self.assertIn("control_noise_abs_ms_per_second", model_input)
        self.assertNotIn("editor_actor_path", model_input)
        self.assertNotIn("trace_sha256", model_input)
        AssistantActorGpuImpactStudyReport.model_validate(
            result.report.model_dump(mode="json")
        )

    def test_tampered_host_aggregate_is_rejected(self):
        result = review_actor_gpu_impact_study(
            prompt="Review repeatability",
            capture_set=capture_set(),
            client=FakeClient(review_json()),
            model="planner",
            report_id="actor_gpu_study_tamper",
        )
        value = result.report.model_dump(mode="json")
        value["aggregates"][0]["control_noise_abs_ms_per_second"] = 99.0
        with self.assertRaisesRegex(ValidationError, "statistics are not recomputable"):
            AssistantActorGpuImpactStudyReport.model_validate(value)

    def test_unknown_aggregate_gets_one_bounded_retry(self):
        invalid = review_json(
            primary_aggregate_id="aggregate_not_measured",
            findings=[
                {
                    "severity": "critical",
                    "category": "stable_candidate",
                    "evidence_aggregate_ids": ["aggregate_not_measured"],
                    "observation": "Unsupported aggregate.",
                    "recommendation": "Unsupported action.",
                }
            ],
        )
        client = FakeClient([invalid, review_json()])
        result = review_actor_gpu_impact_study(
            prompt="Review the repeatable Actor study",
            capture_set=capture_set(),
            client=client,
            model="planner",
            report_id="actor_gpu_study_retry",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertIn("unavailable", result.recovery_reason)

    def test_contradictory_finding_category_gets_one_bounded_retry(self):
        client = FakeClient(
            [
                review_json(
                    findings=[
                        {
                            "severity": "warning",
                            "category": "noise_limited",
                            "evidence_aggregate_ids": ["aggregate_000"],
                            "observation": "The model used the wrong classification.",
                            "recommendation": "Repeat the bounded measurement.",
                        }
                    ]
                ),
                review_json(),
            ]
        )
        result = review_actor_gpu_impact_study(
            prompt="Review the repeatable Actor study",
            capture_set=capture_set(),
            client=client,
            model="planner",
            report_id="actor_gpu_study_category_retry",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertIn("contradicts", result.recovery_reason)

    def test_persisted_report_rejects_contradictory_finding_category(self):
        result = review_actor_gpu_impact_study(
            prompt="Review repeatability",
            capture_set=capture_set(),
            client=FakeClient(review_json()),
            model="planner",
            report_id="actor_gpu_study_category_tamper",
        )
        value = result.report.model_dump(mode="json")
        value["findings"][0]["category"] = "noise_limited"
        with self.assertRaisesRegex(ValidationError, "contradicts"):
            AssistantActorGpuImpactStudyReport.model_validate(value)

    def test_direct_draw_request_can_be_unresolved(self):
        result = review_actor_gpu_impact_study(
            prompt="Which exact draw call did this Actor cause?",
            capture_set=capture_set(),
            client=FakeClient(
                review_json(
                    outcome="unresolved",
                    summary="The study has no direct per-draw ownership evidence.",
                    primary_aggregate_id=None,
                    findings=[],
                    missing_capabilities=["direct Actor draw-call attribution"],
                )
            ),
            model="planner",
            report_id="actor_gpu_study_draw_gap",
        )
        self.assertEqual(result.report.status, "unresolved")
        self.assertEqual(result.report.findings, [])


if __name__ == "__main__":
    unittest.main()
