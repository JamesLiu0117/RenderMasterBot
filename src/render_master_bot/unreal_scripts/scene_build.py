"""Build a validated transient scene inside Unreal Editor and emit evidence."""

from __future__ import annotations

import json
import os
import traceback

import unreal


LIGHT_ACTOR_CLASSES = {
    "directional": unreal.DirectionalLight,
    "point": unreal.PointLight,
    "spot": unreal.SpotLight,
    "rect": unreal.RectLight,
}

LIGHT_UNITS = {
    "lumens": unreal.LightUnits.LUMENS,
    "candelas": unreal.LightUnits.CANDELAS,
    "unitless": unreal.LightUnits.UNITLESS,
}


def vector(value):
    return unreal.Vector(
        x=float(value["x"]),
        y=float(value["y"]),
        z=float(value["z"]),
    )


def rotator(value):
    # RenderSpec XYZ is axis based: X=roll, Y=pitch, Z=yaw in Unreal.
    return unreal.Rotator(
        roll=float(value["x"]),
        pitch=float(value["y"]),
        yaw=float(value["z"]),
    )


def apply_scale(actor, transform):
    scale = transform["scale"]
    actor.set_actor_scale3d(vector(scale))


def actor_record(actor, actor_id, actor_kind, materials=None, exposure=None):
    location = actor.get_actor_location()
    rotation = actor.get_actor_rotation()
    scale = actor.get_actor_scale3d()
    return {
        "actor_id": actor_id,
        "actor_kind": actor_kind,
        "actor_name": str(actor.get_name()),
        "class_name": str(actor.get_class().get_name()),
        "transform": {
            "location_cm": {
                "x": float(location.x),
                "y": float(location.y),
                "z": float(location.z),
            },
            "rotation_deg": {
                "x": float(rotation.roll),
                "y": float(rotation.pitch),
                "z": float(rotation.yaw),
            },
            "scale": {
                "x": float(scale.x),
                "y": float(scale.y),
                "z": float(scale.z),
            },
        },
        "materials": materials or [],
        "exposure": exposure,
    }


def package_path(value):
    return value.partition(".")[0]


def apply_materials(actor, value):
    component = actor.get_editor_property("static_mesh_component")
    if component is None:
        raise RuntimeError(
            f"spawned actor has no StaticMeshComponent: {value['object_id']}"
        )
    slot_names = [str(name) for name in component.get_material_slot_names()]
    asset_subsystem = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
    if asset_subsystem is None:
        raise RuntimeError("the Unreal Editor asset subsystem is unavailable")

    records = []
    for assignment in value.get("materials", []):
        slot_index = int(assignment["slot_index"])
        slot_name = assignment["slot_name"]
        if slot_index >= len(slot_names) or slot_names[slot_index] != slot_name:
            raise RuntimeError(
                f"material slot evidence changed for {value['object_id']}: "
                f"expected {slot_name!r} at index {slot_index}"
            )
        material = unreal.load_asset(
            assignment["engine_path"],
            unreal.MaterialInterface,
        )
        if material is None:
            raise RuntimeError(
                f"material could not be loaded: {assignment['engine_path']}"
            )
        component.set_material(slot_index, material)
        observed = component.get_material(slot_index)
        if observed is None:
            raise RuntimeError(
                f"material assignment was not observable: {assignment['engine_path']}"
            )
        observed_path = package_path(
            str(asset_subsystem.get_path_name_for_loaded_asset(observed))
        )
        if observed_path != assignment["engine_path"]:
            raise RuntimeError(
                f"material assignment mismatch for {value['object_id']} slot {slot_name}: "
                f"expected {assignment['engine_path']}, observed {observed_path}"
            )
        records.append({
            "slot_name": slot_name,
            "slot_index": slot_index,
            "asset_id": assignment["asset_id"],
            "engine_path": observed_path,
            "asset_type": "material",
        })
        unreal.log(
            f"RENDERMASTER_SCENE_STEP material_applied id={value['object_id']} "
            f"slot={slot_name} asset={assignment['asset_id']}"
        )
    return records


