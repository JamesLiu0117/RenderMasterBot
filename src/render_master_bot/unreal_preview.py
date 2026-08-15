"""Host-side orchestration for one-frame Unreal preview rendering."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field, TypeAdapter, ValidationError

from render_master_bot.asset_index import AssetIndexError, load_asset_card_catalog
from render_master_bot.contracts import ArtifactRecord, RunManifest, RunTiming
from render_master_bot.models import Identifier, RenderSpec
from render_master_bot.unreal_executor import (
    UnrealSceneBuildError,
    UnrealSceneBuildResult,
    _diagnostic_output,
    _validate_observed_result,
    resolve_scene_build_request,
)


class UnrealPreviewError(RuntimeError):
    """Raised when a preview run cannot produce trustworthy artifacts."""


class UnrealPreviewResult(UnrealSceneBuildResult):
    """Unreal-side result extended with physical preview file paths."""

    preview_files: list[str] = Field(default_factory=list, max_length=16)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(role: str, path: Path, run_directory: Path) -> ArtifactRecord:
    relative = path.resolve().relative_to(run_directory.resolve()).as_posix()
    return ArtifactRecord(role=role, path=relative, sha256=_sha256_file(path))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_manifest(path: Path, manifest: RunManifest) -> None:
    _write_json(path, manifest.model_dump(mode="json"))


def _load_result(path: Path) -> UnrealPreviewResult:
    try:
        return UnrealPreviewResult.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValidationError) as exc:
        raise UnrealPreviewError(f"invalid Unreal preview result at {path}: {exc}") from exc


def _validate_preview_files(
    result: UnrealPreviewResult,
    run_directory: Path,
) -> list[Path]:
    preview_root = (run_directory / "preview").resolve()
    paths: list[Path] = []
    for value in result.preview_files:
        path = Path(value)
        if not path.is_absolute():
            raise UnrealPreviewError(f"Unreal returned a non-absolute preview path: {value}")
        resolved = path.resolve()
        try:
            resolved.relative_to(preview_root)
        except ValueError as exc:
            raise UnrealPreviewError(
                f"Unreal returned a preview outside the run directory: {resolved}"
            ) from exc
        if resolved.suffix.casefold() != ".png" or not resolved.is_file():
            raise UnrealPreviewError(f"Unreal preview is not an existing PNG: {resolved}")
        if resolved.stat().st_size == 0:
            raise UnrealPreviewError(f"Unreal preview is empty: {resolved}")
        paths.append(resolved)
    unique = sorted(set(paths))
    if len(unique) != 1:
        raise UnrealPreviewError(
            f"the one-frame preview must produce exactly one PNG, observed {len(unique)}"
        )
    return unique


def _scene_result(result: UnrealPreviewResult) -> UnrealSceneBuildResult:
    return UnrealSceneBuildResult.model_validate(
        result.model_dump(mode="json", exclude={"preview_files"})
    )


def run_unreal_preview(
    uproject_path: str | Path,
    *,
    engine_root: str | Path,
    render_spec_path: str | Path,
    asset_catalog_path: str | Path,
    run_directory: str | Path,
    run_id: str,
    timeout_seconds: int = 600,
    fail_on_warning: bool = False,
) -> tuple[RunManifest, UnrealPreviewResult]:
    """Build the requested scene, render one PNG, and finalize a RunManifest."""

    project = Path(uproject_path).expanduser().resolve()
    root = Path(engine_root).expanduser().resolve()
    spec_path = Path(render_spec_path).expanduser().resolve()
    catalog_path = Path(asset_catalog_path).expanduser().resolve()
    run_root = Path(run_directory).expanduser().resolve()
    if project.suffix.casefold() != ".uproject" or not project.is_file():
        raise UnrealPreviewError(f"not an existing .uproject file: {project}")
    if not 1 <= timeout_seconds <= 3600:
        raise UnrealPreviewError("timeout must be between 1 and 3600 seconds")
    try:
        TypeAdapter(Identifier).validate_python(run_id)
    except ValidationError as exc:
        raise UnrealPreviewError(f"invalid run ID: {exc}") from exc
    editor = root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
    if not editor.is_file():
        raise UnrealPreviewError(f"UnrealEditor-Cmd.exe not found under {root}")
    script = Path(__file__).parent / "unreal_scripts" / "preview_render.py"
    if not script.is_file():
        raise UnrealPreviewError(f"bundled Unreal preview script is missing: {script}")

    try:
        spec = RenderSpec.model_validate_json(spec_path.read_text(encoding="utf-8-sig"))
        cards = load_asset_card_catalog(catalog_path)
        request = resolve_scene_build_request(
            spec,
            cards,
            fail_on_warning=fail_on_warning,
        )
    except (OSError, ValidationError, AssetIndexError, UnrealSceneBuildError) as exc:
        raise UnrealPreviewError(f"cannot prepare Unreal preview inputs: {exc}") from exc

    if run_root.exists() and any(run_root.iterdir()):
        raise UnrealPreviewError(f"run directory is not empty: {run_root}")
    run_root.mkdir(parents=True, exist_ok=True)
    inputs = run_root / "inputs"
    preview = run_root / "preview"
    result_path = run_root / "unreal_result.json"
    manifest_path = run_root / "run_manifest.json"
    inputs.mkdir(parents=True, exist_ok=True)
    preview.mkdir(parents=True, exist_ok=True)

    spec_copy = inputs / "render_spec.json"
    cards_copy = inputs / "asset_cards.json"
    request_copy = inputs / "scene_request.json"
    _write_json(spec_copy, spec.model_dump(mode="json"))
    _write_json(cards_copy, [card.model_dump(mode="json") for card in cards])
    _write_json(request_copy, request.model_dump(mode="json"))
    input_artifacts = [
        _artifact("render_spec", spec_copy, run_root),
        _artifact("asset_catalog", cards_copy, run_root),
        _artifact("scene_request", request_copy, run_root),
    ]

    started_at = datetime.now(timezone.utc)
    try:
        running = RunManifest(
            run_id=run_id,
            status="running",
            started_at=started_at,
            render_spec_sha256=request.render_spec_sha256,
            input_artifacts=input_artifacts,
        )
    except ValidationError as exc:
        raise UnrealPreviewError(f"invalid run ID or manifest input: {exc}") from exc
    _write_manifest(manifest_path, running)

    environment = os.environ.copy()
    environment.update(
        {
            "RENDERMASTER_PREVIEW_REQUEST": str(request_copy),
            "RENDERMASTER_PREVIEW_RESULT": str(result_path),
            "RENDERMASTER_PREVIEW_DIRECTORY": str(preview),
        }
    )
    command = [
        str(editor),
        str(project),
        f"-ExecutePythonScript={script}",
        "-unattended",
        "-nop4",
        "-nosplash",
        "-RenderOffscreen",
        "-NoSound",
        "-stdout",
        "-FullStdOutLogOutput",
    ]
    started_clock = time.perf_counter()
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
        if not result_path.is_file():
            raise UnrealPreviewError(
                f"Unreal produced no preview result (exit {completed.returncode}): "
                f"{_diagnostic_output(completed)}"
            )
        result = _load_result(result_path)
        if completed.returncode != 0:
            raise UnrealPreviewError(
                f"Unreal preview exited with code {completed.returncode}: "
                f"{_diagnostic_output(completed)}"
            )
        if result.status != "succeeded":
            raise UnrealPreviewError("Unreal preview failed: " + "; ".join(result.errors))
        _validate_observed_result(request, _scene_result(result))
        preview_files = _validate_preview_files(result, run_root)
        duration = time.perf_counter() - started_clock
        output_artifacts = [_artifact("unreal_result", result_path, run_root)]
        output_artifacts.extend(
            _artifact("beauty_preview", path, run_root) for path in preview_files
        )
        manifest = RunManifest(
            run_id=run_id,
            status="succeeded",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            render_spec_sha256=request.render_spec_sha256,
            input_artifacts=input_artifacts,
            output_artifacts=output_artifacts,
            timings=[RunTiming(stage="unreal_preview", duration_seconds=duration)],
        )
        _write_manifest(manifest_path, manifest)
        return manifest, result
    except Exception as exc:
        duration = time.perf_counter() - started_clock
        message = str(exc)[:4000] or exc.__class__.__name__
        failed = RunManifest(
            run_id=run_id,
            status="failed",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            render_spec_sha256=request.render_spec_sha256,
            input_artifacts=input_artifacts,
            output_artifacts=(
                [_artifact("unreal_result", result_path, run_root)]
                if result_path.is_file()
                else []
            ),
            timings=[RunTiming(stage="unreal_preview", duration_seconds=duration)],
            errors=[message],
        )
        _write_manifest(manifest_path, failed)
        if isinstance(exc, UnrealPreviewError):
            raise
        if isinstance(exc, subprocess.TimeoutExpired):
            raise UnrealPreviewError(f"Unreal preview timed out after {timeout_seconds}s") from exc
        raise UnrealPreviewError(f"failed to run Unreal preview: {exc}") from exc
