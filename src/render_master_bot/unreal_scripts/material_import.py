"""Run inside Unreal Editor to import four textures and build one PBR material."""

from __future__ import annotations

import json
import os
import traceback

import unreal


ROLE_SETTINGS = {
    "base_color": {
        "srgb": True,
        "compression": unreal.TextureCompressionSettings.TC_DEFAULT,
        "sampler": unreal.MaterialSamplerType.SAMPLERTYPE_COLOR,
        "property": unreal.MaterialProperty.MP_BASE_COLOR,
    },
    "normal": {
        "srgb": False,
        "compression": unreal.TextureCompressionSettings.TC_NORMALMAP,
        "sampler": unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL,
        "property": unreal.MaterialProperty.MP_NORMAL,
    },
    "roughness": {
        "srgb": False,
        "compression": unreal.TextureCompressionSettings.TC_MASKS,
        "sampler": unreal.MaterialSamplerType.SAMPLERTYPE_MASKS,
        "property": unreal.MaterialProperty.MP_ROUGHNESS,
    },
    "ambient_occlusion": {
        "srgb": False,
        "compression": unreal.TextureCompressionSettings.TC_MASKS,
        "sampler": unreal.MaterialSamplerType.SAMPLERTYPE_MASKS,
        "property": unreal.MaterialProperty.MP_AMBIENT_OCCLUSION,
    },
}


def write_result(output_path, payload):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)


def package_path(value):
    return value.partition(".")[0]


def import_texture(asset_tools, asset_subsystem, destination_path, texture_request):
    expected_path = f"{destination_path}/{texture_request['destination_name']}"
    if asset_subsystem.does_asset_exist(expected_path):
        raise RuntimeError(f"refusing to overwrite existing texture asset: {expected_path}")

    task = unreal.AssetImportTask()
    task.set_editor_property("filename", texture_request["source_path"])
    task.set_editor_property("destination_path", destination_path)
    task.set_editor_property("destination_name", texture_request["destination_name"])
    task.set_editor_property("automated", True)
    task.set_editor_property("async_", False)
    task.set_editor_property("replace_existing", False)
    task.set_editor_property("replace_existing_settings", False)
    task.set_editor_property("save", False)
    asset_tools.import_asset_tasks([task])

    objects = list(task.get_objects())
    textures = [value for value in objects if isinstance(value, unreal.Texture2D)]
    if len(textures) != 1:
        raise RuntimeError(
            f"expected one Texture2D for {texture_request['role']}, got {len(textures)}"
        )
    texture = textures[0]
    observed_path = package_path(
        str(asset_subsystem.get_path_name_for_loaded_asset(texture))
    )
    if observed_path != expected_path:
        raise RuntimeError(
            f"texture path mismatch for {texture_request['role']}: "
            f"expected {expected_path}, observed {observed_path}"
        )

    settings = ROLE_SETTINGS[texture_request["role"]]
    texture.set_editor_property("srgb", settings["srgb"])
    texture.set_editor_property("compression_settings", settings["compression"])
    if not asset_subsystem.save_loaded_asset(texture, only_if_is_dirty=False):
        raise RuntimeError(f"could not save imported texture: {observed_path}")

    unreal.log(
        f"RENDERMASTER_MATERIAL_STEP texture_imported "
        f"role={texture_request['role']} path={observed_path}"
    )
    return texture, {
        "role": texture_request["role"],
        "source_sha256": texture_request["source_sha256"],
        "engine_path": observed_path,
        "class_name": "Texture2D",
        "srgb": bool(texture.get_editor_property("srgb")),
        "compression_settings": str(texture.get_editor_property("compression_settings")),
    }


