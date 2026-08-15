import unittest

from pydantic import ValidationError

from render_master_bot.models import Camera, ExposureSettings, RenderSpec, Transform


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

    def test_material_assignment_requires_unique_trimmed_slot_names(self):
        with self.assertRaisesRegex(ValidationError, "unique slot names"):
            minimal_spec(objects=[{
                "object_id": "door",
                "asset": {"asset_id": "door_asset"},
                "materials": [
                    {
                        "slot_name": "DoorSurface",
                        "material": {"asset_id": "wood_material"},
                    },
                    {
                        "slot_name": "doorsurface",
                        "material": {"asset_id": "paint_material"},
                    },
                ],
            }])

        with self.assertRaisesRegex(ValidationError, "surrounding whitespace"):
            minimal_spec(objects=[{
                "object_id": "door",
                "asset": {"asset_id": "door_asset"},
                "materials": [{
                    "slot_name": " DoorSurface ",
                    "material": {"asset_id": "wood_material"},
                }],
            }])

    def test_exposure_mode_requires_a_consistent_bounded_ev100(self):
        self.assertEqual(ExposureSettings().mode, "auto")
        self.assertEqual(
            ExposureSettings(mode="fixed", fixed_ev100=12).fixed_ev100,
            12,
        )
        with self.assertRaisesRegex(ValidationError, "requires fixed_ev100"):
            ExposureSettings(mode="fixed")
        with self.assertRaisesRegex(ValidationError, "cannot specify fixed_ev100"):
            ExposureSettings(mode="auto", fixed_ev100=10)
        with self.assertRaises(ValidationError):
            ExposureSettings(mode="fixed", fixed_ev100=31)

    def test_default_auto_exposure_does_not_change_v01_serialization(self):
        automatic = minimal_spec().model_dump(mode="json")
        self.assertNotIn("exposure", automatic["camera"])

        fixed = minimal_spec(camera={
            "transform": {},
            "exposure": {"mode": "fixed", "fixed_ev100": 12.0},
        }).model_dump(mode="json")
        self.assertEqual(
            fixed["camera"]["exposure"],
            {"mode": "fixed", "fixed_ev100": 12.0},
        )


if __name__ == "__main__":
    unittest.main()
