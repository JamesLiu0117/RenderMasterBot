import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.assistant_cameras import (
    CameraProposalError,
    compile_camera_intent,
    load_camera_context,
    propose_camera_change,
)
from render_master_bot.contracts import (
    AssistantCameraProposal,
    CameraEditIntent,
    UnrealCameraContext,
)
from render_master_bot.ollama import StructuredResponse


class FakeClient:
    def __init__(self, contents):
        self.contents = iter(contents if isinstance(contents, list) else [contents])
        self.requests = []

    def chat_structured(self, **kwargs):
        self.requests.append(kwargs)
        return StructuredResponse(content=next(self.contents), model="fake-camera")


def camera_context(kind="camera", **updates):
    camera = {
        "location_cm": {"x": 0, "y": -300, "z": 150},
        "rotation_deg": {"x": 0, "y": -10, "z": 0},
        "field_of_view_deg": 90,
        "focal_length_mm": None,
        "aperture_fstop": 4,
        "focus_mode": "project_default",
        "focus_distance_cm": 1000,
        "exposure_compensation_enabled": False,
        "exposure_compensation_ev": 0,
        "post_process_blend_weight": 1,
    }
    values = {
        "project_name": "OptimizationPlugin",
        "level_path": "/Game/FirstPerson/Lvl_FirstPerson",
        "actor_name": "ProductCamera",
        "actor_path": "/Game/FirstPerson/Lvl_FirstPerson:PersistentLevel.ProductCamera",
        "actor_class": "CameraActor",
        "actor_guid": "0123456789ABCDEF0123456789ABCDEF",
        "component_name": "CameraComponent",
        "camera_kind": kind,
        "component_mobility": "movable",
        "projection_mode": "perspective",
        "is_editable": True,
        "is_locked": False,
        "min_focal_length_mm": None,
        "max_focal_length_mm": None,
        "min_aperture_fstop": 1,
        "max_aperture_fstop": 32,
        "minimum_focus_distance_cm": 0,
        "camera": camera,
    }
    if kind == "cine_camera":
        camera.update({
            "field_of_view_deg": None,
            "focal_length_mm": 50,
            "aperture_fstop": 2.8,
            "focus_mode": "manual",
            "focus_distance_cm": 500,
        })
        values.update({
            "actor_class": "CineCameraActor",
            "min_focal_length_mm": 18,
            "max_focal_length_mm": 135,
            "min_aperture_fstop": 1.4,
            "max_aperture_fstop": 22,
            "minimum_focus_distance_cm": 1.5,
        })
    values.update(updates)
    return UnrealCameraContext.model_validate(values)


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
        "rationale": "Apply the requested bounded camera edit.",
        "missing_capabilities": [],
    }
    value.update(updates)
    return json.dumps(value)