def spawn_object(actor_subsystem, value, transient=True):
    asset = unreal.EditorAssetLibrary.load_asset(value["engine_path"])
    if asset is None:
        raise RuntimeError(f"asset could not be loaded: {value['engine_path']}")
    if not isinstance(asset, unreal.StaticMesh):
        raise RuntimeError(
            f"resolved asset is {asset.get_class().get_name()}, not StaticMesh: "
            f"{value['engine_path']}"
        )
    transform = value["transform"]
    actor = actor_subsystem.spawn_actor_from_object(
        asset,
        vector(transform["location_cm"]),
        rotator(transform["rotation_deg"]),
        transient=transient,
    )
    if actor is None:
        raise RuntimeError(f"Unreal could not spawn asset: {value['engine_path']}")
    unreal.log(f"RENDERMASTER_SCENE_STEP object_spawned id={value['object_id']}")
    apply_scale(actor, transform)
    unreal.log(f"RENDERMASTER_SCENE_STEP object_scaled id={value['object_id']}")
    actor.set_actor_hidden_in_game(not bool(value["visible"]))
    unreal.log(f"RENDERMASTER_SCENE_STEP object_visibility id={value['object_id']}")
    materials = apply_materials(actor, value)
    record = actor_record(actor, value["object_id"], "static_mesh", materials)
    unreal.log(f"RENDERMASTER_SCENE_STEP object_recorded id={value['object_id']}")
    return record


def apply_camera_exposure(component, value):
    exposure = value.get("exposure", {"mode": "auto", "fixed_ev100": None})
    extended = bool(unreal.SystemLibrary.get_console_variable_bool_value(
        "r.DefaultFeature.AutoExposure.ExtendDefaultLuminanceRange"
    ))
    if exposure["mode"] == "auto":
        component.set_editor_property("post_process_blend_weight", 0.0)
        return {
            "mode": "auto",
            "fixed_ev100": None,
            "extended_luminance_range": extended,
        }

    if not extended:
        raise RuntimeError(
            "fixed EV100 requires ExtendDefaultLuminanceRange in Unreal project settings"
        )
    fixed_ev100 = float(exposure["fixed_ev100"])
    settings = component.get_editor_property("post_process_settings")
    settings.set_editor_property("override_auto_exposure_method", True)
    settings.set_editor_property(
        "auto_exposure_method",
        unreal.AutoExposureMethod.AEM_HISTOGRAM,
    )
    settings.set_editor_property("override_auto_exposure_min_brightness", True)
    settings.set_editor_property("override_auto_exposure_max_brightness", True)
    settings.set_editor_property("auto_exposure_min_brightness", fixed_ev100)
    settings.set_editor_property("auto_exposure_max_brightness", fixed_ev100)
    component.set_editor_property("post_process_settings", settings)
    component.set_editor_property("post_process_blend_weight", 1.0)

    observed = component.get_editor_property("post_process_settings")
    observed_min = float(observed.get_editor_property("auto_exposure_min_brightness"))
    observed_max = float(observed.get_editor_property("auto_exposure_max_brightness"))
    observed_weight = float(component.get_editor_property("post_process_blend_weight"))
    if (
        abs(observed_min - fixed_ev100) > 1e-4
        or abs(observed_max - fixed_ev100) > 1e-4
        or abs(observed_weight - 1.0) > 1e-4
    ):
        raise RuntimeError(
            "fixed exposure readback mismatch: "
            f"requested={fixed_ev100} min={observed_min} max={observed_max} "
            f"blend={observed_weight}"
        )
    return {
        "mode": "fixed",
        "fixed_ev100": observed_min,
        "extended_luminance_range": extended,
    }


