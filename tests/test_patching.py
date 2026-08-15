import unittest

from render_master_bot.contracts import RenderSpecPatch
from render_master_bot.models import RenderSpec
from render_master_bot.patching import PatchApplicationError, apply_render_spec_patch
from render_master_bot.serialization import canonical_sha256


def scene() -> RenderSpec:
    return RenderSpec.model_validate({
        "source_prompt": "Frame one object.",
        "scene_name": "patch_scene",
        "camera": {
            "camera_id": "camera",
            "transform": {"location_cm": {"x": -100}},
        },
        "notes": ["original"],
    })


def patch_for(spec: RenderSpec, operations: list[dict]) -> RenderSpecPatch:
    return RenderSpecPatch.model_validate({
        "base_spec_sha256": canonical_sha256(spec),
        "rationale": "Exercise bounded patch application.",
        "proposed_by": {"provider": "local", "model": "test_patch"},
        "operations": operations,
    })


class RenderSpecPatchApplicationTests(unittest.TestCase):
    def test_applies_dictionary_and_list_operations_then_revalidates(self):
        original = scene()
        patch = patch_for(original, [
            {"op": "replace", "path": "/camera/focal_length_mm", "value": 35},
            {"op": "add", "path": "/notes/-", "value": "framed"},
            {"op": "remove", "path": "/notes/0"},
        ])

        result = apply_render_spec_patch(original, patch)

        self.assertEqual(result.camera.focal_length_mm, 35)
        self.assertEqual(result.notes, ["framed"])
        self.assertEqual(original.camera.focal_length_mm, 50)

    def test_rejects_a_patch_for_a_different_spec(self):
        original = scene()
        patch = patch_for(original, [
            {"op": "replace", "path": "/camera/focal_length_mm", "value": 35},
        ]).model_copy(update={"base_spec_sha256": "a" * 64})

        with self.assertRaisesRegex(PatchApplicationError, "does not match"):
            apply_render_spec_patch(original, patch)

    def test_replace_requires_an_existing_path(self):
        original = scene()
        patch = patch_for(original, [
            {"op": "replace", "path": "/camera/not_a_field", "value": 35},
        ])

        with self.assertRaisesRegex(PatchApplicationError, "does not exist"):
            apply_render_spec_patch(original, patch)

    def test_result_must_remain_a_valid_render_spec(self):
        original = scene()
        patch = patch_for(original, [
            {"op": "replace", "path": "/camera/focal_length_mm", "value": -1},
        ])

        with self.assertRaisesRegex(PatchApplicationError, "is invalid"):
            apply_render_spec_patch(original, patch)


if __name__ == "__main__":
    unittest.main()
