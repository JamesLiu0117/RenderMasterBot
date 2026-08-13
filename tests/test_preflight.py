import unittest

from render_master_bot.models import RenderSpec
from render_master_bot.preflight import run_preflight
from render_master_bot.serialization import canonical_sha256


def scene(**overrides) -> RenderSpec:
    values = {
        "source_prompt": "Create a small studio scene.",
        "scene_name": "preflight_scene",
        "objects": [{
            "object_id": "subject",
            "asset": {"asset_id": "studio_chair"},
            "transform": {"location_cm": {"x": 0, "y": 0, "z": 0}},
        }],
        "camera": {
            "camera_id": "main_camera",
            "transform": {"location_cm": {"x": -500, "y": 0, "z": 150}},
            "focal_length_mm": 50,
        },
        "lights": [{
            "light_id": "key_light",
            "kind": "rect",
            "transform": {"location_cm": {"x": -200, "y": 200, "z": 300}},
            "intensity": 1500,
            "intensity_unit": "lumens",
        }],
    }
    values.update(overrides)
    return RenderSpec.model_validate(values)


class SemanticPreflightTests(unittest.TestCase):
    def test_plausible_studio_scene_passes(self):
        report = run_preflight(scene())
        self.assertEqual(report.verdict, "pass")
        self.assertEqual(report.issues, [])
        self.assertEqual(report.render_spec_sha256, canonical_sha256(scene()))

    def test_no_lights_requires_review_but_does_not_fail(self):
        report = run_preflight(scene(lights=[]))
        self.assertEqual(report.verdict, "needs_review")
        self.assertEqual(report.issues[0].issue_id, "no_explicit_lights")

    def test_exact_duplicate_asset_transform_fails(self):
        report = run_preflight(scene(objects=[
            {
                "object_id": "chair_a",
                "asset": {"asset_id": "studio_chair"},
                "transform": {"location_cm": {"x": 0, "y": 0, "z": 0}},
            },
            {
                "object_id": "chair_b",
                "asset": {"asset_id": "studio_chair"},
                "transform": {"location_cm": {"x": 0, "y": 0, "z": 0}},
            },
        ]))
        self.assertEqual(report.verdict, "fail")
        self.assertEqual(report.issues[0].severity, "error")
        self.assertIn("chair_a", report.issues[0].object_ids)

    def test_camera_at_object_pivot_requires_review(self):
        report = run_preflight(scene(camera={
            "camera_id": "main_camera",
            "transform": {"location_cm": {"x": 0, "y": 0, "z": 0}},
        }))
        self.assertEqual(report.verdict, "needs_review")
        self.assertTrue(
            any(issue.issue_id.startswith("camera_at_object_pivot") for issue in report.issues)
        )

    def test_coincident_local_lights_are_grouped(self):
        lights = [
            {
                "light_id": light_id,
                "kind": "point",
                "transform": {"location_cm": {"x": 10, "y": 20, "z": 30}},
                "intensity": 500,
                "intensity_unit": "lumens",
            }
            for light_id in ("light_a", "light_b", "light_c")
        ]
        report = run_preflight(scene(lights=lights))
        issue = next(item for item in report.issues if item.issue_id.startswith("coincident"))
        self.assertEqual(issue.object_ids, ["light_a", "light_b", "light_c"])

    def test_extreme_camera_and_render_values_are_informational(self):
        report = run_preflight(scene(
            camera={
                "camera_id": "main_camera",
                "transform": {"location_cm": {"x": -500, "y": 0, "z": 150}},
                "focal_length_mm": 500,
            },
            render={"width_px": 16384, "height_px": 1024},
        ))
        self.assertEqual(report.verdict, "pass")
        self.assertEqual(
            {issue.issue_id for issue in report.issues},
            {"extreme_focal_length_main_camera", "extreme_aspect_ratio"},
        )

    def test_zero_intensity_lights_require_review(self):
        report = run_preflight(scene(lights=[{
            "light_id": "off_light",
            "kind": "point",
            "intensity": 0,
            "intensity_unit": "lumens",
        }]))
        self.assertEqual(report.verdict, "needs_review")
        self.assertEqual(report.issues[0].issue_id, "zero_intensity_lights_off_light")


if __name__ == "__main__":
    unittest.main()
