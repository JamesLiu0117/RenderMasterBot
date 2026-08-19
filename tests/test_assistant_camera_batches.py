import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_camera_batches import (
    CameraBatchProposalError,
    compile_camera_batch_intent,
    load_camera_selection_context,
    propose_camera_batch_change,
)
from render_master_bot.contracts import (
    AssistantCameraBatchProposal,
    CameraEditIntent,
    UnrealCameraContext,
    UnrealCameraSelectionContext,
)
from render_master_bot.ollama import StructuredResponse


class FakeClient:
    def __init__(self, contents):
        self.contents = iter(contents if isinstance(contents, list) else [contents])
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(
            content=next(self.contents), model="fake-camera-batch"
        )


def camera_context(name, *, kind="camera", exposure=0, fov=90, focal=50):
    cine = kind == "cine_camera"
    return UnrealCameraContext.model_validate({
        "project_name": "OptimizationPlugin",
        "level_path": "/Game/FirstPerson/Lvl_FirstPerson",
        "actor_name": name,
        "actor_path": f"/Game/FirstPerson/Lvl_FirstPerson:PersistentLevel.{name}",
        "actor_class": "CineCameraActor" if cine else "CameraActor",
        "actor_guid": f"{sum(map(ord, name)):032X}",
        "component_name": "CameraComponent",
        "camera_kind": kind,
        "component_mobility": "movable",
        "projection_mode": "perspective",
        "is_editable": True,
        "is_locked": False,
        "min_focal_length_mm": 18 if cine else None,
        "max_focal_length_mm": 135 if cine else None,
        "min_aperture_fstop": 1.4 if cine else 1,
        "max_aperture_fstop": 22 if cine else 32,
        "minimum_focus_distance_cm": 1.5 if cine else 0,
        "camera": {
            "location_cm": {"x": len(name) * 10, "y": -300, "z": 150},
            "rotation_deg": {"x": 0, "y": -10, "z": len(name)},
            "field_of_view_deg": None if cine else fov,
            "focal_length_mm": focal if cine else None,
            "aperture_fstop": 2.8 if cine else 4,
            "focus_mode": "manual" if cine else "project_default",
            "focus_distance_cm": 500 if cine else 1000,
            "exposure_compensation_enabled": exposure != 0,
            "exposure_compensation_ev": exposure,
            "post_process_blend_weight": 1,
        },
    })


def selection(*cameras):
    return UnrealCameraSelectionContext(
        project_name="OptimizationPlugin",
        level_path="/Game/FirstPerson/Lvl_FirstPerson",
        cameras=list(cameras),
    )


def intent_json(**updates):
    value = {
        "schema_version": "0.1",
        "outcome": "proposed",
        "location": {"operation": "preserve", "x": None, "y": None, "z": None},
        "rotation": {"operation": "preserve", "x": None, "y": None, "z": None},
        "field_of_view_deg": {"operation": "preserve", "value": None},
        "focal_length_mm": {"operation": "preserve", "value": None},
        "aperture_fstop": {"operation": "preserve", "value": None},
        "focus_mode": "preserve",
        "focus_distance_cm": {"operation": "preserve", "value": None},
        "exposure_compensation_enabled": None,
        "exposure_compensation_ev": {"operation": "preserve", "value": None},
        "rationale": "Apply one shared bounded edit to the selected cameras.",
        "missing_capabilities": [],
    }
    value.update(updates)
    return json.dumps(value)


