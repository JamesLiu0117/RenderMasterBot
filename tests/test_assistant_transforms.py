import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_transforms import (
    MAX_LOCATION_DELTA_CM,
    TransformProposalError,
    compile_transform_intent,
    load_transform_context,
    propose_transform_change,
)
from render_master_bot.contracts import (
    AssistantTransformProposal,
    TransformEditIntent,
    UnrealActorTransformContext,
)
from render_master_bot.ollama import StructuredResponse


class FakeClient:
    def __init__(self, contents):
        self.contents = iter(contents if isinstance(contents, list) else [contents])
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(content=next(self.contents), model="fake-transform")


def context(**updates):
    values = {
        "project_name": "OptimizationPlugin",
        "level_path": "/Game/FirstPerson/Lvl_FirstPerson",
        "actor_name": "TestCube",
        "actor_path": "/Game/FirstPerson/Lvl_FirstPerson:PersistentLevel.TestCube",
        "actor_class": "StaticMeshActor",
        "actor_guid": "0123456789ABCDEF0123456789ABCDEF",
        "root_component_name": "StaticMeshComponent0",
        "root_mobility": "static",
        "is_editable": True,
        "is_locked": False,
        "transform": {
            "location_cm": {"x": 100, "y": 200, "z": 300},
            "rotation_deg": {"x": 0, "y": 0, "z": 10},
            "scale": {"x": 1, "y": 1, "z": 1},
        },
    }
    values.update(updates)
    return UnrealActorTransformContext.model_validate(values)


class AssistantTransformTests(unittest.TestCase):
    def test_context_loader_rejects_unknown_or_non_finite_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "context.json"
            path.write_text(context().model_dump_json(), encoding="utf-8")
            loaded = load_transform_context(path)
            self.assertEqual(loaded.actor_name, "TestCube")

            path.write_text('{"project_name":"Project","extra":true}', encoding="utf-8")
            with self.assertRaisesRegex(TransformProposalError, "invalid Unreal"):
                load_transform_context(path)

    def test_host_computes_world_before_after_and_changed_axes(self):
        intent = TransformEditIntent.model_validate({
            "outcome": "proposed",
            "location": {"operation": "add", "z": 50},
            "rotation": {"operation": "add", "z": 30},
            "scale": {"operation": "multiply", "z": 2},
            "rationale": "Move, turn, and make the Actor taller.",
        })

        after, changes = compile_transform_intent(context(), intent)

        self.assertEqual(after.location_cm.z, 350)
        self.assertEqual(after.rotation_deg.z, 40)
        self.assertEqual(after.scale.z, 2)
        self.assertEqual([change.channel for change in changes], [
            "location", "rotation", "scale"
        ])
        self.assertEqual(changes[0].axes, ["z"])

    def test_host_rejects_unsupported_operation_and_unsafe_delta(self):
        unsupported = TransformEditIntent.model_validate({
            "outcome": "proposed",
            "location": {"operation": "multiply", "x": 2},
            "rationale": "Unsafe interpretation.",
        })
        with self.assertRaisesRegex(TransformProposalError, "does not support"):
            compile_transform_intent(context(), unsupported)

        oversized = TransformEditIntent.model_validate({
            "outcome": "proposed",
            "location": {"operation": "add", "x": MAX_LOCATION_DELTA_CM + 1},
            "rationale": "Unsafe translation.",
        })
        with self.assertRaisesRegex(TransformProposalError, "exceeds"):
            compile_transform_intent(context(), oversized)

    def test_locked_actor_is_rejected_before_proposal(self):
        intent = TransformEditIntent.model_validate({
            "outcome": "proposed",
            "location": {"operation": "add", "z": 50},
            "rationale": "Move upward.",
        })
        with self.assertRaisesRegex(TransformProposalError, "locked"):
            compile_transform_intent(context(is_locked=True), intent)

    def test_model_intent_becomes_host_owned_proposal(self):
        client = FakeClient("""{
          "outcome": "proposed",
          "coordinate_space": "world",
          "location": {"operation": "add", "z": 50},
          "rotation": {"operation": "preserve"},
          "scale": {"operation": "preserve"},
          "rationale": "Move the selected Actor upward in world space.",
          "missing_capabilities": []
        }""")

        result = propose_transform_change(
            prompt="Move this object up by 50 cm",
            context=context(),
            client=client,
            model="planner",
            proposal_id="transform_001",
        )

        self.assertEqual(result.proposal.status, "proposed")
        self.assertEqual(result.proposal.target.actor_path, context().actor_path)
        self.assertEqual(result.proposal.before.location_cm.z, 300)
        self.assertEqual(result.proposal.after.location_cm.z, 350)
        self.assertFalse(result.proposal.auto_save)
        self.assertTrue(result.proposal.undo_supported)
        self.assertEqual(client.requests[0]["json_schema"]["title"], "TransformEditIntent")

    def test_unresolved_intent_has_no_executable_after_value(self):
        client = FakeClient("""{
          "outcome": "unresolved",
          "coordinate_space": "world",
          "location": {"operation": "preserve"},
          "rotation": {"operation": "preserve"},
          "scale": {"operation": "preserve"},
          "rationale": "The request asks for a geometry-aware placement.",
          "missing_capabilities": ["geometry-aware placement"]
        }""")
        result = propose_transform_change(
            prompt="Place this exactly on top of the table",
            context=context(),
            client=client,
            model="planner",
            proposal_id="transform_002",
        )
        self.assertEqual(result.proposal.status, "unresolved")
        self.assertIsNone(result.proposal.after)
        self.assertEqual(result.proposal.changes, [])

    def test_invalid_or_unsafe_model_output_gets_one_retry(self):
        client = FakeClient([
            """{
              "outcome": "proposed",
              "location": {"operation": "multiply", "x": 2},
              "rationale": "Invalid location operation."
            }""",
            """{
              "outcome": "proposed",
              "location": {"operation": "add", "x": 25},
              "rationale": "Move forward 25 cm."
            }""",
        ])
        result = propose_transform_change(
            prompt="Move forward 25 cm",
            context=context(),
            client=client,
            model="planner",
            proposal_id="transform_003",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.proposal.after.location_cm.x, 125)
        self.assertIn("does not support", client.requests[1]["messages"][-1]["content"])

    def test_proposal_contract_rejects_model_owned_before_value(self):
        client = FakeClient("""{
          "outcome": "proposed",
          "location": {"operation": "add", "z": 50},
          "rationale": "Move upward."
        }""")
        proposal = propose_transform_change(
            prompt="Move up",
            context=context(),
            client=client,
            model="planner",
            proposal_id="transform_004",
        ).proposal
        payload = proposal.model_dump(mode="json")
        payload["before"]["location_cm"]["z"] = 0
        with self.assertRaisesRegex(ValidationError, "before value"):
            AssistantTransformProposal.model_validate(payload)

    def test_proposal_contract_rejects_tampered_change_evidence(self):
        client = FakeClient("""{
          "outcome": "proposed",
          "location": {"operation": "add", "z": 50},
          "rationale": "Move upward."
        }""")
        proposal = propose_transform_change(
            prompt="Move up",
            context=context(),
            client=client,
            model="planner",
            proposal_id="transform_005",
        ).proposal
        payload = proposal.model_dump(mode="json")
        payload["changes"][0]["axes"] = ["x"]
        with self.assertRaisesRegex(ValidationError, "observable differences"):
            AssistantTransformProposal.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
