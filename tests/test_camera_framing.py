import unittest

from render_master_bot.camera_framing import CameraFramingError, frame_camera
from render_master_bot.contracts import AssetCard
from render_master_bot.models import RenderSpec
from render_master_bot.patching import apply_render_spec_patch
from render_master_bot.serialization import canonical_sha256


def cube_card(**overrides) -> AssetCard:
    values = {
        "asset_id": "cube_asset",
        "engine_path": "/Game/Props/SM_Cube",
        "display_name": "Cube",
        "asset_type": "static_mesh",
        "dimensions_cm": {"x": 200, "y": 200, "z": 200},
    }
    values.update(overrides)
    return AssetCard.model_validate(values)


def framing_scene(**overrides) -> RenderSpec:
    values = {
        "source_prompt": "Frame one two-meter object.",
        "scene_name": "framing_scene",
        "objects": [{
            "object_id": "subject",
            "asset": {"asset_id": "cube_asset"},
            "transform": {"location_cm": {"z": 100}},
        }],
        "camera": {
            "camera_id": "camera",
            "transform": {"location_cm": {"x": -500, "z": 100}},
            "focal_length_mm": 50,
        },
        "render": {"width_px": 1920, "height_px": 1080},
    }
    values.update(overrides)
    return RenderSpec.model_validate(values)


class CameraFramingTests(unittest.TestCase):
    def test_frames_a_cube_with_ten_percent_edge_margins(self):
        original = framing_scene()

        result = frame_camera(original, [cube_card()])

        self.assertAlmostEqual(result.target_cm.z, 100)
        self.assertGreater(result.distance_cm, 700)
        self.assertAlmostEqual(result.spec.camera.transform.location_cm.x, -718.283951)
        self.assertEqual(result.spec.camera.transform.rotation_deg.model_dump(), {
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
        })
        self.assertEqual(result.spec.camera.focus_distance_cm, result.distance_cm)

    def test_patch_is_bound_to_and_reproduces_the_source_spec(self):
        original = framing_scene()

        result = frame_camera(original, [cube_card()])

        self.assertEqual(result.patch.base_spec_sha256, canonical_sha256(original))
        self.assertEqual(apply_render_spec_patch(original, result.patch), result.spec)
        self.assertEqual(result.patch.proposed_by.model, "deterministic_autoframe_v1")

    def test_preserves_the_camera_viewing_side_and_aims_at_bounds_center(self):
        original = framing_scene(camera={
            "camera_id": "camera",
            "transform": {"location_cm": {"x": -500, "y": -500, "z": 100}},
            "focal_length_mm": 50,
        })

        result = frame_camera(original, [cube_card()])

        location = result.spec.camera.transform.location_cm
        rotation = result.spec.camera.transform.rotation_deg
        self.assertLess(location.x, 0)
        self.assertLess(location.y, 0)
        self.assertAlmostEqual(rotation.z, 45.0)

    def test_object_scale_increases_required_distance(self):
        normal = frame_camera(framing_scene(), [cube_card()])
        scaled = frame_camera(framing_scene(objects=[{
            "object_id": "subject",
            "asset": {"asset_id": "cube_asset"},
            "transform": {
                "location_cm": {"z": 100},
                "scale": {"x": 2, "y": 2, "z": 2},
            },
        }]), [cube_card()])

        self.assertGreater(scaled.distance_cm, normal.distance_cm * 1.9)

    def test_object_rotation_changes_projected_bounds(self):
        card = cube_card(dimensions_cm={"x": 400, "y": 100, "z": 100})
        unrotated = frame_camera(framing_scene(), [card])
        rotated = frame_camera(framing_scene(objects=[{
            "object_id": "subject",
            "asset": {"asset_id": "cube_asset"},
            "transform": {
                "location_cm": {"z": 100},
                "rotation_deg": {"z": 90},
            },
        }]), [card])

        self.assertGreater(rotated.distance_cm, unrotated.distance_cm)

    def test_missing_dimensions_are_rejected_instead_of_guessed(self):
        with self.assertRaisesRegex(CameraFramingError, "no dimensions_cm"):
            frame_camera(framing_scene(), [cube_card(dimensions_cm=None)])


if __name__ == "__main__":
    unittest.main()
