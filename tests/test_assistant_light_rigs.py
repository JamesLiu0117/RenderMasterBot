import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_light_rigs import (
    LightingRigProposalError,
    compile_lighting_rig_intent,
    load_lighting_rig_context,
    propose_lighting_rig,
)
from render_master_bot.contracts import (
    AssistantLightingRigProposal,
    LightingRigIntent,
    UnrealLightingRigContext,
)
from render_master_bot.ollama import StructuredResponse


EXAMPLE = Path(__file__).parents[1] / "examples" / "unreal_lighting_rig_context.json"


class FakeClient:
    def __init__(self, contents):
        self.contents = iter(contents if isinstance(contents, list) else [contents])
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(content=next(self.contents), model="fake-rig")


def rig_context() -> UnrealLightingRigContext:
    return load_lighting_rig_context(EXAMPLE)


def proposed_intent(**updates) -> LightingRigIntent:
    context = rig_context()
    values = {
        "outcome": "proposed",
        "assignments": [
            {"actor_path": context.lights[0].target.actor_path, "role": "key"},
            {"actor_path": context.lights[1].target.actor_path, "role": "fill"},
            {"actor_path": context.lights[2].target.actor_path, "role": "rim"},
        ],
        "contrast": "balanced",
        "palette": "warm_cool",
        "key_side": "camera_left",
        "spacing": "standard",
        "brightness": "balanced",
        "rationale": "Build a bounded warm-key, cool-rim studio rig.",
        "missing_capabilities": [],
    }
    values.update(updates)
    return LightingRigIntent.model_validate(values)


def intent_json(**updates) -> str:
    return proposed_intent(**updates).model_dump_json()


class AssistantLightingRigTests(unittest.TestCase):
    def test_example_loads_and_contracts_require_compatible_local_lights(self):
        context = rig_context()
        self.assertEqual(len(context.lights), 3)
        self.assertEqual(context.camera.camera_kind, "cine_camera")

        payload = context.model_dump(mode="json")
        payload["lights"][1]["target"]["light"]["intensity_unit"] = "candelas"
        with self.assertRaisesRegex(ValidationError, "shared non-EV"):
            UnrealLightingRigContext.model_validate(payload)

        payload = context.model_dump(mode="json")
        payload["lights"][1]["target"]["component_mobility"] = "stationary"
        with self.assertRaisesRegex(ValidationError, "Movable"):
            UnrealLightingRigContext.model_validate(payload)

    def test_host_builds_key_fill_rim_from_camera_and_subject_evidence(self):
        actions = compile_lighting_rig_intent(rig_context(), proposed_intent())
        self.assertEqual([action.role for action in actions], ["key", "fill", "rim"])

        key, fill, rim = actions
        self.assertLess(key.after.location_cm.y, 0.0)
        self.assertGreater(fill.after.location_cm.y, 0.0)
        self.assertGreater(rim.after.location_cm.x, 0.0)
        self.assertEqual(key.after.light.intensity, 5000.0)
        self.assertEqual(fill.after.light.intensity, 2000.0)
        self.assertEqual(rim.after.light.intensity, 3250.0)
        self.assertEqual(key.after.light.temperature_kelvin, 4200.0)
        self.assertEqual(fill.after.light.temperature_kelvin, 5000.0)
        self.assertEqual(rim.after.light.temperature_kelvin, 7000.0)
        self.assertTrue(all(action.after.light.use_temperature for action in actions))
        self.assertEqual(rim.after.light.rotation_deg, rim.before.light.rotation_deg)
        self.assertIn("location", [change.property for change in key.changes])
        self.assertIn("rotation", [change.property for change in key.changes])

    def test_camera_right_flips_key_and_fill_sides(self):
        actions = compile_lighting_rig_intent(
            rig_context(),
            proposed_intent(key_side="camera_right", palette="preserve"),
        )
        self.assertGreater(actions[0].after.location_cm.y, 0.0)
        self.assertLess(actions[1].after.location_cm.y, 0.0)
        self.assertFalse(actions[0].after.light.use_temperature)

    def test_host_rejects_invented_path_and_camera_facing_away(self):
        intent = proposed_intent()
        values = intent.model_dump(mode="json")
        values["assignments"][0]["actor_path"] = "/Game/Invented.Light"
        invented = LightingRigIntent.model_validate(values)
        with self.assertRaisesRegex(LightingRigProposalError, "invented"):
            compile_lighting_rig_intent(rig_context(), invented)

        payload = rig_context().model_dump(mode="json")
        payload["camera"]["rotation_deg"] = {"x": 0, "y": 0, "z": 180}
        away = UnrealLightingRigContext.model_validate(payload)
        with self.assertRaisesRegex(LightingRigProposalError, "not facing"):
            compile_lighting_rig_intent(away, proposed_intent())

    def test_model_output_becomes_host_owned_rig_proposal(self):
        context = rig_context()
        client = FakeClient(intent_json())
        result = propose_lighting_rig(
            prompt="Create a warm cinematic three-point rig around this subject",
            context=context,
            client=client,
            model="planner",
            proposal_id="rig_001",
        )
        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(result.proposal.proposed_by.model, "fake-rig")
        self.assertEqual(len(result.proposal.actions), 3)
        self.assertFalse(result.proposal.auto_save)
        self.assertEqual(
            client.requests[0]["json_schema"]["title"],
            "LightingRigIntent",
        )

        payload = result.proposal.model_dump(mode="json")
        payload["actions"][0]["target"] = payload["actions"][1]["target"]
        with self.assertRaisesRegex(ValidationError, "Before values|selection order"):
            AssistantLightingRigProposal.model_validate(payload)

    def test_invalid_first_assignment_gets_one_bounded_retry(self):
        invalid = json.loads(intent_json())
        invalid["assignments"][0]["actor_path"] = "/Game/Invented.Light"
        client = FakeClient([json.dumps(invalid), intent_json(contrast="dramatic")])
        result = propose_lighting_rig(
            prompt="Create dramatic three-point lighting",
            context=rig_context(),
            client=client,
            model="planner",
            proposal_id="rig_retry_001",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.proposal.contrast, "dramatic")
        self.assertIn("invented", client.requests[1]["messages"][-1]["content"])

    def test_unresolved_rig_preserves_context_and_has_no_actions(self):
        client = FakeClient(json.dumps({
            "outcome": "unresolved",
            "assignments": [],
            "contrast": "balanced",
            "palette": "preserve",
            "key_side": "camera_left",
            "spacing": "standard",
            "brightness": "balanced",
            "rationale": "The request needs a fourth background light.",
            "missing_capabilities": ["four-light role coordination"],
        }))
        result = propose_lighting_rig(
            prompt="Use Key, Fill, Rim, and a background light",
            context=rig_context(),
            client=client,
            model="planner",
            proposal_id="rig_gap_001",
        )
        self.assertEqual(result.proposal.status, "unresolved")
        self.assertEqual(result.proposal.actions, [])
        self.assertEqual(len(result.proposal.context.lights), 3)

    def test_loader_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rig.json"
            payload = rig_context().model_dump(mode="json")
            payload["unknown"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(LightingRigProposalError, "invalid Unreal"):
                load_lighting_rig_context(path)


if __name__ == "__main__":
    unittest.main()