def spawn_camera(actor_subsystem, value, transient=True, return_actor=False):
    transform = value["transform"]
    actor = actor_subsystem.spawn_actor_from_class(
        unreal.CineCameraActor,
        vector(transform["location_cm"]),
        rotator(transform["rotation_deg"]),
        transient=transient,
    )
    if actor is None:
        raise RuntimeError("Unreal could not spawn the CineCameraActor")
    unreal.log(f"RENDERMASTER_SCENE_STEP camera_spawned id={value['camera_id']}")
    apply_scale(actor, transform)
    component = actor.get_cine_camera_component()
    component.set_editor_property("current_focal_length", float(value["focal_length_mm"]))
    component.set_editor_property("current_aperture", float(value["aperture_f_stop"]))
    if value.get("focus_distance_cm") is not None:
        focus = component.get_editor_property("focus_settings")
        focus.set_editor_property("focus_method", unreal.CameraFocusMethod.MANUAL)
        focus.set_editor_property("manual_focus_distance", float(value["focus_distance_cm"]))
        component.set_editor_property("focus_settings", focus)
    exposure = apply_camera_exposure(component, value)
    unreal.log(
        f"RENDERMASTER_SCENE_STEP camera_exposure id={value['camera_id']} "
        f"mode={exposure['mode']} ev100={exposure['fixed_ev100']}"
    )
    record = actor_record(actor, value["camera_id"], "camera", exposure=exposure)
    if return_actor:
        return actor, record
    return record


def spawn_light(actor_subsystem, value, transient=True):
    transform = value["transform"]
    kind = value["kind"]
    actor = actor_subsystem.spawn_actor_from_class(
        LIGHT_ACTOR_CLASSES[kind],
        vector(transform["location_cm"]),
        rotator(transform["rotation_deg"]),
        transient=transient,
    )
    if actor is None:
        raise RuntimeError(f"Unreal could not spawn {kind} light")
    unreal.log(f"RENDERMASTER_SCENE_STEP light_spawned id={value['light_id']} kind={kind}")
    apply_scale(actor, transform)
    component = actor.get_editor_property("light_component")
    if kind != "directional":
        component.set_editor_property("intensity_units", LIGHT_UNITS[value["intensity_unit"]])
    component.set_intensity(float(value["intensity"]))
    component.set_cast_shadows(bool(value["cast_shadows"]))
    red, green, blue = value["color_rgb"]
    component.set_light_color(
        unreal.LinearColor(float(red), float(green), float(blue), 1.0),
        srgb=False,
    )
    return actor_record(actor, value["light_id"], kind)


def write_result(output_path, payload):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)


def main():
    request_path = os.environ.get("RENDERMASTER_SCENE_BUILD_REQUEST")
    output_path = os.environ.get("RENDERMASTER_SCENE_BUILD_OUTPUT")
    if not request_path:
        raise RuntimeError("RENDERMASTER_SCENE_BUILD_REQUEST is required")
    if not output_path:
        raise RuntimeError("RENDERMASTER_SCENE_BUILD_OUTPUT is required")

    with open(request_path, "r", encoding="utf-8-sig") as stream:
        request = json.load(stream)
    project_name = os.path.splitext(
        os.path.basename(unreal.Paths.get_project_file_path())
    )[0]
    result = {
        "schema_version": "0.1",
        "status": "failed",
        "project_name": project_name,
        "world_name": None,
        "scene_name": request["scene_name"],
        "render_spec_sha256": request["render_spec_sha256"],
        "actors": [],
        "warnings": [],
        "errors": [],
    }
    try:
        actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        editor_subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        world = editor_subsystem.get_editor_world()
        if actor_subsystem is None or world is None:
            raise RuntimeError("the Unreal Editor world or actor subsystem is unavailable")
        result["world_name"] = str(world.get_name())

        for scene_object in request["objects"]:
            result["actors"].append(spawn_object(actor_subsystem, scene_object))
        result["actors"].append(spawn_camera(actor_subsystem, request["camera"]))
        for light in request["lights"]:
            result["actors"].append(spawn_light(actor_subsystem, light))

        result["status"] = "succeeded"
        write_result(output_path, result)
        unreal.log(
            f"RENDERMASTER_SCENE_BUILD_OK output={output_path} "
            f"scene={request['scene_name']} actors={len(result['actors'])} "
            f"world={result['world_name']}"
        )
    except Exception as exc:
        result["errors"].append(str(exc)[:4000])
        write_result(output_path, result)
        unreal.log_error(f"RENDERMASTER_SCENE_BUILD_FAILED: {exc}")
        unreal.log_error(traceback.format_exc())


if __name__ == "__main__":
    main()