class AssistantCameraTests(unittest.TestCase):
    def test_context_loader_and_type_specific_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "camera.json"
            path.write_text(camera_context("cine_camera").model_dump_json(), encoding="utf-8")
            self.assertEqual(load_camera_context(path).camera_kind, "cine_camera")
            path.write_text('{"project_name":"P","extra":true}', encoding="utf-8")
            with self.assertRaisesRegex(CameraProposalError, "invalid Unreal camera"):
                load_camera_context(path)

        with self.assertRaisesRegex(ValidationError, "standard Cameras expose FOV"):
            camera_context(camera={
                **camera_context().camera.model_dump(),
                "field_of_view_deg": None,
            })

    def test_host_computes_standard_camera_transform_fov_and_exposure(self):
        intent = CameraEditIntent.model_validate({
            "outcome": "proposed",
            "location": {"operation": "add", "z": 50},
            "rotation": {"operation": "add", "z": 30},
            "field_of_view_deg": {"operation": "set", "value": 60},
            "exposure_compensation_ev": {"operation": "add", "value": 1},
            "rationale": "Move, rotate, narrow the view, and brighten by one stop.",
        })
        after, changes = compile_camera_intent(camera_context(), intent)
        self.assertEqual(after.location_cm.z, 200)
        self.assertEqual(after.rotation_deg.z, 30)
        self.assertEqual(after.field_of_view_deg, 60)
        self.assertTrue(after.exposure_compensation_enabled)
        self.assertEqual(after.exposure_compensation_ev, 1)
        self.assertEqual(
            [change.property for change in changes],
            [
                "location",
                "rotation",
                "field_of_view_deg",
                "exposure_compensation_enabled",
                "exposure_compensation_ev",
            ],
        )

    def test_host_computes_cine_lens_aperture_and_manual_focus(self):
        intent = CameraEditIntent.model_validate({
            "outcome": "proposed",
            "focal_length_mm": {"operation": "set", "value": 85},
            "aperture_fstop": {"operation": "set", "value": 4},
            "focus_distance_cm": {"operation": "set", "value": 350},
            "rationale": "Use an 85 mm lens at f/4 with manual focus at 3.5 meters.",
        })
        current = camera_context("cine_camera")
        current = current.model_copy(update={
            "camera": current.camera.model_copy(update={"focus_mode": "tracking"})
        })
        after, changes = compile_camera_intent(current, intent)
        self.assertEqual(after.focal_length_mm, 85)
        self.assertEqual(after.aperture_fstop, 4)
        self.assertEqual(after.focus_mode, "manual")
        self.assertEqual(after.focus_distance_cm, 350)
        self.assertEqual(
            [change.property for change in changes],
            ["focal_length_mm", "aperture_fstop", "focus_mode", "focus_distance_cm"],
        )

    def test_standard_camera_aperture_edit_enables_manual_focus_override(self):
        intent = CameraEditIntent.model_validate({
            "outcome": "proposed",
            "aperture_fstop": {"operation": "set", "value": 5.6},
            "rationale": "Set a visible depth-of-field aperture.",
        })
        after, changes = compile_camera_intent(camera_context(), intent)
        self.assertEqual(after.aperture_fstop, 5.6)
        self.assertEqual(after.focus_mode, "manual")
        self.assertEqual(
            [change.property for change in changes],
            ["aperture_fstop", "focus_mode"],
        )

    def test_type_specific_and_numeric_safety_rejections(self):
        focal = CameraEditIntent.model_validate({
            "outcome": "proposed",
            "focal_length_mm": {"operation": "set", "value": 50},
            "rationale": "Set focal length.",
        })
        with self.assertRaisesRegex(CameraProposalError, "standard Cameras use FOV"):
            compile_camera_intent(camera_context(), focal)

        fov = CameraEditIntent.model_validate({
            "outcome": "proposed",
            "field_of_view_deg": {"operation": "set", "value": 60},
            "rationale": "Set direct FOV.",
        })
        with self.assertRaisesRegex(CameraProposalError, "Cine Cameras use focal length"):
            compile_camera_intent(camera_context("cine_camera"), fov)

        outside_lens = CameraEditIntent.model_validate({
            "outcome": "proposed",
            "focal_length_mm": {"operation": "set", "value": 200},
            "rationale": "Use 200 mm.",
        })
        with self.assertRaisesRegex(CameraProposalError, "captured Cine lens bounds"):
            compile_camera_intent(camera_context("cine_camera"), outside_lens)

    def test_inactive_post_process_and_conflicting_edits_are_rejected(self):
        exposure = CameraEditIntent.model_validate({
            "outcome": "proposed",
            "exposure_compensation_ev": {"operation": "add", "value": 1},
            "rationale": "Brighten one stop.",
        })
        inactive = camera_context()
        inactive = inactive.model_copy(update={
            "camera": inactive.camera.model_copy(update={"post_process_blend_weight": 0})
        })
        with self.assertRaisesRegex(CameraProposalError, "blend weight is zero"):
            compile_camera_intent(inactive, exposure)

        conflict = CameraEditIntent.model_validate({
            "outcome": "proposed",
            "focus_mode": "disabled",
            "focus_distance_cm": {"operation": "set", "value": 200},
            "rationale": "Conflicting focus request.",
        })
        with self.assertRaisesRegex(CameraProposalError, "cannot explicitly disable focus"):
            compile_camera_intent(camera_context(), conflict)

    def test_model_proposal_and_unresolved_result(self):
        client = FakeClient(intent_json(
            field_of_view_deg={"operation": "set", "value": 70},
        ))
        result = propose_camera_change(
            prompt="Set this camera to 70 degrees FOV",
            context=camera_context(),
            client=client,
            model="planner",
            proposal_id="camera_001",
        )
        self.assertEqual(result.proposal.status, "proposed")
        self.assertEqual(result.proposal.after.field_of_view_deg, 70)
        self.assertEqual(result.attempt_count, 1)

        unresolved = intent_json(
            outcome="unresolved",
            rationale="The request needs geometry-aware composition.",
            missing_capabilities=["geometry-aware target framing"],
        )
        unresolved_result = propose_camera_change(
            prompt="Frame the selected hero perfectly",
            context=camera_context(),
            client=FakeClient(unresolved),
            model="planner",
            proposal_id="camera_002",
        )
        self.assertEqual(unresolved_result.proposal.status, "unresolved")
        self.assertIsNone(unresolved_result.proposal.after)

    def test_unsafe_first_output_gets_one_retry(self):
        client = FakeClient([
            intent_json(field_of_view_deg={"operation": "set", "value": 300}),
            intent_json(field_of_view_deg={"operation": "set", "value": 75}),
        ])
        result = propose_camera_change(
            prompt="Use a moderately wide view",
            context=camera_context(),
            client=client,
            model="planner",
            proposal_id="camera_retry",
        )
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.proposal.after.field_of_view_deg, 75)
        self.assertEqual(len(client.requests), 2)

    def test_proposal_rejects_tampered_change_list(self):
        intent = CameraEditIntent.model_validate_json(intent_json(
            field_of_view_deg={"operation": "set", "value": 60},
        ))
        context = camera_context()
        after, changes = compile_camera_intent(context, intent)
        value = {
            "proposal_id": "camera_tamper",
            "status": "proposed",
            "request": "Set FOV to 60",
            "target": context.model_dump(mode="json"),
            "proposed_by": {"provider": "ollama", "model": "planner"},
            "before": context.camera.model_dump(mode="json"),
            "after": after.model_dump(mode="json"),
            "changes": [change.model_dump(mode="json") for change in changes],
            "rationale": "Use a narrower view.",
        }
        AssistantCameraProposal.model_validate(value)
        value["changes"] = [{"property": "rotation", "operation": "set"}]
        with self.assertRaisesRegex(ValidationError, "cover every changed property"):
            AssistantCameraProposal.model_validate(value)


if __name__ == "__main__":
    unittest.main()
