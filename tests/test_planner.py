import unittest

from render_master_bot.ollama import StructuredResponse
from render_master_bot.planner import PlanningError, ScenePlanner


class FakeClient:
    def __init__(self, content):
        self.content = content
        self.last_request = None
        self.call_count = 0

    def chat_structured(self, **kwargs):
        self.last_request = kwargs
        self.call_count += 1
        return StructuredResponse(content=self.content, model="fake")


class SequenceClient:
    def __init__(self, contents):
        self.contents = iter(contents)
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(content=next(self.contents), model="fake")


class PlannerTests(unittest.TestCase):
    def test_planner_validates_model_json(self):
        client = FakeClient("""{
          "schema_version": "0.1",
          "source_prompt": "test",
          "scene_name": "test_scene",
          "objects": [],
          "camera": {"camera_id": "main_camera", "transform": {}},
          "lights": [],
          "render": {},
          "notes": []
        }""")
        result = ScenePlanner(client).plan(model="fake", prompt="test")
        self.assertEqual(result.spec.scene_name, "test_scene")
        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(client.call_count, 1)
        self.assertEqual(client.last_request["json_schema"]["title"], "RenderSpec")

    def test_invalid_exposure_is_repaired_by_one_schema_guided_retry(self):
        invalid = """{
          "source_prompt": "test",
          "scene_name": "test_scene",
          "objects": [],
          "camera": {
            "camera_id": "main_camera",
            "transform": {},
            "exposure": {"fixed_ev100": 0}
          }
        }"""
        valid = """{
          "source_prompt": "test",
          "scene_name": "test_scene",
          "objects": [],
          "camera": {
            "camera_id": "main_camera",
            "transform": {},
            "exposure": {"mode": "auto"}
          }
        }"""
        client = SequenceClient([invalid, valid])

        result = ScenePlanner(client).plan(model="fake", prompt="test")

        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.spec.camera.exposure.mode, "auto")
        self.assertEqual(len(client.requests), 2)
        retry_messages = client.requests[1]["messages"]
        self.assertEqual(retry_messages[-2], {"role": "assistant", "content": invalid})
        self.assertIn(
            "auto exposure mode cannot specify fixed_ev100",
            retry_messages[-1]["content"],
        )

    def test_invalid_model_json_becomes_planning_error(self):
        client = FakeClient('{"scene_name": "missing_required_fields"}')
        with self.assertRaises(PlanningError) as raised:
            ScenePlanner(client).plan(model="fake", prompt="test")
        self.assertIsNotNone(raised.exception.response)

    def test_assets_outside_catalog_are_rejected(self):
        client = FakeClient("""{
          "schema_version": "0.1",
          "source_prompt": "test",
          "scene_name": "test_scene",
          "objects": [{
            "object_id": "invented",
            "asset": {"asset_id": "asset_not_in_catalog"}
          }],
          "camera": {"camera_id": "main_camera", "transform": {}},
          "lights": [],
          "render": {},
          "notes": []
        }""")
        with self.assertRaisesRegex(PlanningError, "outside the available catalog"):
            ScenePlanner(client).plan(
                model="fake",
                prompt="test",
                asset_ids=["allowed_asset"],
            )
        self.assertEqual(client.call_count, 1)

    def test_retrieved_asset_context_is_shown_to_the_model(self):
        client = FakeClient("""{
          "schema_version": "0.1",
          "source_prompt": "test",
          "scene_name": "door_scene",
          "objects": [{
            "object_id": "door",
            "asset": {"asset_id": "sm_door"}
          }],
          "camera": {"camera_id": "main_camera", "transform": {}},
          "lights": [],
          "render": {},
          "notes": []
        }""")

        ScenePlanner(client).plan(
            model="fake",
            prompt="add a door",
            asset_ids=["sm_door"],
            asset_context=["sm_door: SM_Door; type=static_mesh; unreal_path=/Game/SM_Door"],
        )

        user_message = client.last_request["messages"][1]["content"]
        system_message = client.last_request["messages"][0]["content"]
        self.assertIn("sm_door: SM_Door", user_message)
        self.assertIn("only the listed asset IDs may be used", user_message)
        self.assertIn("candidates, not proof of suitability", system_message)
        self.assertIn("Dimensions cm evidence", system_message)
        self.assertIn("fixed exposure for repeatable previews", system_message)

    def test_material_assets_are_subject_to_the_same_catalog_allowlist(self):
        client = FakeClient("""{
          "source_prompt": "wooden door",
          "scene_name": "door_scene",
          "objects": [{
            "object_id": "door",
            "asset": {"asset_id": "sm_door"},
            "materials": [{
              "slot_name": "DoorSurface",
              "material": {"asset_id": "invented_wood"}
            }]
          }],
          "camera": {"camera_id": "main_camera", "transform": {}}
        }""")

        with self.assertRaisesRegex(PlanningError, "outside the available catalog"):
            ScenePlanner(client).plan(
                model="fake",
                prompt="make the door wooden",
                asset_ids=["sm_door", "real_wood"],
            )


if __name__ == "__main__":
    unittest.main()
