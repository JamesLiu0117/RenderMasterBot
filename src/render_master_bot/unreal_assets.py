"""Headless Unreal Asset Registry scanning and AssetCard conversion."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from render_master_bot.contracts import AssetCard, Dimensions3, SourceReference
from render_master_bot.models import StrictModel, Vector3


class UnrealAssetScanError(RuntimeError):
    """Raised when Unreal cannot produce a trustworthy asset scan."""


class RawUnrealAsset(StrictModel):
    """Minimal deterministic record emitted from Unreal's Python runtime."""

    package_name: str = Field(min_length=1, max_length=500)
    object_path: str = Field(min_length=1, max_length=1000)
    display_name: str = Field(min_length=1, max_length=240)
    class_name: str = Field(min_length=1, max_length=240)
    dimensions_cm: Dimensions3 | None = None
    pivot_offset_cm: Vector3 = Field(default_factory=Vector3)
    material_slots: list[str] = Field(default_factory=list, max_length=64)


class RawUnrealAssetScan(StrictModel):
    """Versioned envelope written by the Unreal-side scanner."""

    schema_version: Literal["0.1"]
    project_name: str = Field(min_length=1, max_length=240)
    path_prefix: str = Field(min_length=1, max_length=500)
    total_assets: int = Field(ge=0)
    selected_assets: int = Field(ge=0)
    assets: list[RawUnrealAsset]
    warnings: list[str] = Field(default_factory=list)


_CLASS_TO_ASSET_TYPE = {
    "staticmesh": "static_mesh",
    "skeletalmesh": "skeletal_mesh",
    "world": "level",
    "levelsequence": "animation",
    "animsequence": "animation",
    "animmontage": "animation",
    "poseasset": "animation",
}


def _identifier(value: str, *, fallback: str = "asset") -> str:
    """Normalize arbitrary Unreal text into the public Identifier grammar."""

    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    normalized = re.sub(r"[^a-z0-9]+", "_", ascii_value.casefold()).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"{fallback}_{normalized}".rstrip("_")
    return normalized[:64]


def asset_type_for_unreal_class(class_name: str) -> str:
    """Map Unreal class names to the bounded AssetCard vocabulary."""

    folded = class_name.casefold()
    compact = re.sub(r"[^a-z0-9]", "", folded)
    if compact in _CLASS_TO_ASSET_TYPE:
        return _CLASS_TO_ASSET_TYPE[compact]
    if "material" in compact:
        return "material"
    if "texture" in compact:
        return "texture"
    if compact.endswith("blueprint"):
        return "blueprint"
    if "animation" in compact or compact.startswith("anim"):
        return "animation"
    if "camera" in compact:
        return "camera"
    if "light" in compact:
        return "light"
    return "other"


def stable_asset_id(package_name: str, display_name: str) -> str:
    """Create a readable, collision-resistant ID that is stable across scans."""

    digest = hashlib.sha256(package_name.encode("utf-8")).hexdigest()[:8]
    base = _identifier(display_name)
    return f"{base[:55]}_{digest}"


def _asset_tags(asset: RawUnrealAsset) -> list[str]:
    candidates = [asset.class_name]
    candidates.extend(part for part in asset.package_name.split("/") if part and part != "Game")
    tags: list[str] = []
    for candidate in candidates:
        tag = _identifier(candidate, fallback="tag")
        if tag not in tags:
            tags.append(tag)
    return tags[:64]