def create_material(asset_tools, asset_subsystem, request, textures):
    destination_path = request["destination_path"]
    material_name = request["material_name"]
    material_path = f"{destination_path}/{material_name}"
    if asset_subsystem.does_asset_exist(material_path):
        raise RuntimeError(f"refusing to overwrite existing material asset: {material_path}")

    material = asset_tools.create_asset(
        material_name,
        destination_path,
        unreal.Material,
        unreal.MaterialFactoryNew(),
    )
    if material is None or not isinstance(material, unreal.Material):
        raise RuntimeError(f"Unreal could not create material: {material_path}")

    y_positions = {
        "base_color": -300,
        "normal": -100,
        "roughness": 100,
        "ambient_occlusion": 300,
    }
    for role in ("base_color", "normal", "roughness", "ambient_occlusion"):
        expression = unreal.MaterialEditingLibrary.create_material_expression(
            material,
            unreal.MaterialExpressionTextureSample,
            -500,
            y_positions[role],
        )
        if expression is None:
            raise RuntimeError(f"could not create Texture Sample for {role}")
        expression.set_editor_property("texture", textures[role])
        expression.set_editor_property("sampler_type", ROLE_SETTINGS[role]["sampler"])
        expression.set_editor_property("desc", f"RenderMasterBot {role}")
        if not unreal.MaterialEditingLibrary.connect_material_property(
            expression,
            "",
            ROLE_SETTINGS[role]["property"],
        ):
            raise RuntimeError(f"could not connect {role} to the material output")

    unreal.MaterialEditingLibrary.recompile_material(material)
    if not asset_subsystem.save_loaded_asset(material, only_if_is_dirty=False):
        raise RuntimeError(f"could not save material: {material_path}")
    observed_path = package_path(
        str(asset_subsystem.get_path_name_for_loaded_asset(material))
    )
    if observed_path != material_path:
        raise RuntimeError(
            f"material path mismatch: expected {material_path}, observed {observed_path}"
        )
    unreal.log(f"RENDERMASTER_MATERIAL_STEP material_created path={observed_path}")
    return observed_path


def main():
    request_path = os.environ.get("RENDERMASTER_MATERIAL_IMPORT_REQUEST")
    output_path = os.environ.get("RENDERMASTER_MATERIAL_IMPORT_OUTPUT")
    if not request_path:
        raise RuntimeError("RENDERMASTER_MATERIAL_IMPORT_REQUEST is required")
    if not output_path:
        raise RuntimeError("RENDERMASTER_MATERIAL_IMPORT_OUTPUT is required")

    with open(request_path, "r", encoding="utf-8-sig") as stream:
        request = json.load(stream)
    project_name = os.path.splitext(
        os.path.basename(unreal.Paths.get_project_file_path())
    )[0]
    result = {
        "schema_version": "0.1",
        "status": "failed",
        "project_name": project_name,
        "destination_path": request["destination_path"],
        "material_name": request["material_name"],
        "material_engine_path": None,
        "textures": [],
        "warnings": [],
        "errors": [],
    }
    try:
        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
        asset_subsystem = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
        if asset_tools is None or asset_subsystem is None:
            raise RuntimeError("Unreal asset tools or editor asset subsystem is unavailable")

        material_path = f"{request['destination_path']}/{request['material_name']}"
        expected_paths = [material_path] + [
            f"{request['destination_path']}/{texture['destination_name']}"
            for texture in request["textures"]
        ]
        collisions = [
            path for path in expected_paths if asset_subsystem.does_asset_exist(path)
        ]
        if collisions:
            raise RuntimeError(
                "refusing to overwrite existing Unreal assets: " + ", ".join(collisions)
            )

        loaded_textures = {}
        for texture_request in request["textures"]:
            texture, record = import_texture(
                asset_tools,
                asset_subsystem,
                request["destination_path"],
                texture_request,
            )
            loaded_textures[texture_request["role"]] = texture
            result["textures"].append(record)

        result["material_engine_path"] = create_material(
            asset_tools,
            asset_subsystem,
            request,
            loaded_textures,
        )
        result["status"] = "succeeded"
        write_result(output_path, result)
        unreal.log(
            f"RENDERMASTER_MATERIAL_IMPORT_OK output={output_path} "
            f"material={result['material_engine_path']} textures={len(result['textures'])}"
        )
    except Exception as exc:
        result["errors"].append(str(exc)[:4000])
        write_result(output_path, result)
        unreal.log_error(f"RENDERMASTER_MATERIAL_IMPORT_FAILED: {exc}")
        unreal.log_error(traceback.format_exc())


if __name__ == "__main__":
    main()
