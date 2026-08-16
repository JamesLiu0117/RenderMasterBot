"""Run inside Unreal Editor to inventory one material's exposed parameters."""

import json
import os
import traceback

import unreal


def write_result(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)


def main():
    material_path = os.environ["RENDERMASTER_MATERIAL_PARAMETER_PATH"]
    output_path = os.environ["RENDERMASTER_MATERIAL_PARAMETER_OUTPUT"]
    project_name = os.path.splitext(os.path.basename(unreal.Paths.get_project_file_path()))[0]
    result = {
        "schema_version": "0.1",
        "status": "failed",
        "project_name": project_name,
        "material_path": material_path,
        "scalar_parameters": [],
        "vector_parameters": [],
        "errors": [],
    }
    try:
        material = unreal.load_asset(material_path)
        if material is None or not isinstance(material, unreal.MaterialInterface):
            raise RuntimeError(f"not a loadable MaterialInterface: {material_path}")
        result["scalar_parameters"] = sorted(
            str(value) for value in unreal.MaterialEditingLibrary.get_scalar_parameter_names(material)
        )
        result["vector_parameters"] = sorted(
            str(value) for value in unreal.MaterialEditingLibrary.get_vector_parameter_names(material)
        )
        result["status"] = "succeeded"
    except Exception as exc:
        result["errors"].append(str(exc))
        unreal.log_error(traceback.format_exc())
    write_result(output_path, result)
    if result["status"] != "succeeded":
        raise RuntimeError("material parameter scan failed")


main()
