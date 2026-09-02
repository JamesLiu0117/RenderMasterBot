import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_level_audits import (
    LevelAuditError,
    compile_level_audit_intent,
    load_level_audit_context,
    review_level_audit,
)
from render_master_bot.contracts import (
    AssistantLevelAuditReport,
    LevelAuditReviewIntent,
    UnrealLevelAuditContext,
    build_level_audit_assessments,
    build_level_audit_statistics,
)
from render_master_bot.ollama import StructuredResponse


class FakeClient:
    def __init__(self, contents):
        self.contents = iter(contents if isinstance(contents, list) else [contents])
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(
            content=next(self.contents), model="fake-level-audit-model"
        )


def example_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "unreal_level_audit_context.json"
    )


def context() -> UnrealLevelAuditContext:
    return UnrealLevelAuditContext.model_validate_json(
        example_path().read_text(encoding="utf-8")
    )


def review_json(**updates) -> str:
    value = {
        "schema_version": "0.1",
        "outcome": "review_only",
        "summary": (
            "The deterministic static audit prioritizes HeroStatue for runtime "
            "confirmation before any asset-level optimization."
        ),
        "primary_assessment_id": "audit_actor_0000",
        "findings": [
            {
                "severity": "warning",
                "category": "measurement",
                "evidence_assessment_ids": ["audit_actor_0000"],
                "observation": (
                    "The host score ranks this Actor first, but does not measure "
                    "runtime GPU cost."
                ),
                "recommendation": (
                    "Run the repeatable selected-Actor GPU study under a stable view."
                ),
            }
        ],
        "actions": [],
        "missing_capabilities": [],
    }
    value.update(updates)
    return json.dumps(value)


