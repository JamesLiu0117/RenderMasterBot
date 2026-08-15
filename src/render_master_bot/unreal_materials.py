"""Validated PBR texture import and material creation boundary for Unreal Editor."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from render_master_bot.contracts import Sha256
from render_master_bot.models import StrictModel


TextureRole = Literal["base_color", "normal", "roughness", "ambient_occlusion"]
REQUIRED_TEXTURE_ROLES = {
    "base_color",
    "normal",
    "roughness",
    "ambient_occlusion",
}
_SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tga", ".exr"}
_UNREAL_ASSET_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,99}$")


class UnrealMaterialImportError(RuntimeError):
    """Raised when a PBR material cannot be imported with trustworthy evidence."""


class PbrTextureInput(StrictModel):
    """One source image and its deterministic Unreal import identity."""

    role: TextureRole
    source_path: str = Field(min_length=1, max_length=1000)
    source_sha256: Sha256
    destination_name: str = Field(min_length=1, max_length=100)


class PbrMaterialImportRequest(StrictModel):
    """Private request consumed by the bundled Unreal material importer."""

    schema_version: Literal["0.1"] = "0.1"
    destination_path: str = Field(min_length=1, max_length=500)
    material_name: str = Field(min_length=1, max_length=100)
    textures: list[PbrTextureInput] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def texture_roles_are_complete(self) -> "PbrMaterialImportRequest":
        roles = [texture.role for texture in self.textures]
        if set(roles) != REQUIRED_TEXTURE_ROLES or len(roles) != len(set(roles)):
            raise ValueError(
                "textures must contain exactly one base_color, normal, roughness, "
                "and ambient_occlusion map"
            )
        names = [texture.destination_name.casefold() for texture in self.textures]
        if len(names) != len(set(names)):
            raise ValueError("texture destination names must be unique")
        return self


class ImportedTextureRecord(StrictModel):
    """Texture asset observed after Unreal import and configuration."""

    role: TextureRole
    source_sha256: Sha256
    engine_path: str = Field(min_length=1, max_length=500)
    class_name: Literal["Texture2D"]
    srgb: bool
    compression_settings: str = Field(min_length=1, max_length=120)


class PbrMaterialImportResult(StrictModel):
    """Versioned evidence emitted by the Unreal-side material importer."""

    schema_version: Literal["0.1"] = "0.1"
    status: Literal["succeeded", "failed"]
    project_name: str = Field(min_length=1, max_length=240)
    destination_path: str = Field(min_length=1, max_length=500)
    material_name: str = Field(min_length=1, max_length=100)
    material_engine_path: str | None = Field(default=None, max_length=500)
    textures: list[ImportedTextureRecord] = Field(default_factory=list, max_length=4)
    warnings: list[str] = Field(default_factory=list, max_length=64)
    errors: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def status_matches_evidence(self) -> "PbrMaterialImportResult":
        if self.status == "succeeded":
            if self.errors:
                raise ValueError("a successful material import cannot contain errors")
            if self.material_engine_path is None:
                raise ValueError("a successful material import requires a material path")
            roles = [texture.role for texture in self.textures]
            if set(roles) != REQUIRED_TEXTURE_ROLES or len(roles) != len(set(roles)):
                raise ValueError("a successful material import requires all four textures")
        elif not self.errors:
            raise ValueError("a failed material import must contain at least one error")
        return self


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_destination_path(value: str) -> str:
    path = value.rstrip("/")
    if (
        not path.startswith("/Game/")
        or "\\" in path
        or ".." in path
        or "//" in path
    ):
        raise UnrealMaterialImportError(
            f"unsafe Unreal destination path: {value!r}; expected /Game/..."
        )
    return path


def _safe_asset_name(value: str, *, label: str) -> str:
    if not _UNREAL_ASSET_NAME.fullmatch(value):
        raise UnrealMaterialImportError(
            f"unsafe Unreal {label} name {value!r}; use letters, digits, and underscores"
        )
    return value


def prepare_pbr_material_import_request(
    *,
    destination_path: str,
    material_name: str,
    base_color: str | Path,
    normal: str | Path,
    roughness: str | Path,
    ambient_occlusion: str | Path,
) -> PbrMaterialImportRequest:
    """Validate source images and freeze their hashes before Unreal launches."""

    sources = {
        "base_color": Path(base_color).expanduser().resolve(),
        "normal": Path(normal).expanduser().resolve(),
        "roughness": Path(roughness).expanduser().resolve(),
        "ambient_occlusion": Path(ambient_occlusion).expanduser().resolve(),
    }
    resolved_values = list(sources.values())
    if len(set(resolved_values)) != len(resolved_values):
        raise UnrealMaterialImportError("every PBR role must use a distinct source image")

    textures: list[PbrTextureInput] = []
    for role, path in sources.items():
        if not path.is_file():
            raise UnrealMaterialImportError(f"{role} image does not exist: {path}")
        if path.suffix.casefold() not in _SUPPORTED_IMAGE_EXTENSIONS:
            raise UnrealMaterialImportError(
                f"unsupported {role} image extension {path.suffix!r}: {path}"
            )
        if path.stat().st_size <= 0:
            raise UnrealMaterialImportError(f"{role} image is empty: {path}")
        destination_name = _safe_asset_name(path.stem, label=f"{role} texture")
        textures.append(
            PbrTextureInput(
                role=role,
                source_path=str(path),
                source_sha256=_sha256(path),
                destination_name=destination_name,
            )
        )

    return PbrMaterialImportRequest(
        destination_path=_safe_destination_path(destination_path),
        material_name=_safe_asset_name(material_name, label="material"),
        textures=textures,
    )


def load_pbr_material_import_result(path: str | Path) -> PbrMaterialImportResult:
    """Read and strictly validate evidence emitted from Unreal."""

    result_path = Path(path)
    try:
        return PbrMaterialImportResult.model_validate_json(
            result_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise UnrealMaterialImportError(
            f"invalid Unreal material import result at {result_path}: {exc}"
        ) from exc


def _validate_result(
    request: PbrMaterialImportRequest,
    result: PbrMaterialImportResult,
) -> None:
    if result.status != "succeeded":
        raise UnrealMaterialImportError(
            "Unreal material import failed: " + "; ".join(result.errors)
        )
    expected_material_path = f"{request.destination_path}/{request.material_name}"
    if result.destination_path != request.destination_path:
        raise UnrealMaterialImportError("Unreal returned a different destination path")
    if result.material_name != request.material_name:
        raise UnrealMaterialImportError("Unreal returned a different material name")
    if result.material_engine_path != expected_material_path:
        raise UnrealMaterialImportError(
            "Unreal returned a different material asset path: "
            f"{result.material_engine_path!r}"
        )

    expected = {
        texture.role: (
            texture.source_sha256,
            f"{request.destination_path}/{texture.destination_name}",
        )
        for texture in request.textures
    }
    observed = {
        texture.role: (texture.source_sha256, texture.engine_path)
        for texture in result.textures
    }
    if observed != expected:
        raise UnrealMaterialImportError(
            "Unreal texture evidence does not match the frozen import request"
        )


def _diagnostic_output(completed: subprocess.CompletedProcess[str]) -> str:
    lines = [
        line
        for line in (completed.stdout + "\n" + completed.stderr).splitlines()
        if "Error:" in line or "RENDERMASTER" in line or "LogPython: Error" in line
    ]
    return "\n".join(lines)[-4000:]


def run_unreal_pbr_material_import(
    uproject_path: str | Path,
    *,
    engine_root: str | Path,
    destination_path: str,
    material_name: str,
    base_color: str | Path,
    normal: str | Path,
    roughness: str | Path,
    ambient_occlusion: str | Path,
    output: str | Path,
    timeout_seconds: int = 300,
) -> tuple[PbrMaterialImportRequest, PbrMaterialImportResult]:
    """Launch Unreal, build one PBR material, and verify returned asset evidence."""

    project = Path(uproject_path).expanduser().resolve()
    root = Path(engine_root).expanduser().resolve()
    result_path = Path(output).expanduser().resolve()
    if project.suffix.casefold() != ".uproject" or not project.is_file():
        raise UnrealMaterialImportError(f"not an existing .uproject file: {project}")
    if not 1 <= timeout_seconds <= 3600:
        raise UnrealMaterialImportError("timeout must be between 1 and 3600 seconds")

    editor = root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
    if not editor.is_file():
        raise UnrealMaterialImportError(f"UnrealEditor-Cmd.exe not found under {root}")
    script = Path(__file__).parent / "unreal_scripts" / "material_import.py"
    if not script.is_file():
        raise UnrealMaterialImportError(f"bundled Unreal material importer is missing: {script}")

    request = prepare_pbr_material_import_request(
        destination_path=destination_path,
        material_name=material_name,
        base_color=base_color,
        normal=normal,
        roughness=roughness,
        ambient_occlusion=ambient_occlusion,
    )
    try:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        if result_path.exists():
            result_path.unlink()
    except OSError as exc:
        raise UnrealMaterialImportError(
            f"cannot prepare material import output {result_path}: {exc}"
        ) from exc

    with tempfile.TemporaryDirectory(prefix="render-master-material-") as directory:
        request_path = Path(directory) / "material_import_request.json"
        request_path.write_text(request.model_dump_json(indent=2) + "\n", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "RENDERMASTER_MATERIAL_IMPORT_REQUEST": str(request_path),
                "RENDERMASTER_MATERIAL_IMPORT_OUTPUT": str(result_path),
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
            raise UnrealMaterialImportError(
                f"failed to run Unreal material import: {exc}"
            ) from exc

    if not result_path.is_file():
        raise UnrealMaterialImportError(
            f"Unreal material import produced no result (exit {completed.returncode}): "
            f"{_diagnostic_output(completed)}"
        )
    result = load_pbr_material_import_result(result_path)
    if completed.returncode != 0:
        raise UnrealMaterialImportError(
            f"Unreal material import exited with code {completed.returncode}: "
            f"{_diagnostic_output(completed)}"
        )
    _validate_result(request, result)
    return request, result
