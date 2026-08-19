import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_insights_gpu import (
    InsightsGpuReviewError,
    hash_insights_gpu_capture_file,
    load_insights_gpu_capture,
    review_insights_gpu_capture,
)
from render_master_bot.contracts import (
    AssistantInsightsGpuReport,
    UnrealInsightsGpuCapture,
)
from render_master_bot.ollama import StructuredResponse


class FakeClient:
    def __init__(self, contents):
        self.contents = iter(contents if isinstance(contents, list) else [contents])
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(
            content=next(self.contents), model="fake-insights-gpu-model"
        )


def example_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "unreal_insights_gpu_capture.json"
    )


def capture() -> UnrealInsightsGpuCapture:
    return load_insights_gpu_capture(example_path())


def review_json(**updates):
    value = {
        "schema_version": "0.1",
        "outcome": "review_complete",
        "summary": "The trace ranks LumenSceneLighting first by accumulated inclusive GPU scope time.",
        "primary_scope_id": "scope_000",
        "findings": [
            {
                "severity": "warning",
                "category": "gpu_scope",
                "evidence_scope_ids": ["scope_000", "scope_001"],
                "observation": "The first scope has higher total and mean inclusive duration than the second scope.",
                "recommendation": "Repeat the trace with one bounded Lumen quality experiment and compare the same workload.",
            }
        ],
        "missing_capabilities": [],
    }
    value.update(updates)
    return json.dumps(value)


class AssistantInsightsGpuTests(unittest.TestCase):
    def test_repository_capture_recomputes_and_validates(self):
        current = capture()
        self.assertEqual(current.gpu_queue_count, 2)
        self.assertEqual(current.total_gpu_event_count, 120)
        self.assertEqual(current.scopes[0].scope_id, "scope_000")
        self.assertAlmostEqual(current.scopes[0].mean_inclusive_ms, 5.0)

    def test_loader_rejects_tampered_scope_mean(self):
        value = json.loads(example_path().read_text(encoding="utf-8"))
        value["scopes"][0]["mean_inclusive_ms"] = 9.0
        with self.assertRaisesRegex(ValidationError, "mean must equal"):
            UnrealInsightsGpuCapture.model_validate(value)

    def test_loader_rejects_unsorted_scope_totals(self):
        value = json.loads(example_path().read_text(encoding="utf-8"))
        value["scopes"][0], value["scopes"][1] = (
            value["scopes"][1],
            value["scopes"][0],
        )
        with self.assertRaisesRegex(ValidationError, "ordered by total"):
            UnrealInsightsGpuCapture.model_validate(value)

    def test_loader_reports_invalid_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                InsightsGpuReviewError,
                "invalid Unreal Insights GPU capture",
            ):
                load_insights_gpu_capture(path)

    def test_complete_review_preserves_capture_and_is_read_only(self):
        current = capture()
        client = FakeClient(review_json())
        result = review_insights_gpu_capture(
            prompt="Which measured GPU scopes deserve the next experiment?",
            capture=current,
            client=client,
            model="planner",
            report_id="insights_review_001",
        )
        self.assertEqual(result.report.capture, current)
        self.assertEqual(len(result.report.capture_sha256), 64)
        self.assertEqual(len(result.report.capture_file_sha256), 64)
        self.assertFalse(result.report.modifies_editor_scene)
        self.assertEqual(result.attempt_count, 1)
        model_input = client.requests[0]["messages"][1]["content"]
        self.assertIn("available_scopes", model_input)
        self.assertNotIn("trace_sha256", model_input)
        AssistantInsightsGpuReport.model_validate(
            result.report.model_dump(mode="json")
        )
        tampered = result.report.model_dump(mode="json")
        tampered["capture_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "capture hash"):
            AssistantInsightsGpuReport.model_validate(tampered)

    def test_capture_file_hash_uses_exact_artifact_bytes(self):
        expected = hashlib.sha256(example_path().read_bytes()).hexdigest()
        self.assertEqual(hash_insights_gpu_capture_file(example_path()), expected)

    def test_unknown_scope_id_gets_one_bounded_retry(self):
        invalid = review_json(
            primary_scope_id="scope_not_captured",
            findings=[
                {
                    "severity": "critical",
                    "category": "gpu_scope",
                    "evidence_scope_ids": ["scope_not_captured"],
                    "observation": "Unsupported scope.",
                    "recommendation": "Unsupported action.",
                }
            ],
        )
        client = FakeClient([invalid, review_json()])
        result = review_insights_gpu_capture(
            prompt="Review the GPU trace",
            capture=capture(),
            client=client,
            model="planner",
            report_id="insights_retry",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertIn("not in the capture", result.recovery_reason)

    def test_per_actor_request_can_be_unresolved(self):
        result = review_insights_gpu_capture(
            prompt="Which Actor caused Lumen to be expensive?",
            capture=capture(),
            client=FakeClient(
                review_json(
                    outcome="unresolved",
                    summary="The GPU scope trace does not contain per-Actor attribution.",
                    primary_scope_id=None,
                    findings=[],
                    missing_capabilities=["per-Actor GPU attribution"],
                )
            ),
            model="planner",
            report_id="insights_actor_gap",
        )
        self.assertEqual(result.report.status, "unresolved")
        self.assertEqual(result.report.findings, [])


if __name__ == "__main__":
    unittest.main()