class AssistantLevelAuditTests(unittest.TestCase):
    def test_example_and_deterministic_statistics(self):
        current = load_level_audit_context(example_path())
        statistics = build_level_audit_statistics(current)
        self.assertEqual(statistics.captured_actor_count, 3)
        self.assertEqual(statistics.excluded_actor_count, 1)
        self.assertEqual(statistics.total_lod0_triangles, 2_611_000)
        self.assertEqual(statistics.high_priority_actor_count, 2)
        assessments = build_level_audit_assessments(current)
        self.assertEqual(
            [item.actor_path.rsplit(".", 1)[-1] for item in assessments],
            ["HeroStatue", "BackgroundRock_A", "BackgroundRock_B"],
        )
        self.assertEqual(assessments[0].risk_score, 100)
        self.assertEqual(assessments[0].risk_band, "critical")
        self.assertEqual(
            assessments[0].recommended_next_test,
            "repeatable_actor_gpu_study",
        )

    def test_context_rejects_count_and_identity_tampering(self):
        value = json.loads(example_path().read_text(encoding="utf-8"))
        value["static_mesh_actor_count"] = 99
        with self.assertRaisesRegex(ValidationError, "counts"):
            UnrealLevelAuditContext.model_validate(value)
        value = json.loads(example_path().read_text(encoding="utf-8"))
        value["actors"].append(value["actors"][0])
        value["static_mesh_actor_count"] += 1
        with self.assertRaisesRegex(ValidationError, "repeat an Actor path"):
            UnrealLevelAuditContext.model_validate(value)

    def test_loader_rejects_invalid_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(LevelAuditError, "invalid Unreal level audit"):
                load_level_audit_context(path)

    def test_review_preserves_full_context_but_bounds_model_evidence(self):
        current = context()
        client = FakeClient(review_json())
        result = review_level_audit(
            prompt="Audit this level and prioritize evidence-backed next tests.",
            context=current,
            client=client,
            model="planner",
        )
        self.assertEqual(result.report.context, current)
        self.assertEqual(
            result.report.assessments, build_level_audit_assessments(current)
        )
        self.assertEqual(result.report.status, "review_only")
        self.assertFalse(result.report.modifies_editor_scene)
        model_input = client.requests[0]["messages"][1]["content"]
        self.assertIn("available_assessments", model_input)
        self.assertIn("HeroStatue", model_input)
        self.assertNotIn("actor_path", model_input)
        self.assertNotIn("actor_guid", model_input)
        AssistantLevelAuditReport.model_validate(
            result.report.model_dump(mode="json")
        )

    def test_explicit_safe_action_compiles_exact_before_after(self):
        current = context()
        assessments = build_level_audit_assessments(current)
        intent = LevelAuditReviewIntent.model_validate(
            {
                "outcome": "proposed",
                "summary": "Apply the explicitly requested background-rock bounds.",
                "primary_assessment_id": "audit_actor_0001",
                "findings": [],
                "actions": [
                    {
                        "assessment_id": "audit_actor_0001",
                        "cast_shadow": False,
                        "max_draw_distance_cm": 10000,
                        "rationale": "The user explicitly requested both settings.",
                    }
                ],
                "missing_capabilities": [],
            }
        )
        actions = compile_level_audit_intent(current, assessments, intent)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].target.actor_name, "BackgroundRock_A")
        self.assertTrue(actions[0].before.cast_shadow)
        self.assertFalse(actions[0].after.cast_shadow)
        self.assertEqual(actions[0].after.max_draw_distance_cm, 10000)
        self.assertEqual(
            [change.property for change in actions[0].changes],
            ["cast_shadow", "max_draw_distance_cm"],
        )

    def test_unknown_assessment_gets_one_bounded_retry(self):
        invalid = review_json(primary_assessment_id="audit_actor_9999")
        client = FakeClient([invalid, review_json()])
        result = review_level_audit(
            prompt="Audit the whole level.",
            context=context(),
            client=client,
            model="planner",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertIn("not shown", result.recovery_reason)

    def test_noop_action_gets_one_bounded_retry(self):
        invalid = review_json(
            outcome="proposed",
            findings=[],
            actions=[
                {
                    "assessment_id": "audit_actor_0001",
                    "cast_shadow": True,
                    "rationale": "This incorrectly preserves the captured value.",
                }
            ],
        )
        client = FakeClient([invalid, review_json()])
        result = review_level_audit(
            prompt="Audit and propose only observable safe changes.",
            context=context(),
            client=client,
            model="planner",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertIn("no observable change", result.recovery_reason)

    def test_review_only_model_actions_are_discarded_conservatively(self):
        result = review_level_audit(
            prompt="Audit only; do not change the level.",
            context=context(),
            client=FakeClient(
                review_json(
                    actions=[
                        {
                            "assessment_id": "audit_actor_0001",
                            "cast_shadow": False,
                            "rationale": "This action must not survive review-only mode.",
                        }
                    ]
                )
            ),
            model="planner",
        )
        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(result.report.status, "review_only")
        self.assertEqual(result.report.actions, [])
        self.assertIn("discarded model-authored actions", result.recovery_reason)

    def test_report_rejects_tampered_statistics_and_assessments(self):
        result = review_level_audit(
            prompt="Audit the level.",
            context=context(),
            client=FakeClient(review_json()),
            model="planner",
        )
        value = result.report.model_dump(mode="json")
        value["statistics"]["total_lod0_triangles"] += 1
        with self.assertRaisesRegex(ValidationError, "statistics are not recomputable"):
            AssistantLevelAuditReport.model_validate(value)
        value = result.report.model_dump(mode="json")
        value["assessments"][0]["risk_score"] = 0
        with self.assertRaisesRegex(ValidationError, "assessments are not recomputable"):
            AssistantLevelAuditReport.model_validate(value)

    def test_unsupported_execution_can_be_unresolved(self):
        result = review_level_audit(
            prompt="Automatically generate LODs for every Actor.",
            context=context(),
            client=FakeClient(
                review_json(
                    outcome="unresolved",
                    summary="LOD generation is not an executable level-audit action.",
                    primary_assessment_id=None,
                    findings=[],
                    actions=[],
                    missing_capabilities=["asset-level LOD generation"],
                )
            ),
            model="planner",
        )
        self.assertEqual(result.report.status, "unresolved")
        self.assertEqual(result.report.actions, [])


if __name__ == "__main__":
    unittest.main()
