import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_actor_gpu_impact import (
    ActorGpuImpactReviewError,
    hash_actor_gpu_impact_file,
    load_actor_gpu_impact_experiment,
    review_actor_gpu_impact,
)
from render_master_bot.contracts import (
    AssistantActorGpuImpactReport,
    UnrealActorGpuImpactExperiment,
)
from render_master_bot.ollama import StructuredResponse


class FakeClient:
    def __init__(self, contents):
        self.contents = iter(contents if isinstance(contents, list) else [contents])
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(
            content=next(self.contents), model="fake-actor-gpu-model"
        )


def example_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "unreal_actor_gpu_impact_experiment.json"
    )


def experiment() -> UnrealActorGpuImpactExperiment:
    return UnrealActorGpuImpactExperiment.model_validate_json(
        example_path().read_text(encoding="utf-8")
    )


def review_json(**overrides) -> str:
    value = {
        "schema_version": "0.1",
        "outcome": "review_complete",
        "summary": (
            "ShadowDepths decreased while HeroStatue was hidden, which is an impact "
            "candidate that should be repeated under the same workload."
        ),
        "primary_delta_id": "delta_000",
        "findings": [
            {
                "severity": "warning",
                "category": "impact_candidate",
                "evidence_delta_ids": ["delta_000"],
                "observation": (
                    "The normalized ShadowDepths total was lower in the hidden variant."
                ),
                "recommendation": (
                    "Repeat the same baseline-hidden pair before changing the asset."
                ),
            }
        ],
        "missing_capabilities": [],
    }
    value.update(overrides)
    return json.dumps(value)


class ActorGpuImpactTests(unittest.TestCase):
    def test_example_validates_and_is_sorted(self):
        current = experiment()
        self.assertEqual(current.comparison_count, 4)
        self.assertEqual(current.deltas[0].scope_name, "ShadowDepths")
        self.assertTrue(current.runtime_state_restored)

    def test_tampered_normalized_delta_is_rejected(self):
        value = json.loads(example_path().read_text(encoding="utf-8"))
        value["deltas"][0]["baseline_minus_variant_ms_per_second"] = 99.0
        with self.assertRaisesRegex(ValidationError, "delta does not match"):
            UnrealActorGpuImpactExperiment.model_validate(value)

    def test_mismatched_capture_environment_is_rejected(self):
        value = json.loads(example_path().read_text(encoding="utf-8"))
        value["variant"]["viewport_width_px"] = 1280
        with self.assertRaisesRegex(ValidationError, "environment must match"):
            UnrealActorGpuImpactExperiment.model_validate(value)

    def test_loader_and_exact_file_hash(self):
        self.assertEqual(load_actor_gpu_impact_experiment(example_path()), experiment())
        expected = hashlib.sha256(example_path().read_bytes()).hexdigest()
        self.assertEqual(hash_actor_gpu_impact_file(example_path()), expected)
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                ActorGpuImpactReviewError, "invalid Actor GPU impact experiment"
            ):
                load_actor_gpu_impact_experiment(invalid)

    def test_complete_review_preserves_experiment_and_is_read_only(self):
        current = experiment()
        client = FakeClient(review_json())
        result = review_actor_gpu_impact(
            prompt="Did this Actor measurably affect the captured GPU scopes?",
            experiment=current,
            client=client,
            model="planner",
            report_id="actor_gpu_review_001",
            source_file_sha256=hash_actor_gpu_impact_file(example_path()),
        )
        self.assertEqual(result.report.experiment, current)
        self.assertFalse(result.report.modifies_editor_scene)
        self.assertEqual(result.attempt_count, 1)
        self.assertNotEqual(
            result.report.experiment_sha256,
            result.report.experiment_file_sha256,
        )
        model_input = client.requests[0]["messages"][1]["content"]
        self.assertIn("available_deltas", model_input)
        self.assertNotIn("editor_actor_path", model_input)
        self.assertNotIn("trace_sha256", model_input)
        AssistantActorGpuImpactReport.model_validate(
            result.report.model_dump(mode="json")
        )

    def test_unknown_delta_gets_one_bounded_retry(self):
        invalid = review_json(
            primary_delta_id="delta_not_measured",
            findings=[
                {
                    "severity": "critical",
                    "category": "impact_candidate",
                    "evidence_delta_ids": ["delta_not_measured"],
                    "observation": "Unsupported delta.",
                    "recommendation": "Unsupported action.",
                }
            ],
        )
        client = FakeClient([invalid, review_json()])
        result = review_actor_gpu_impact(
            prompt="Review the Actor impact experiment",
            experiment=experiment(),
            client=client,
            model="planner",
            report_id="actor_gpu_retry",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertIn("not in the experiment", result.recovery_reason)

    def test_direct_draw_call_request_can_be_unresolved(self):
        result = review_actor_gpu_impact(
            prompt="Which draw call from this Actor caused the cost?",
            experiment=experiment(),
            client=FakeClient(
                review_json(
                    outcome="unresolved",
                    summary="The experiment does not contain per-draw attribution.",
                    primary_delta_id=None,
                    findings=[],
                    missing_capabilities=["per-draw Actor attribution"],
                )
            ),
            model="planner",
            report_id="actor_gpu_draw_gap",
        )
        self.assertEqual(result.report.status, "unresolved")
        self.assertEqual(result.report.findings, [])


if __name__ == "__main__":
    unittest.main()
