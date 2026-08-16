import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from render_master_bot.material_variants import (
    MaterialVariantError,
    ScalarParameterOverride,
    VectorParameterOverride,
    create_material_variant,
    prepare_material_variant_request,
)


class MaterialVariantTests(unittest.TestCase):
    def test_request_requires_a_safe_parent_destination_and_unique_override(self):
        request = prepare_material_variant_request(
            parent_material_path="/Game/Materials/M_Master.M_Master",
            destination_path="/Game/RenderMasterBot/Variants/Wood",
            instance_name="MI_DarkWood",
            scalar_parameters=[ScalarParameterOverride(name="Roughness", value=0.8)],
            vector_parameters=[
                VectorParameterOverride(name="Tint", r=0.2, g=0.1, b=0.05)
            ],
        )
        self.assertEqual(request.parent_material_path, "/Game/Materials/M_Master")
        self.assertEqual(request.scalar_parameters[0].value, 0.8)

        with self.assertRaisesRegex(ValueError, "must be unique"):
            prepare_material_variant_request(
                parent_material_path="/Game/M_Master",
                destination_path="/Game/Variants",
                instance_name="MI_Invalid",
                scalar_parameters=[ScalarParameterOverride(name="Tint", value=1.0)],
                vector_parameters=[VectorParameterOverride(name="tint", r=1, g=1, b=1)],
            )
        with self.assertRaisesRegex(MaterialVariantError, "unsafe Unreal"):
            prepare_material_variant_request(
                parent_material_path="/Engine/M_Default",
                destination_path="/Game/Variants",
                instance_name="MI_Invalid",
                scalar_parameters=[ScalarParameterOverride(name="Roughness", value=1.0)],
            )

    def test_runner_revalidates_unreal_result_against_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "Project.uproject"
            project.write_text("{}", encoding="utf-8")
            editor = root / "UE" / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
            editor.parent.mkdir(parents=True)
            editor.write_bytes(b"")
            output = root / "variant_result.json"

            def fake_run(command, **kwargs):
                request = json.loads(
                    Path(kwargs["env"]["RENDERMASTER_MATERIAL_VARIANT_REQUEST"]).read_text()
                )
                output.write_text(json.dumps({
                    "schema_version": "0.1",
                    "status": "succeeded",
                    "project_name": "Project",
                    "parent_material_path": request["parent_material_path"],
                    "instance_engine_path": (
                        f"{request['destination_path']}/{request['instance_name']}"
                    ),
                    "scalar_parameters": request["scalar_parameters"],
                    "vector_parameters": request["vector_parameters"],
                    "errors": [],
                }), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("render_master_bot.material_variants.subprocess.run", side_effect=fake_run):
                request, result = create_material_variant(
                    project,
                    engine_root=root / "UE",
                    parent_material_path="/Game/M_Master",
                    destination_path="/Game/Variants",
                    instance_name="MI_Dark",
                    scalar_parameters=[ScalarParameterOverride(name="Roughness", value=0.9)],
                    vector_parameters=None,
                    output=output,
                )

        self.assertEqual(result.parent_material_path, request.parent_material_path)
        self.assertEqual(result.instance_engine_path, "/Game/Variants/MI_Dark")


if __name__ == "__main__":
    unittest.main()
