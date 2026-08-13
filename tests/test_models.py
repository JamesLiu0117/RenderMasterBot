import unittest

from pydantic import ValidationError

from render_master_bot.models import Camera, RenderSpec, Transform


def minimal_spec(**overrides):
    values = {
        "source_prompt": "Create an empty lighting test scene.",
        "scene_name": "lighting_test",
        "camera": Camera(transform=Transform()),
    }
    values.update(overrides)
    return RenderSpec(**values)


class RenderSpecTests(unittest.TestCase):
    def test_minimal_spec_is_valid(self):
        spec = minimal_spec()
        self.assertEqual(spec.schema_version, "0.1")
        self.assertEqual(spec.render.width_px, 1920)

    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            minimal_spec(unexpected="unsafe")

    def test_duplicate_scene_identifiers_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "identifiers must be unique"):
            minimal_spec(camera={"camera_id": "duplicate", "transform": {}}, lights=[{
                "light_id": "duplicate",
                "kind": "point",
                "intensity": 100,
                "intensity_unit": "lumens",
            }])

    def test_non_positive_scale_is_rejected(self):
        with self.assertRaises(ValidationError):
            Transform(scale={"x": 0, "y": 1, "z": 1})

    def test_directional_light_requires_lux(self):
        with self.assertRaisesRegex(ValidationError, "must use lux"):
            minimal_spec(lights=[{
                "light_id": "sun",
                "kind": "directional",
                "intensity": 100_000,
                "intensity_unit": "lumens",
            }])


if __name__ == "__main__":
    unittest.main()
