import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_lights import (
    LightProposalError,
    compile_light_intent,
    load_light_context,
    propose_light_change,
)
from render_master_bot.contracts import (
    AssistantLightProposal,
    LightEditIntent,
    UnrealLightContext,
)
from render_master_bot.ollama import StructuredResponse


class FakeClient:
    def __init__(self, contents):
        self.contents = iter(contents if isinstance(contents, list) else [contents])
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(content=next(self.contents), model="fake-light")


def light_context(kind="spot", **updates):
    light = {
        "rotation_deg": {"x": 0, "y": -20, "z": 10},
        "intensity": 5000,
        "intensity_unit": "lumens",
        "color_rgb": {"r": 1, "g": 1, "b": 1},
        "use_temperature": False,
        "temperature_kelvin": 6500,
        "cast_shadows": True,
        "attenuation_radius_cm": 1000,
        "inner_cone_deg": 20,
        "outer_cone_deg": 40,
    }
    if kind == "directional":
        light.update({
            "intensity": 100000,
            "intensity_unit": "lux",
            "attenuation_radius_cm": None,
            "inner_cone_deg": None,
            "outer_cone_deg": None,
        })
    elif kind in {"point", "rect"}:
        light.update({"inner_cone_deg": None, "outer_cone_deg": None})
    values = {
        "project_name": "OptimizationPlugin",
        "level_path": "/Game/FirstPerson/Lvl_FirstPerson",
        "actor_name": "KeyLight",
        "actor_path": "/Game/FirstPerson/Lvl_FirstPerson:PersistentLevel.KeyLight",
        "actor_class": f"{kind.title()}Light",
        "actor_guid": "0123456789ABCDEF0123456789ABCDEF",
        "component_name": "LightComponent0",
        "light_kind": kind,
        "component_mobility": "movable",
        "is_editable": True,
        "is_locked": False,
        "light": light,
    }
    values.update(updates)
    return UnrealLightContext.model_validate(values)


