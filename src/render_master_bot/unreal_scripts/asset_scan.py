"""Run inside Unreal Editor to export a bounded sample from /Game."""

from __future__ import annotations

import json
import os

import unreal


CLASS_GROUPS = (
    "static_mesh",
    "skeletal_mesh",
    "material",
    "texture",
    "blueprint",
    "level",
    "animation",
    "other",
)


def class_name(asset_data):
    return str(asset_data.asset_class_path.asset_name)


def class_group(name):
    compact = "".join(character for character in name.casefold() if character.isalnum())
    if compact == "staticmesh":
        return "static_mesh"
    if compact == "skeletalmesh":
        return "skeletal_mesh"
    if compact == "material" or compact.startswith("materialinstance"):
        return "material"
    if "texture" in compact:
        return "texture"
    if compact.endswith("blueprint"):
        return "blueprint"
    if compact == "world":
        return "level"
    if compact in {"levelsequence", "animsequence", "animmontage", "poseasset"}:
        return "animation"
    return "other"


def select_balanced(asset_data, limit):
    groups = {name: [] for name in CLASS_GROUPS}
    for asset in sorted(asset_data, key=lambda item: str(item.package_name).casefold()):
        groups[class_group(class_name(asset))].append(asset)

    selected = []
    index = 0
    while len(selected) < limit:
        added = False
        for name in CLASS_GROUPS:
            values = groups[name]
            if index < len(values):
                selected.append(values[index])
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
        index += 1
    return selected


def static_mesh_metadata(asset_data):
    loaded = asset_data.get_asset()
    if loaded is None or not isinstance(loaded, unreal.StaticMesh):
        return {}, ["StaticMesh could not be loaded"]

    warnings = []
    result = {}
    try:
        bounds = loaded.get_bounding_box()
        minimum = bounds.min
        maximum = bounds.max
        dimensions = {
            "x": float(maximum.x - minimum.x),
            "y": float(maximum.y - minimum.y),
            "z": float(maximum.z - minimum.z),
        }
        if all(value > 0.0 for value in dimensions.values()):
            result["dimensions_cm"] = dimensions
        else:
            warnings.append("StaticMesh has degenerate bounds; dimensions were omitted")
        result["pivot_offset_cm"] = {
            "x": float((minimum.x + maximum.x) / 2.0),
            "y": float((minimum.y + maximum.y) / 2.0),
            "z": float((minimum.z + maximum.z) / 2.0),
        }
    except Exception as exc:
        warnings.append(f"Could not read StaticMesh bounds: {exc}")

    try:
        slots = []
        for material in loaded.get_editor_property("static_materials"):
            name = str(material.material_slot_name)
            if name and name not in slots:
                slots.append(name)
        result["material_slots"] = slots[:64]
    except Exception as exc:
        warnings.append(f"Could not read StaticMesh material slots: {exc}")
    return result, warnings


def record_for(asset_data):
    name = class_name(asset_data)
    package_name = str(asset_data.package_name)
    display_name = str(asset_data.asset_name)
    record = {
        "package_name": package_name,
        "object_path": f"{package_name}.{display_name}",
        "display_name": display_name,
        "class_name": name,
        "pivot_offset_cm": {"x": 0.0, "y": 0.0, "z": 0.0},
        "material_slots": [],
    }
    warnings = []
    if class_group(name) == "static_mesh":
        metadata, warnings = static_mesh_metadata(asset_data)
        record.update(metadata)
    return record, [f"{package_name}: {warning}" for warning in warnings]


def main():
    output_path = os.environ.get("RENDERMASTER_ASSET_SCAN_OUTPUT")
    if not output_path:
        raise RuntimeError("RENDERMASTER_ASSET_SCAN_OUTPUT is required")
    limit = int(os.environ.get("RENDERMASTER_ASSET_SCAN_LIMIT", "20"))
    path_prefix = os.environ.get("RENDERMASTER_ASSET_SCAN_PATH", "/Game")

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    registry.search_all_assets(True)
    registry.wait_for_completion()
    all_assets = registry.get_assets_by_path(
        path_prefix,
        recursive=True,
        include_only_on_disk_assets=True,
    )
    selected = select_balanced(all_assets, limit)
    records = []
    warnings = []
    for asset_data in selected:
        record, record_warnings = record_for(asset_data)
        records.append(record)
        warnings.extend(record_warnings)

    payload = {
        "schema_version": "0.1",
        "project_name": os.path.splitext(
            os.path.basename(unreal.Paths.get_project_file_path())
        )[0],
        "path_prefix": path_prefix,
        "total_assets": len(all_assets),
        "selected_assets": len(records),
        "assets": records,
        "warnings": warnings,
    }
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
    unreal.log(
        f"RENDERMASTER_ASSET_SCAN_OK output={output_path} "
        f"total={len(all_assets)} selected={len(records)} warnings={len(warnings)}"
    )


main()
