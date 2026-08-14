import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from render_master_bot.unreal_assets import (
    UnrealAssetScanError,
    asset_cards_from_scan,
    asset_type_for_unreal_class,
    load_asset_cards,
    run_unreal_asset_scan,
    stable_asset_id,
)


def raw_scan() -> dict:
    return {
        "schema_version": "0.1",
        "project_name": "OptimizationPlugin",
        "path_prefix": "/Game",
        "total_assets": 539,
        "selected_assets": 2,
        "assets": [
            {
                "package_name": "/Game/FirstPerson/LevelPrototyping/Meshes/SM_Cube",
                "object_path": (
                    "/Game/FirstPerson/LevelPrototyping/Meshes/SM_Cube.SM_Cube"
                ),
                "display_name": "SM_Cube",
                "class_name": "StaticMesh",
                "dimensions_cm": {"x": 100.0, "y": 100.0, "z": 100.0},
                "pivot_offset_cm": {"x": 0.0, "y": 0.0, "z": 0.0},
                "material_slots": ["MaterialSlot_0"],
            },
            {
                "package_name": "/Game/FirstPerson/Lvl_FirstPerson",
                "object_path": "/Game/FirstPerson/Lvl_FirstPerson.Lvl_FirstPerson",
                "display_name": "Lvl_FirstPerson",
                "class_name": "World",
                "pivot_offset_cm": {"x": 0.0, "y": 0.0, "z": 0.0},
                "material_slots": [],
            },
        ],
        "warnings": [],
    }


class UnrealAssetConversionTests(unittest.TestCase):
    def test_class_mapping_covers_render_relevant_unreal_types(self):
        expected = {
            "StaticMesh": "static_mesh",
            "SkeletalMesh": "skeletal_mesh",
            "MaterialInstanceConstant": "material",
            "Texture2D": "texture",
            "AnimSequence": "animation",
            "WidgetBlueprint": "blueprint",
            "World": "level",
            "NiagaraSystem": "other",
        }
        for class_name, asset_type in expected.items():
            with self.subTest(class_name=class_name):
                self.assertEqual(asset_type_for_unreal_class(class_name), asset_type)

    def test_stable_id_is_readable_bounded_and_path_specific(self):
        first = stable_asset_id("/Game/A/SM_Crate", "SM_Crate")
        second = stable_asset_id("/Game/B/SM_Crate", "SM_Crate")

        self.assertRegex(first, r"^[a-z][a-z0-9_-]{0,63}$")
        self.assertLessEqual(len(first), 64)
        self.assertNotEqual(first, second)
        self.assertEqual(first, stable_asset_id("/Game/A/SM_Crate", "SM_Crate"))

    def test_scan_records_become_strict_asset_cards(self):
        cards = asset_cards_from_scan(raw_scan())

        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0].asset_type, "static_mesh")
        self.assertEqual(cards[0].dimensions_cm.z, 100.0)
        self.assertEqual(cards[0].material_slots, ["MaterialSlot_0"])
        self.assertEqual(cards[1].asset_type, "level")
        self.assertEqual(cards[1].engine_path, "/Game/FirstPerson/Lvl_FirstPerson")
        self.assertEqual(cards[0].sources[0].source_type, "generated")

    def test_loader_rejects_a_count_mismatch(self):
        value = raw_scan()
        value["selected_assets"] = 3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(UnrealAssetScanError, "does not match"):
                load_asset_cards(path)

    def test_output_preparation_errors_are_wrapped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "Project.uproject"
            project.write_text("{}", encoding="utf-8")
            editor = root / "UE" / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
            editor.parent.mkdir(parents=True)
            editor.write_bytes(b"")

            with patch(
                "render_master_bot.unreal_assets.Path.mkdir",
                side_effect=PermissionError("denied"),
            ):
                with self.assertRaisesRegex(UnrealAssetScanError, "cannot prepare raw output"):
                    run_unreal_asset_scan(
                        project,
                        engine_root=root / "UE",
                        raw_output=root / "locked" / "raw.json",
                    )


if __name__ == "__main__":
    unittest.main()
