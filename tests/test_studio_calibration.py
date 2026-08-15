import unittest

from render_master_bot.models import RenderSpec
from render_master_bot.serialization import canonical_sha256
from render_master_bot.studio_calibration import calibrate_studio_preview


def scene(**overrides) -> RenderSpec:
    value = {
        "source_prompt": "Render a product preview.",
        "scene_name": "product",
        "objects": [],
        "camera": {"transform": {"location_cm": {"x": -300}}},
        "lights": [{
            "light_id": "key",
            "kind": "directional",
            "intensity": 5_000,
            "intensity_unit": "lux",
        }, {
            "light_id": "fill",
            "kind": "rect",
            "intensity": 900,
            "intensity_unit": "lumens",
        }],
    }
    value.update(overrides)
    return RenderSpec.model_validate(value)


class StudioCalibrationTests(unittest.TestCase):
    def test_locks_exposure_and_raises_only_existing_directional_lights(self):
        original = scene()

        result = calibrate_studio_preview(original)

        self.assertIsNotNone(result.patch)
        self.assertEqual(result.patch.base_spec_sha256, canonical_sha256(original))
        self.assertEqual(result.spec.camera.exposure.mode, "fixed")
        self.assertEqual(result.spec.camera.exposure.fixed_ev100, 9.0)
        self.assertEqual(result.spec.lights[0].intensity, 20_000)
        self.assertEqual(result.spec.lights[1].intensity, 900)
        self.assertEqual(len(result.spec.lights), 2)

    def test_already_calibrated_spec_requires_no_patch(self):
        original = scene(
            camera={
                "transform": {"location_cm": {"x": -300}},
                "exposure": {"mode": "fixed", "fixed_ev100": 9},
            },
            lights=[{
                "light_id": "key",
                "kind": "directional",
                "intensity": 25_000,
                "intensity_unit": "lux",
            }],
        )

        result = calibrate_studio_preview(original)

        self.assertIsNone(result.patch)
        self.assertIs(result.spec, original)