class AssistantLightTests(unittest.TestCase):
    def test_context_loader_and_type_specific_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "light.json"
            path.write_text(light_context().model_dump_json(), encoding="utf-8")
            self.assertEqual(load_light_context(path).light_kind, "spot")
            path.write_text('{"project_name":"P","extra":true}', encoding="utf-8")
            with self.assertRaisesRegex(LightProposalError, "invalid Unreal light"):
                load_light_context(path)

        with self.assertRaisesRegex(ValidationError, "only spot"):
            light_context("point", light={
                **light_context("point").light.model_dump(),
                "inner_cone_deg": 10,
                "outer_cone_deg": 20,
            })

    def test_host_computes_spot_light_changes(self):
        intent = LightEditIntent.model_validate({
            "outcome": "proposed",
            "intensity": {"operation": "multiply", "value": 1.2},
            "temperature_kelvin": 3200,
            "outer_cone_deg": {"operation": "add", "value": 5},
            "rotation": {"operation": "add", "z": 30},
            "rationale": "Make the selected key light brighter and warmer.",
        })
        after, changes = compile_light_intent(light_context(), intent)
        self.assertEqual(after.intensity, 6000)
        self.assertEqual(after.temperature_kelvin, 3200)
        self.assertTrue(after.use_temperature)
        self.assertEqual(after.outer_cone_deg, 45)
        self.assertEqual(after.rotation_deg.z, 40)
        self.assertEqual(
            [change.property for change in changes],
            ["intensity", "temperature_kelvin", "use_temperature", "outer_cone_deg", "rotation"],
        )

    def test_direct_color_disables_temperature_when_not_explicit(self):
        current = light_context()
        current = current.model_copy(update={
            "light": current.light.model_copy(update={"use_temperature": True})
        })
        intent = LightEditIntent.model_validate({
            "outcome": "proposed",
            "color_rgb": {"r": 0.2, "g": 0.4, "b": 1.0},
            "rationale": "Use a blue light color.",
        })
        after, changes = compile_light_intent(current, intent)
        self.assertFalse(after.use_temperature)
        self.assertEqual(after.color_rgb.b, 1.0)
        self.assertEqual(
            [change.property for change in changes],
            ["color_rgb", "use_temperature"],
        )

    def test_type_specific_and_numeric_safety_rejections(self):
        cone = LightEditIntent.model_validate({
            "outcome": "proposed",
            "outer_cone_deg": {"operation": "set", "value": 40},
            "rationale": "Change cone.",
        })
        with self.assertRaisesRegex(LightProposalError, "only spot"):
            compile_light_intent(light_context("point"), cone)

        rotation = LightEditIntent.model_validate({
            "outcome": "proposed",
            "rotation": {"operation": "add", "z": 20},
            "rationale": "Rotate point light.",
        })
        with self.assertRaisesRegex(LightProposalError, "no visual effect"):
            compile_light_intent(light_context("point"), rotation)

        unsafe = LightEditIntent.model_validate({
            "outcome": "proposed",
            "intensity": {"operation": "multiply", "value": 101},
            "rationale": "Unsafe brightness.",
        })
        with self.assertRaisesRegex(LightProposalError, "multiplier"):
            compile_light_intent(light_context(), unsafe)

    def test_spot_cone_order_and_locked_actor_are_rejected(self):
        invalid_cone = LightEditIntent.model_validate({
            "outcome": "proposed",
            "inner_cone_deg": {"operation": "set", "value": 60},
            "rationale": "Invalid cone.",
        })
        with self.assertRaisesRegex(LightProposalError, "incorrectly ordered"):
            compile_light_intent(light_context(), invalid_cone)

        valid = LightEditIntent.model_validate({
            "outcome": "proposed",
            "cast_shadows": False,
            "rationale": "Disable shadows.",
        })
        with self.assertRaisesRegex(LightProposalError, "locked"):
            compile_light_intent(light_context(is_locked=True), valid)

    def test_model_intent_becomes_host_owned_proposal(self):
        client = FakeClient("""{
          "outcome": "proposed",
          "intensity": {"operation": "multiply", "value": 1.2},
          "color_rgb": null,
          "use_temperature": null,
          "temperature_kelvin": 3200,
          "cast_shadows": null,
          "attenuation_radius_cm": {"operation": "preserve", "value": null},
          "inner_cone_deg": {"operation": "preserve", "value": null},
          "outer_cone_deg": {"operation": "preserve", "value": null},
          "rotation": {"operation": "preserve", "x": null, "y": null, "z": null},
          "rationale": "Increase brightness and use a warmer temperature.",
          "missing_capabilities": []
        }""")
        result = propose_light_change(
            prompt="Make this light 20% brighter and warmer at 3200K",
            context=light_context(),
            client=client,
            model="planner",
            proposal_id="light_001",
        )
        self.assertEqual(result.proposal.after.intensity, 6000)
        self.assertTrue(result.proposal.after.use_temperature)
        self.assertEqual(result.proposal.before.intensity, 5000)
        self.assertFalse(result.proposal.auto_save)
        self.assertEqual(client.requests[0]["json_schema"]["title"], "LightEditIntent")

    def test_unresolved_has_no_executable_after_state(self):
        client = FakeClient("""{
          "outcome": "unresolved",
          "intensity": {"operation": "preserve", "value": null},
          "color_rgb": null,
          "use_temperature": null,
          "temperature_kelvin": null,
          "cast_shadows": null,
          "attenuation_radius_cm": {"operation": "preserve", "value": null},
          "inner_cone_deg": {"operation": "preserve", "value": null},
          "outer_cone_deg": {"operation": "preserve", "value": null},
          "rotation": {"operation": "preserve", "x": null, "y": null, "z": null},
          "rationale": "The request needs camera exposure evidence.",
          "missing_capabilities": ["camera exposure editing"]
        }""")
        result = propose_light_change(
            prompt="Fix the exposure with this light",
            context=light_context(),
            client=client,
            model="planner",
            proposal_id="light_002",
        )
        self.assertEqual(result.proposal.status, "unresolved")
        self.assertIsNone(result.proposal.after)

    def test_unsafe_first_output_gets_one_retry(self):
        client = FakeClient([
            """{"outcome":"proposed","outer_cone_deg":{"operation":"set","value":95},"rationale":"Too wide."}""",
            """{"outcome":"proposed","outer_cone_deg":{"operation":"set","value":50},"rationale":"Widen safely."}""",
        ])
        result = propose_light_change(
            prompt="Widen this spot light to 50 degrees",
            context=light_context(),
            client=client,
            model="planner",
            proposal_id="light_003",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.proposal.after.outer_cone_deg, 50)

    def test_proposal_contract_rejects_tampered_change_list(self):
        client = FakeClient("""{
          "outcome": "proposed",
          "intensity": {"operation": "multiply", "value": 2},
          "rationale": "Double intensity."
        }""")
        proposal = propose_light_change(
            prompt="Double this light",
            context=light_context(),
            client=client,
            model="planner",
            proposal_id="light_004",
        ).proposal
        payload = proposal.model_dump(mode="json")
        payload["changes"][0]["property"] = "cast_shadows"
        payload["changes"][0]["operation"] = "set"
        with self.assertRaisesRegex(ValidationError, "every changed property"):
            AssistantLightProposal.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
