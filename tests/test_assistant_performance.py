import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_performance import (
    PerformanceProposalError,
    compile_performance_intent,
    load_performance_selection_context,
    propose_performance_review,
)
from render_master_bot.contracts import (
    AssistantPerformanceProposal,
    PerformanceReviewIntent,
    UnrealPerformanceSelectionContext,
    UnrealStaticMeshPerformanceContext,
)
from render_master_bot.ollama import StructuredResponse


class FakeClient:
    def __init__(self, contents):
        self.contents = iter(contents if isinstance(contents, list) else [contents])
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(
            content=next(self.contents), model="fake-performance-model"
        )


def actor_context(name, *, triangles=100_000, lods=1, nanite=False, shadow=True):
    return UnrealStaticMeshPerformanceContext.model_validate({
        "project_name": "OptimizationPlugin",
        "level_path": "/Game/Maps/PerformanceTest",
        "actor_name": name,
        "actor_path": f"/Game/Maps/PerformanceTest:PersistentLevel.{name}",
        "actor_class": "StaticMeshActor",
        "actor_guid": f"{sum(map(ord, name)):032X}",
        "component_name": "StaticMeshComponent0",
        "component_mobility": "static",
        "is_editable": True,
        "is_locked": False,
        "mesh_path": f"/Game/Test/{name}.{name}",
        "lod_count": lods,
        "lod0_triangles": triangles,
        "material_slot_count": 3,
        "nanite_enabled": nanite,
        "collision_mode": "query_and_physics",
        "component_tick_enabled": False,
        "bounds_radius_cm": 250,
        "performance": {
            "cast_shadow": shadow,
            "max_draw_distance_cm": 0,
        },
    })


def selection(*actors):
    return UnrealPerformanceSelectionContext(
        project_name="OptimizationPlugin",
        level_path="/Game/Maps/PerformanceTest",
        actors=list(actors),
    )


def intent_json(**updates):
    value = {
        "schema_version": "0.1",
        "outcome": "review_only",
        "summary": "The selected meshes contain measurable optimization candidates.",
        "findings": [{
            "actor_path": "/Game/Maps/PerformanceTest:PersistentLevel.RockA",
            "severity": "warning",
            "category": "geometry",
            "evidence_fields": ["lod0_triangles", "lod_count", "nanite_enabled"],
            "recommendation": "Review Nanite or authored LODs for this dense mesh.",
        }],
        "actions": [],
        "missing_capabilities": [],
    }
    value.update(updates)
    return json.dumps(value)


