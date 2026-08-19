import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_runtime_performance import (
    RuntimePerformanceReviewError,
    load_runtime_performance_capture,
    review_runtime_performance,
)
from render_master_bot.contracts import (
    AssistantRuntimePerformanceReport,
    UnrealRuntimePerformanceCapture,
)
from render_master_bot.ollama import StructuredResponse


class FakeClient:
    def __init__(self, contents):
        self.contents = iter(contents if isinstance(contents, list) else [contents])
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(
            content=next(self.contents), model="fake-runtime-performance-model"
        )


def example_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "unreal_runtime_performance_capture.json"
    )


def capture() -> UnrealRuntimePerformanceCapture:
    return load_runtime_performance_capture(example_path())


def review_json(**updates):
    value = {
        "schema_version": "0.1",
        "outcome": "review_complete",
        "summary": "The capture has occasional frame-budget misses while GPU p95 remains below the target budget.",
        "primary_bottleneck": "gpu",
        "findings": [
            {
                "severity": "warning",
                "category": "frame_pacing",
                "evidence_fields": [
                    "target_frame_ms",
                    "frame_time.p95_ms",
                    "frame_budget_miss_fraction",
                    "gpu.p95_ms",
                ],
                "observation": "Frame p95 exceeds the 60 FPS target while the measured GPU p95 is lower.",
                "recommendation": "Capture a longer representative path and inspect intermittent frame spikes.",
            }
        ],
        "missing_capabilities": [],
    }
    value.update(updates)
    return json.dumps(value)


class AssistantRuntimePerformanceTests(unittest.TestCase):
    def test_repository_capture_recomputes_and_validates(self):
        current = capture()
        self.assertEqual(current.sample_count, 30)
        self.assertAlmostEqual(current.frame_time.p95_ms, 22.0)
        self.assertEqual(current.largest_measured_component, "gpu")
        self.assertFalse(current.rhi_thread.available)

    def test_loader_rejects_tampered_host_summary(self):
        value = json.loads(example_path().read_text(encoding="utf-8"))
        value["frame_time"]["p95_ms"] = 24.0
        with self.assertRaisesRegex(ValidationError, "summary does not match"):
            UnrealRuntimePerformanceCapture.model_validate(value)

    def test_loader_reports_invalid_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                RuntimePerformanceReviewError,
                "invalid Unreal runtime performance capture",
            ):
                load_runtime_performance_capture(path)

    def test_complete_review_preserves_exact_capture_and_is_read_only(self):
        current = capture()
        client = FakeClient(review_json())
        result = review_runtime_performance(
            prompt="Diagnose this runtime capture against 60 FPS",
            capture=current,
            client=client,
            model="planner",
            report_id="runtime_review_001",
        )
        self.assertEqual(result.report.capture, current)
        self.assertEqual(len(result.report.capture_sha256), 64)
        self.assertFalse(result.report.modifies_editor_scene)
        self.assertEqual(result.attempt_count, 1)
        model_input = client.requests[0]["messages"][1]["content"]
        self.assertIn("unavailable_evidence_fields", model_input)
        self.assertNotIn('"samples"', model_input)
        AssistantRuntimePerformanceReport.model_validate(
            result.report.model_dump(mode="json")
        )

    def test_clean_capture_can_return_no_findings(self):
        result = review_runtime_performance(
            prompt="Review without inventing a problem",
            capture=capture(),
            client=FakeClient(
                review_json(
                    summary="No decisive frame-level issue is supported by this short capture.",
                    primary_bottleneck="inconclusive",
                    findings=[],
                )
            ),
            model="planner",
            report_id="runtime_clean_review",
        )
        self.assertEqual(result.report.findings, [])
        self.assertEqual(result.report.primary_bottleneck, "inconclusive")

    def test_unavailable_rhi_evidence_gets_one_bounded_retry(self):
        invalid = review_json(
            primary_bottleneck="rhi_thread",
            findings=[
                {
                    "severity": "critical",
                    "category": "rhi_thread",
                    "evidence_fields": ["rhi_thread.p95_ms"],
                    "observation": "Unsupported claim.",
                    "recommendation": "Unsupported recommendation.",
                }
            ],
        )
        client = FakeClient([invalid, review_json()])
        result = review_runtime_performance(
            prompt="Review the capture",
            capture=capture(),
            client=client,
            model="planner",
            report_id="runtime_retry",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(len(client.requests), 2)
        self.assertIn("unavailable rhi_thread", result.recovery_reason)

    def test_finding_category_requires_matching_evidence(self):
        invalid = review_json(
            findings=[
                {
                    "severity": "warning",
                    "category": "gpu",
                    "evidence_fields": ["frame_time.p95_ms"],
                    "observation": "This category is not supported by its citation.",
                    "recommendation": "Capture matching evidence.",
                }
            ]
        )
        client = FakeClient([invalid, review_json()])
        result = review_runtime_performance(
            prompt="Review the capture",
            capture=capture(),
            client=client,
            model="planner",
            report_id="runtime_category_retry",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertIn("does not cite same-category", result.recovery_reason)

    def test_unresolved_requires_concrete_missing_evidence(self):
        result = review_runtime_performance(
            prompt="Tell me which GPU pass is slow",
            capture=capture(),
            client=FakeClient(
                review_json(
                    outcome="unresolved",
                    summary="Per-pass timing is not present in this capture.",
                    primary_bottleneck="inconclusive",
                    findings=[],
                    missing_capabilities=["per-pass GPU timing capture"],
                )
            ),
            model="planner",
            report_id="runtime_gap",
        )
        self.assertEqual(result.report.status, "unresolved")
        self.assertEqual(result.report.findings, [])


if __name__ == "__main__":
    unittest.main()
