"""Validated Unreal material-parameter inspection and instance creation."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from render_master_bot.models import StrictModel


_UNREAL_ASSET_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,99}$")


class MaterialVariantError(RuntimeError):
    """Raised when an Unreal material variant cannot be created safely."""


class ScalarParameterOverride(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    value: float = Field(ge=-1_000_000.0, le=1_000_000.0, allow_inf_nan=False)


class VectorParameterOverride(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    r: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    g: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    b: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    a: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)


class MaterialParameterInventory(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    status: Literal["succeeded", "failed"]
    project_name: str = Field(min_length=1, max_length=240)
    material_path: str = Field(min_length=1, max_length=500)
    scalar_parameters: list[str] = Field(default_factory=list, max_length=128)
    vector_parameters: list[str] = Field(default_factory=list, max_length=128)
    errors: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def status_matches_evidence(self) -> "MaterialParameterInventory":
        if self.status == "succeeded" and self.errors:
            raise ValueError("successful parameter inventory cannot contain errors")
        if self.status == "failed" and not self.errors:
            raise ValueError("failed parameter inventory requires an error")
        return self


class MaterialVariantRequest(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    parent_material_path: str = Field(min_length=1, max_length=500)
    destination_path: str = Field(min_length=1, max_length=500)
    instance_name: str = Field(min_length=1, max_length=100)
    scalar_parameters: list[ScalarParameterOverride] = Field(
        default_factory=list,
        max_length=32,
    )
    vector_parameters: list[VectorParameterOverride] = Field(
        default_factory=list,
        max_length=32,
    )

    @model_validator(mode="after")
    def overrides_are_bounded_and_unique(self) -> "MaterialVariantRequest":
        if not self.scalar_parameters and not self.vector_parameters:
            raise ValueError("material variant requires at least one parameter override")
        names = [
            value.name.casefold()
            for value in [*self.scalar_parameters, *self.vector_parameters]
        ]
        if len(names) != len(set(names)):
            raise ValueError("material variant parameter names must be unique")
        return self


class MaterialVariantResult(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    status: Literal["succeeded", "failed"]
    project_name: str = Field(min_length=1, max_length=240)
    parent_material_path: str = Field(min_length=1, max_length=500)
    instance_engine_path: str | None = Field(default=None, max_length=500)
    scalar_parameters: list[ScalarParameterOverride] = Field(default_factory=list)
    vector_parameters: list[VectorParameterOverride] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def status_matches_evidence(self) -> "MaterialVariantResult":
        if self.status == "succeeded":
            if self.errors or self.instance_engine_path is None:
                raise ValueError("successful material variant requires a path and no errors")
        elif not self.errors:
            raise ValueError("failed material variant requires an error")
        return self


def _safe_package_path(value: str, *, label: str) -> str:
    path = value.partition(".")[0].rstrip("/")
    if not path.startswith("/Game/") or "\\" in path or ".." in path or "//" in path:
        raise MaterialVariantError(f"unsafe Unreal {label} path: {value!r}")
    return path


def _safe_asset_name(value: str) -> str:
    if not _UNREAL_ASSET_NAME.fullmatch(value):
        raise MaterialVariantError(
            f"unsafe Unreal material instance name {value!r}; use letters, digits, and underscores"
        )
    return value


def prepare_material_variant_request(
    *,
    parent_material_path: str,
    destination_path: str,
    instance_name: str,
    scalar_parameters: list[ScalarParameterOverride] | None = None,
    vector_parameters: list[VectorParameterOverride] | None = None,
) -> MaterialVariantRequest:
    return MaterialVariantRequest(
        parent_material_path=_safe_package_path(parent_material_path, label="parent material"),
        destination_path=_safe_package_path(destination_path, label="destination"),
        instance_name=_safe_asset_name(instance_name),
        scalar_parameters=scalar_parameters or [],
        vector_parameters=vector_parameters or [],
    )


def _editor_and_project(
    uproject_path: str | Path,
    engine_root: str | Path,
) -> tuple[Path, Path]:
    project = Path(uproject_path).expanduser().resolve()
    editor = (
        Path(engine_root).expanduser().resolve()
        / "Engine"
        / "Binaries"
        / "Win64"
        / "UnrealEditor-Cmd.exe"
    )
    if project.suffix.casefold() != ".uproject" or not project.is_file():
        raise MaterialVariantError(f"not an existing .uproject file: {project}")
    if not editor.is_file():
        raise MaterialVariantError(f"UnrealEditor-Cmd.exe not found: {editor}")
    return editor, project


def _run_script(
    *,
    editor: Path,
    project: Path,
    script: Path,
    environment: dict[str, str],
    output: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    if not 1 <= timeout_seconds <= 3600:
        raise MaterialVariantError("timeout must be between 1 and 3600 seconds")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
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
        return subprocess.run(
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
        raise MaterialVariantError(f"failed to run Unreal material operation: {exc}") from exc


def inspect_material_parameters(
    uproject_path: str | Path,
    *,
    engine_root: str | Path,
    material_path: str,
    output: str | Path,
    timeout_seconds: int = 300,
) -> MaterialParameterInventory:
    editor, project = _editor_and_project(uproject_path, engine_root)
    output_path = Path(output).expanduser().resolve()
    script = Path(__file__).parent / "unreal_scripts" / "material_parameter_scan.py"
    environment = os.environ.copy()
    environment.update({
        "RENDERMASTER_MATERIAL_PARAMETER_PATH": _safe_package_path(
            material_path,
            label="material",
        ),
        "RENDERMASTER_MATERIAL_PARAMETER_OUTPUT": str(output_path),
    })
    completed = _run_script(
        editor=editor,
        project=project,
        script=script,
        environment=environment,
        output=output_path,
        timeout_seconds=timeout_seconds,
    )
    try:
        result = MaterialParameterInventory.model_validate_json(
            output_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise MaterialVariantError(f"invalid material parameter evidence: {exc}") from exc
    if completed.returncode != 0 or result.status != "succeeded":
        raise MaterialVariantError("material parameter inspection failed: " + "; ".join(result.errors))
    return result


def create_material_variant(
    uproject_path: str | Path,
    *,
    engine_root: str | Path,
    parent_material_path: str,
    destination_path: str,
    instance_name: str,
    scalar_parameters: list[ScalarParameterOverride] | None,
    vector_parameters: list[VectorParameterOverride] | None,
    output: str | Path,
    timeout_seconds: int = 300,
) -> tuple[MaterialVariantRequest, MaterialVariantResult]:
    editor, project = _editor_and_project(uproject_path, engine_root)
    output_path = Path(output).expanduser().resolve()
    request = prepare_material_variant_request(
        parent_material_path=parent_material_path,
        destination_path=destination_path,
        instance_name=instance_name,
        scalar_parameters=scalar_parameters,
        vector_parameters=vector_parameters,
    )
    script = Path(__file__).parent / "unreal_scripts" / "material_variant.py"
    with tempfile.TemporaryDirectory(prefix="render-master-variant-") as directory:
        request_path = Path(directory) / "material_variant_request.json"
        request_path.write_text(request.model_dump_json(indent=2) + "\n", encoding="utf-8")
        environment = os.environ.copy()
        environment.update({
            "RENDERMASTER_MATERIAL_VARIANT_REQUEST": str(request_path),
            "RENDERMASTER_MATERIAL_VARIANT_OUTPUT": str(output_path),
        })
        completed = _run_script(
            editor=editor,
            project=project,
            script=script,
            environment=environment,
            output=output_path,
            timeout_seconds=timeout_seconds,
        )
    try:
        result = MaterialVariantResult.model_validate_json(
            output_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise MaterialVariantError(f"invalid material variant evidence: {exc}") from exc
    expected_path = f"{request.destination_path}/{request.instance_name}"
    if completed.returncode != 0 or result.status != "succeeded":
        raise MaterialVariantError("material variant failed: " + "; ".join(result.errors))
    if result.parent_material_path != request.parent_material_path:
        raise MaterialVariantError("Unreal returned a different parent material path")
    if result.instance_engine_path != expected_path:
        raise MaterialVariantError("Unreal returned a different material instance path")
    if result.scalar_parameters != request.scalar_parameters:
        raise MaterialVariantError("Unreal scalar parameter evidence differs from the request")
    if result.vector_parameters != request.vector_parameters:
        raise MaterialVariantError("Unreal vector parameter evidence differs from the request")
    return request, result