class AssistantCameraBatchTests(unittest.TestCase):
    def test_repository_selection_example_validates(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "examples"
            / "unreal_camera_selection_context.json"
        )
        loaded = load_camera_selection_context(path)
        self.assertEqual(len(loaded.cameras), 2)
        self.assertEqual(
            [camera.actor_name for camera in loaded.cameras],
            ["WideCamera", "DetailCamera"],
        )

    def test_selection_requires_unique_two_to_sixteen_cameras(self):
        first = camera_context("CameraA")
        second = camera_context("CameraB")
        self.assertEqual(len(selection(first, second).cameras), 2)
        with self.assertRaisesRegex(ValidationError, "at least 2"):
            selection(first)
        with self.assertRaisesRegex(ValidationError, "repeat an Actor path"):
            selection(first, first)

    def test_loader_preserves_ordered_selection(self):
        current = selection(camera_context("CameraA"), camera_context("CameraB"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selection.json"
            path.write_text(current.model_dump_json(), encoding="utf-8")
            loaded = load_camera_selection_context(path)
            self.assertEqual(
                [camera.actor_name for camera in loaded.cameras],
                ["CameraA", "CameraB"],
            )
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                CameraBatchProposalError, "invalid Unreal camera selection"
            ):
                load_camera_selection_context(path)

    def test_shared_relative_transform_preserves_camera_offsets(self):
        current = selection(camera_context("CameraA"), camera_context("LongCameraB"))
        intent = CameraEditIntent.model_validate({
            "outcome": "proposed",
            "location": {"operation": "add", "z": 50},
            "rotation": {"operation": "add", "z": 15},
            "rationale": "Raise and pan every selected camera together.",
        })
        actions = compile_camera_batch_intent(current, intent)
        self.assertEqual(actions[0].after.location_cm.z, 200)
        self.assertEqual(actions[1].after.location_cm.z, 200)
        self.assertNotEqual(
            actions[0].after.location_cm.x,
            actions[1].after.location_cm.x,
        )
        self.assertEqual(
            [change.property for change in actions[0].changes],
            ["location", "rotation"],
        )

    def test_batch_allows_one_camera_already_at_shared_target(self):
        current = selection(
            camera_context("CameraA", exposure=1),
            camera_context("CameraB", exposure=0),
        )
        intent = CameraEditIntent.model_validate({
            "outcome": "proposed",
            "exposure_compensation_enabled": True,
            "exposure_compensation_ev": {"operation": "set", "value": 1},
            "rationale": "Match exposure compensation across the selection.",
        })
        actions = compile_camera_batch_intent(current, intent)
        self.assertEqual(actions[0].changes, [])
        self.assertEqual(actions[1].after.exposure_compensation_ev, 1)
        self.assertTrue(actions[1].after.exposure_compensation_enabled)

    def test_mixed_selection_rejects_incompatible_lens_edit(self):
        current = selection(
            camera_context("CameraA"),
            camera_context("CineB", kind="cine_camera"),
        )
        direct_fov = CameraEditIntent.model_validate({
            "outcome": "proposed",
            "field_of_view_deg": {"operation": "set", "value": 60},
            "rationale": "Set direct FOV on the mixed selection.",
        })
        with self.assertRaisesRegex(
            CameraBatchProposalError, "Cine Cameras use focal length"
        ):
            compile_camera_batch_intent(current, direct_fov)

    def test_model_proposal_covers_every_camera_in_order(self):
        current = selection(camera_context("CameraA"), camera_context("CameraB"))
        result = propose_camera_batch_change(
            prompt="Set all selected cameras to +1 EV",
            selection=current,
            client=FakeClient(intent_json(
                exposure_compensation_enabled=True,
                exposure_compensation_ev={"operation": "set", "value": 1},
            )),
            model="planner",
            proposal_id="camera_batch_001",
        )
        self.assertEqual(result.proposal.status, "proposed")
        self.assertEqual(
            [action.target.actor_name for action in result.proposal.actions],
            ["CameraA", "CameraB"],
        )
        self.assertEqual(result.attempt_count, 1)

    def test_unsafe_shared_edit_gets_one_retry(self):
        current = selection(camera_context("CameraA"), camera_context("CameraB"))
        client = FakeClient([
            intent_json(field_of_view_deg={"operation": "set", "value": 300}),
            intent_json(field_of_view_deg={"operation": "set", "value": 70}),
        ])
        result = propose_camera_batch_change(
            prompt="Use a moderately narrow view on every camera",
            selection=current,
            client=client,
            model="planner",
            proposal_id="camera_batch_retry",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertTrue(all(action.after.field_of_view_deg == 70 for action in result.proposal.actions))
        self.assertEqual(len(client.requests), 2)

    def test_unresolved_and_tampered_order_contracts(self):
        current = selection(camera_context("CameraA"), camera_context("CameraB"))
        unresolved = propose_camera_batch_change(
            prompt="Frame a different hero in every camera",
            selection=current,
            client=FakeClient(intent_json(
                outcome="unresolved",
                rationale="This needs per-camera geometry-aware framing.",
                missing_capabilities=["per-camera geometry-aware framing"],
            )),
            model="planner",
            proposal_id="camera_batch_gap",
        )
        self.assertEqual(unresolved.proposal.status, "unresolved")
        self.assertEqual(unresolved.proposal.actions, [])

        proposed = propose_camera_batch_change(
            prompt="Raise every camera",
            selection=current,
            client=FakeClient(intent_json(
                location={"operation": "add", "x": None, "y": None, "z": 20}
            )),
            model="planner",
            proposal_id="camera_batch_order",
        ).proposal.model_dump(mode="json")
        proposed["actions"].reverse()
        with self.assertRaisesRegex(ValidationError, "preserve selection order"):
            AssistantCameraBatchProposal.model_validate(proposed)


if __name__ == "__main__":
    unittest.main()