def asset_cards_from_scan(value: dict[str, Any] | RawUnrealAssetScan) -> list[AssetCard]:
    """Validate Unreal output and convert every selected record into an AssetCard."""

    scan = value if isinstance(value, RawUnrealAssetScan) else RawUnrealAssetScan.model_validate(value)
    source_id = _identifier(f"{scan.project_name}_asset_registry", fallback="project")
    cards: list[AssetCard] = []
    for asset in scan.assets:
        source = SourceReference(
            source_id=source_id,
            source_type="generated",
            title=f"{scan.project_name} Unreal Asset Registry scan",
            uri=f"unreal://{scan.project_name}{asset.package_name}",
        )
        cards.append(
            AssetCard(
                asset_id=stable_asset_id(asset.package_name, asset.display_name),
                engine_path=asset.package_name,
                display_name=asset.display_name,
                asset_type=asset_type_for_unreal_class(asset.class_name),
                description=(
                    f"Unreal {asset.class_name} discovered under {scan.path_prefix} "
                    "by the project Asset Registry."
                ),
                tags=_asset_tags(asset),
                dimensions_cm=asset.dimensions_cm,
                pivot_offset_cm=asset.pivot_offset_cm,
                material_slots=asset.material_slots,
                sources=[source],
            )
        )
    return cards


def load_asset_cards(raw_output: str | Path) -> tuple[RawUnrealAssetScan, list[AssetCard]]:
    """Read, validate, and convert one raw Unreal scanner result."""

    path = Path(raw_output)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        scan = RawUnrealAssetScan.model_validate(value)
        cards = asset_cards_from_scan(scan)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise UnrealAssetScanError(f"invalid Unreal asset scan at {path}: {exc}") from exc
    if scan.selected_assets != len(scan.assets):
        raise UnrealAssetScanError(
            "Unreal scan selected_assets does not match the number of emitted records"
        )
    return scan, cards


def run_unreal_asset_scan(
    uproject_path: str | Path,
    *,
    engine_root: str | Path,
    raw_output: str | Path,
    limit: int = 20,
    path_prefix: str = "/Game",
    timeout_seconds: int = 300,
) -> tuple[RawUnrealAssetScan, list[AssetCard]]:
    """Launch UnrealEditor-Cmd, then validate and convert its raw JSON output."""

    project = Path(uproject_path).expanduser().resolve()
    root = Path(engine_root).expanduser().resolve()
    output = Path(raw_output).expanduser().resolve()
    if project.suffix.casefold() != ".uproject" or not project.is_file():
        raise UnrealAssetScanError(f"not an existing .uproject file: {project}")
    if not 1 <= limit <= 1000:
        raise UnrealAssetScanError("limit must be between 1 and 1000")
    if not path_prefix.startswith("/") or ".." in path_prefix:
        raise UnrealAssetScanError("path_prefix must be an Unreal package path such as /Game")

    editor = root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
    if not editor.is_file():
        raise UnrealAssetScanError(f"UnrealEditor-Cmd.exe not found under {root}")
    script = Path(__file__).parent / "unreal_scripts" / "asset_scan.py"
    if not script.is_file():
        raise UnrealAssetScanError(f"bundled Unreal scanner script is missing: {script}")

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            output.unlink()
    except OSError as exc:
        raise UnrealAssetScanError(f"cannot prepare raw output path {output}: {exc}") from exc
    environment = os.environ.copy()
    environment.update(
        {
            "RENDERMASTER_ASSET_SCAN_OUTPUT": str(output),
            "RENDERMASTER_ASSET_SCAN_LIMIT": str(limit),
            "RENDERMASTER_ASSET_SCAN_PATH": path_prefix,
        }
    )
    command = [
        str(editor),
        str(project),
        f"-ExecutePythonScript={script}",
        "-unattended",
        "-nop4",
        "-nosplash",
        "-nullrhi",
        "-stdout",
        "-FullStdOutLogOutput",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UnrealAssetScanError(f"failed to run Unreal asset scan: {exc}") from exc

    if completed.returncode != 0 or not output.is_file():
        diagnostic = "\n".join(
            line
            for line in (completed.stdout + "\n" + completed.stderr).splitlines()
            if "Error:" in line or "RENDERMASTER" in line
        )[-4000:]
        raise UnrealAssetScanError(
            f"Unreal asset scan failed with exit code {completed.returncode}: {diagnostic}"
        )
    return load_asset_cards(output)