class AssistantPerformanceTests(unittest.TestCase):
    def test_repository_selection_example_validates(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "examples"
            / "unreal_performance_selection_context.json"
        )
        loaded = load_performance_selection_context(path)
        self.assertEqual(len(loaded.actors), 2)
        self.assertEqual(loaded.actors[0].lod0_triangles, 182400)

    def test_selection_requires_unique_ordered_actors(self):
        first = actor_context("RockA")
        second = actor_context("RockB")
        self.assertEqual([a.actor_name for a in selection(first, second).actors], ["RockA", "RockB"])
        with self.assertRaisesRegex(ValidationError, "repeat an Actor path"):
            selection(first, first)

    def test_loader_rejects_invalid_context(self):
        current = selection(actor_context("RockA"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "context.json"
            path.write_text(current.model_dump_json(), encoding="utf-8")
            self.assertEqual(load_performance_selection_context(path), current)
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                PerformanceProposalError, "invalid Unreal performance selection"
            ):
                load_performance_selection_context(path)

    def test_compiler_preserves_selection_order_and_explicit_noops(self):
        current = selection(actor_context("RockA"), actor_context("RockB", shadow=False))
        intent = PerformanceReviewIntent.model_validate({
            "outcome": "proposed",
            "summary": "Disable background-prop shadows and add distance culling.",
            "actions": [{
                "actor_path": current.actors[0].actor_path,
                "cast_shadow": False,
                "max_draw_distance_cm": 10000,
                "rationale": "The user explicitly identified this as a background prop.",
            }],
        })
        actions = compile_performance_intent(current, intent)
        self.assertEqual([action.target.actor_name for action in actions], ["RockA", "RockB"])
        self.assertEqual(
            [change.property for change in actions[0].changes],
            ["cast_shadow", "max_draw_distance_cm"],
        )
        self.assertEqual(actions[1].changes, [])
        self.assertEqual(actions[1].before, actions[1].after)

    def test_review_only_keeps_diagnosis_read_only(self):
        current = selection(actor_context("RockA"))
        result = propose_performance_review(
            prompt="Analyze the selected mesh performance evidence",
            selection=current,
            client=FakeClient(intent_json()),
            model="planner",
            proposal_id="performance_review_001",
        )
        self.assertEqual(result.proposal.status, "review_only")
        self.assertFalse(result.proposal.modifies_editor_scene)
        self.assertEqual(result.proposal.actions, [])
        self.assertEqual(result.attempt_count, 1)

    def test_clean_review_can_report_no_findings_without_inventing_a_problem(self):
        current = selection(actor_context("RockA", triangles=1200, lods=4, nanite=True))
        result = propose_performance_review(
            prompt="Review the selected mesh without inventing a problem",
            selection=current,
            client=FakeClient(intent_json(
                summary="No obvious risk is present in the captured static evidence.",
                findings=[],
            )),
            model="planner",
            proposal_id="performance_clean_review",
        )
        self.assertEqual(result.proposal.status, "review_only")
        self.assertEqual(result.proposal.findings, [])
        self.assertEqual(result.proposal.actions, [])

    def test_executable_proposal_has_exact_before_after_evidence(self):
        current = selection(actor_context("RockA"), actor_context("RockB"))
        result = propose_performance_review(
            prompt="Set both selected background meshes to stop drawing beyond 100 meters",
            selection=current,
            client=FakeClient(intent_json(
                outcome="proposed",
                findings=[],
                actions=[{
                    "actor_path": actor.actor_path,
                    "cast_shadow": None,
                    "max_draw_distance_cm": 10000,
                    "rationale": "The user supplied the exact background cull distance.",
                } for actor in current.actors],
            )),
            model="planner",
            proposal_id="performance_action_001",
        )
        self.assertTrue(result.proposal.modifies_editor_scene)
        self.assertTrue(all(action.after.max_draw_distance_cm == 10000 for action in result.proposal.actions))
        self.assertTrue(all(action.before == action.target.performance for action in result.proposal.actions))

    def test_unknown_actor_gets_one_bounded_retry(self):
        current = selection(actor_context("RockA"))
        client = FakeClient([
            intent_json(findings=[{
                "actor_path": "/Game/Maps/PerformanceTest:PersistentLevel.Unknown",
                "severity": "warning",
                "category": "geometry",
                "evidence_fields": ["lod0_triangles"],
                "recommendation": "Invalid target.",
            }]),
            intent_json(),
        ])
        result = propose_performance_review(
            prompt="Review the selection",
            selection=current,
            client=client,
            model="planner",
            proposal_id="performance_retry",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(len(client.requests), 2)

    def test_unsupported_runtime_profile_request_is_unresolved(self):
        current = selection(actor_context("RockA"))
        result = propose_performance_review(
            prompt="Tell me the GPU time and memory for this mesh",
            selection=current,
            client=FakeClient(intent_json(
                outcome="unresolved",
                summary="Runtime timing is not present in static selection evidence.",
                findings=[],
                actions=[],
                missing_capabilities=["runtime GPU and memory profiler capture"],
            )),
            model="planner",
            proposal_id="performance_gap",
        )
        self.assertEqual(result.proposal.status, "unresolved")
        self.assertFalse(result.proposal.modifies_editor_scene)

    def test_tampered_action_order_is_rejected(self):
        current = selection(actor_context("RockA"), actor_context("RockB"))
        result = propose_performance_review(
            prompt="Disable shadows on the selected background meshes",
            selection=current,
            client=FakeClient(intent_json(
                outcome="proposed",
                findings=[],
                actions=[{
                    "actor_path": actor.actor_path,
                    "cast_shadow": False,
                    "max_draw_distance_cm": None,
                    "rationale": "Explicit user request for selected background meshes.",
                } for actor in current.actors],
            )),
            model="planner",
            proposal_id="performance_order",
        ).proposal.model_dump(mode="json")
        result["actions"].reverse()
        with self.assertRaisesRegex(ValidationError, "preserve selection order"):
            AssistantPerformanceProposal.model_validate(result)


if __name__ == "__main__":
    unittest.main()
