import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from render_master_bot.unreal_materials import (
    PbrMaterialImportRequest,
    UnrealMaterialImportError,
    prepare_pbr_material_import_request,
    run_unreal_pbr_material_import,
)


def write_maps(root: Path) -> dict[str, Path]:
    paths = {}
    for role in ("base_color", "normal", "roughness", "ambient_occlusion"):
        path = root / f"T_Wood_{role}.jpg"
        path.write_bytes(f"map:{role}".encode())
        paths[role] = path
    return paths


class UnrealMaterialImportTests(unittest.TestCase):
    def test_request_freezes_all_source_hashes_and_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            maps = write_maps(Path(directory))
            request = prepare_pbr_material_import_request(
                destination_path="/Game/RenderMasterBot/TestMaterials/Wood",
                material_name="M_Wood",
                **maps,
            )

        self.assertEqual(
            {texture.role for texture in request.textures},
            {"base_color", "normal", "roughness", "ambient_occlusion"},
        )
        self.assertTrue(all(len(texture.source_sha256) == 64 for texture in request.textures))
        self.assertEqual(request.material_name, "M_Wood")

    def test_request_rejects_unsafe_destination_and_missing_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            maps = write_maps(Path(directory))
            with self.assertRaisesRegex(UnrealMaterialImportError, "unsafe Unreal"):
                prepare_pbr_material_import_request(
                    destination_path="/Game/../Secret",
                    material_name="M_Wood",
                    **maps,
                )
            maps["normal"] = Path(directory) / "missing.jpg"
            with self.assertRaisesRegex(UnrealMaterialImportError, "does not exist"):
                prepare_pbr_material_import_request(
                    destination_path="/Game/Materials/Wood",
                    material_name="M_Wood",
                    **maps,
                )

    def test_request_schema_requires_exactly_one_of_each_role(self):
        value = {
            "destination_path": "/Game/Materials/Wood",
            "material_name": "M_Wood",
            "textures": [
                {
                    "role": "base_color",
                    "source_path": f"C:/map_{index}.jpg",
                    "source_sha256": "a" * 64,
                    "destination_name": f"T_Map_{index}",
                }
                for index in range(4)
            ],
        }
        with self.assertRaisesRegex(ValueError, "exactly one"):
            PbrMaterialImportRequest.model_validate(value)

    def test_runner_verifies_frozen_unreal_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "Project.uproject"
            project.write_text("{}", encoding="utf-8")
            editor = root / "UE" / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
            editor.parent.mkdir(parents=True)
            editor.write_bytes(b"")
            maps = write_maps(root)
            output = root / "material_import.json"
            observed_command = []

            def fake_run(command, **kwargs):
                observed_command.extend(command)
                request = json.loads(
                    Path(kwargs["env"]["RENDERMASTER_MATERIAL_IMPORT_REQUEST"]).read_text(
                        encoding="utf-8"
                    )
                )
                output.write_text(
                    json.dumps(
                        {
                            "schema_version": "0.1",
                            "status": "succeeded",
                            "project_name": "Project",
                            "destination_path": request["destination_path"],
                            "material_name": request["material_name"],
                            "material_engine_path": (
                                f"{request['destination_path']}/{request['material_name']}"
                            ),
                            "textures": [
                                {
                                    "role": texture["role"],
                                    "source_sha256": texture["source_sha256"],
                                    "engine_path": (
                                        f"{request['destination_path']}/"
                                        f"{texture['destination_name']}"
                                    ),
                                    "class_name": "Texture2D",
                                    "srgb": texture["role"] == "base_color",
                                    "compression_settings": "test",
                                }
                                for texture in request["textures"]
                            ],
                            "warnings": [],
                            "errors": [],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch(
                "render_master_bot.unreal_materials.subprocess.run",
                side_effect=fake_run,
            ):
                request, result = run_unreal_pbr_material_import(
                    project,
                    engine_root=root / "UE",
                    destination_path="/Game/RenderMasterBot/TestMaterials/Wood",
                    material_name="M_Wood",
                    output=output,
                    **maps,
                )

        self.assertEqual(result.material_engine_path, f"{request.destination_path}/M_Wood")
        self.assertIn("-nullrhi", observed_command)
        self.assertIn("-unattended", observed_command)


if __name__ == "__main__":
    unittest.main()
