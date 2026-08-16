"""Run inside Unreal Editor to create one bounded MaterialInstanceConstant."""

import json
import os
import traceback

import unreal


def write_result(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)


def main():
    request_path = os.environ["RENDERMASTER_MATERIAL_VARIANT_REQUEST"]
    output_path = os.environ["RENDERMASTER_MATERIAL_VARIANT_OUTPUT"]
    with open(request_path, "r", encoding="utf-8-sig") as stream:
        request = json.load(stream)
    project_name = os.path.splitext(os.path.basename(unreal.Paths.get_project_file_path()))[0]
    result = {
        "schema_version": "0.1",
        "status": "failed",
        "project_name": project_name,
        "parent_material_path": request["parent_material_path"],
        "instance_engine_path": None,
        "scalar_parameters": [],
        "vector_parameters": [],
        "errors": [],
    }
    try:
        parent = unreal.load_asset(request["parent_material_path"])
        if parent is None or not isinstance(parent, unreal.MaterialInterface):
            raise RuntimeError("parent path is not a loadable MaterialInterface")
        scalar_names = {
            str(value) for value in unreal.MaterialEditingLibrary.get_scalar_parameter_names(parent)
        }
        vector_names = {
            str(value) for value in unreal.MaterialEditingLibrary.get_vector_parameter_names(parent)
        }
        missing_scalars = [
            value["name"] for value in request["scalar_parameters"] if value["name"] not in scalar_names
        ]
        missing_vectors = [
            value["name"] for value in request["vector_parameters"] if value["name"] not in vector_names
        ]
        if missing_scalars or missing_vectors:
            raise RuntimeError(
                "requested parameters are not exposed by the parent: "
                + ", ".join(missing_scalars + missing_vectors)
            )

        asset_subsystem = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
        expected_path = f"{request['destination_path']}/{request['instance_name']}"
        if asset_subsystem.does_asset_exist(expected_path):
            raise RuntimeError(f"refusing to overwrite existing asset: {expected_path}")
        factory = unreal.MaterialInstanceConstantFactoryNew()
        instance = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            request["instance_name"],
            request["destination_path"],
            unreal.MaterialInstanceConstant,
            factory,
        )
        if instance is None or not isinstance(instance, unreal.MaterialInstanceConstant):
            raise RuntimeError("Unreal could not create the material instance")
        instance.set_editor_property("parent", parent)
        unreal.MaterialEditingLibrary.update_material_instance(instance)
        instance_scalar_names = {
            str(value) for value in unreal.MaterialEditingLibrary.get_scalar_parameter_names(instance)
        }
        instance_vector_names = {
            str(value) for value in unreal.MaterialEditingLibrary.get_vector_parameter_names(instance)
        }
        missing_instance_parameters = [
            value["name"]
            for value in request["scalar_parameters"]
            if value["name"] not in instance_scalar_names
        ] + [
            value["name"]
            for value in request["vector_parameters"]
            if value["name"] not in instance_vector_names
        ]
        if missing_instance_parameters:
            raise RuntimeError(
                "new material instance did not inherit requested parameters: "
                + ", ".join(missing_instance_parameters)
            )
        for value in request["scalar_parameters"]:
            unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(
                instance,
                unreal.Name(value["name"]),
                float(value["value"]),
            )
            observed = unreal.MaterialEditingLibrary.get_material_instance_scalar_parameter_value(
                instance,
                unreal.Name(value["name"]),
            )
            if abs(float(observed) - float(value["value"])) > 0.00001:
                raise RuntimeError(
                    f"scalar read-back mismatch for {value['name']}: "
                    f"requested {value['value']}, observed {observed}"
                )
        for value in request["vector_parameters"]:
            color = unreal.LinearColor(value["r"], value["g"], value["b"], value["a"])
            unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(
                instance,
                unreal.Name(value["name"]),
                color,
            )
            observed = unreal.MaterialEditingLibrary.get_material_instance_vector_parameter_value(
                instance,
                unreal.Name(value["name"]),
            )
            requested_values = (value["r"], value["g"], value["b"], value["a"])
            observed_values = (observed.r, observed.g, observed.b, observed.a)
            if any(
                abs(float(actual) - float(expected)) > 0.00001
                for actual, expected in zip(observed_values, requested_values)
            ):
                raise RuntimeError(
                    f"vector read-back mismatch for {value['name']}: "
                    f"requested {requested_values}, observed {observed_values}"
                )
        unreal.MaterialEditingLibrary.update_material_instance(instance)
        if not asset_subsystem.save_loaded_asset(instance, only_if_is_dirty=False):
            raise RuntimeError("could not save the material instance")
        result["status"] = "succeeded"
        result["instance_engine_path"] = expected_path
        result["scalar_parameters"] = request["scalar_parameters"]
        result["vector_parameters"] = request["vector_parameters"]
        unreal.log(f"RENDERMASTER_MATERIAL_VARIANT_OK path={expected_path}")
    except Exception as exc:
        result["errors"].append(str(exc))
        unreal.log_error(traceback.format_exc())
    write_result(output_path, result)
    if result["status"] != "succeeded":
        raise RuntimeError("material variant creation failed")


main()
