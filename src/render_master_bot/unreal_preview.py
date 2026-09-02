"""Host-side orchestration for one-frame Unreal preview rendering."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
import time
import zlib
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


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _png_luminance_summary(path: Path) -> tuple[int, int, float, float]:
    """Read an 8-bit non-interlaced PNG without adding an image-library dependency."""

    payload = path.read_bytes()
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise UnrealPreviewError(f"Unreal preview is not a valid PNG: {path}")
    cursor = 8
    width = height = bit_depth = color_type = interlace = None
    compressed = bytearray()
    palette: list[tuple[int, int, int]] = []
    while cursor + 12 <= len(payload):
        length = struct.unpack(">I", payload[cursor : cursor + 4])[0]
        chunk_type = payload[cursor + 4 : cursor + 8]
        data_start = cursor + 8
        data_end = data_start + length
        if data_end + 4 > len(payload):
            raise UnrealPreviewError(f"Unreal preview PNG is truncated: {path}")
        data = payload[data_start:data_end]
        if chunk_type == b"IHDR":
            if len(data) != 13:
                raise UnrealPreviewError(f"Unreal preview PNG has an invalid IHDR: {path}")
            width, height, bit_depth, color_type, compression, filter_method, interlace = (
                struct.unpack(">IIBBBBB", data)
            )
            if compression != 0 or filter_method != 0:
                raise UnrealPreviewError(f"Unreal preview PNG uses unsupported encoding: {path}")
        elif chunk_type == b"PLTE":
            if len(data) % 3:
                raise UnrealPreviewError(f"Unreal preview PNG has an invalid palette: {path}")
            palette = [tuple(data[index : index + 3]) for index in range(0, len(data), 3)]
        elif chunk_type == b"IDAT":
            compressed.extend(data)
        elif chunk_type == b"IEND":
            break
        cursor = data_end + 4
    if not width or not height or bit_depth != 8 or interlace != 0:
        raise UnrealPreviewError(
            f"Unreal preview PNG must be 8-bit and non-interlaced: {path}"
        )
    channel_counts = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    channels = channel_counts.get(color_type)
    if channels is None or (color_type == 3 and not palette):
        raise UnrealPreviewError(f"Unreal preview PNG color type is unsupported: {path}")
    try:
        decoded = zlib.decompress(bytes(compressed))
    except zlib.error as exc:
        raise UnrealPreviewError(f"Unreal preview PNG pixel data is invalid: {path}") from exc
    row_size = width * channels
    expected_size = height * (row_size + 1)
    if len(decoded) != expected_size:
        raise UnrealPreviewError(f"Unreal preview PNG scanline size is invalid: {path}")

    previous = bytearray(row_size)
    minimum = 255
    maximum = 0
    total = 0
    visible = 0
    pixel_count = width * height
    offset = 0
    for _ in range(height):
        filter_type = decoded[offset]
        offset += 1
        raw = decoded[offset : offset + row_size]
        offset += row_size
        row = bytearray(row_size)
        for index, value in enumerate(raw):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth_predictor(left, above, upper_left)
            else:
                raise UnrealPreviewError(
                    f"Unreal preview PNG uses an invalid scanline filter: {path}"
                )
            row[index] = (value + predictor) & 0xFF
        for index in range(0, row_size, channels):
            if color_type == 0:
                red = green = blue = row[index]
                alpha = 255
            elif color_type == 2:
                red, green, blue = row[index : index + 3]
                alpha = 255
            elif color_type == 3:
                palette_index = row[index]
                if palette_index >= len(palette):
                    raise UnrealPreviewError(f"Unreal preview PNG palette index is invalid: {path}")
                red, green, blue = palette[palette_index]
                alpha = 255
            elif color_type == 4:
                red = green = blue = row[index]
                alpha = row[index + 1]
            else:
                red, green, blue, alpha = row[index : index + 4]
            luminance = ((54 * red + 183 * green + 19 * blue) >> 8) * alpha // 255
            minimum = min(minimum, luminance)
            maximum = max(maximum, luminance)
            total += luminance
            visible += luminance > 2
        previous = row
    return minimum, maximum, total / pixel_count, visible / pixel_count


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
    preview_directory: Path,
) -> list[Path]:
    preview_root = preview_directory.resolve()
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
        minimum, maximum, mean, visible_fraction = _png_luminance_summary(resolved)
        if maximum - minimum < 2 or visible_fraction < 0.001:
            raise UnrealPreviewError(
                "Unreal preview contains no meaningful visible pixels: "
                f"min={minimum} max={maximum} mean={mean:.3f} "
                f"visible_fraction={visible_fraction:.6f} path={resolved}"
            )
        paths.append(resolved)
    unique = sorted(set(paths))
    if len(unique) != 1:
        raise UnrealPreviewError(
            f"the one-frame preview must produce exactly one PNG, observed {len(unique)}"
        )
    return unique


def _write_process_log(
    path: Path,
    command: list[str],
    completed: subprocess.CompletedProcess[str],
) -> None:
    value = (
        f"exit_code={completed.returncode}\n"
        f"command={subprocess.list2cmdline(command)}\n\n"
        "STDOUT\n"
        f"{completed.stdout or ''}\n"
        "STDERR\n"
        f"{completed.stderr or ''}\n"
    )
    path.write_text(value, encoding="utf-8", errors="replace")


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
    process_log_path = run_root / "unreal_process.log"
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

    command = [
        str(editor),
        str(project),
        "-DisablePlugins=RenderMasterBot",
        "-DDC-ForceMemoryCache",
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
        with tempfile.TemporaryDirectory(prefix=f"render-master-{run_id}-") as scratch_value:
            scratch_root = Path(scratch_value).resolve()
            scratch_preview = scratch_root / "preview"
            scratch_result = scratch_root / "unreal_result.json"
            scratch_shader_working = scratch_root / "shader-working"
            scratch_preview.mkdir()
            scratch_shader_working.mkdir()
            execution_command = [
                *command,
                f"-ShaderWorkingDir={scratch_shader_working}",
            ]
            environment = os.environ.copy()
            environment.update(
                {
                    "RENDERMASTER_PREVIEW_REQUEST": str(request_copy),
                    "RENDERMASTER_PREVIEW_RESULT": str(scratch_result),
                    "RENDERMASTER_PREVIEW_DIRECTORY": str(scratch_preview),
                }
            )
            completed = subprocess.run(
                execution_command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=timeout_seconds,
                check=False,
            )
            _write_process_log(process_log_path, execution_command, completed)
            if not scratch_result.is_file():
                raise UnrealPreviewError(
                    f"Unreal produced no preview result (exit {completed.returncode}): "
                    f"{_diagnostic_output(completed)}"
                )
            shutil.copy2(scratch_result, result_path)
            result = _load_result(scratch_result)
            if completed.returncode != 0:
                raise UnrealPreviewError(
                    f"Unreal preview exited with code {completed.returncode}: "
                    f"{_diagnostic_output(completed)}"
                )
            if result.status != "succeeded":
                raise UnrealPreviewError(
                    "Unreal preview failed: " + "; ".join(result.errors)
                )
            _validate_observed_result(request, _scene_result(result))
            scratch_files = _validate_preview_files(result, scratch_preview)
            final_preview = preview / "beauty.png"
            shutil.copy2(scratch_files[0], final_preview)
            result = result.model_copy(
                update={"preview_files": [str(final_preview.resolve())]}
            )
            _write_json(result_path, result.model_dump(mode="json"))

        duration = time.perf_counter() - started_clock
        output_artifacts = [
            _artifact("unreal_result", result_path, run_root),
            _artifact("beauty_preview", final_preview, run_root),
            _artifact("unreal_process_log", process_log_path, run_root),
        ]
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
        failed_artifacts = []
        if result_path.is_file():
            failed_artifacts.append(_artifact("unreal_result", result_path, run_root))
        if process_log_path.is_file():
            failed_artifacts.append(
                _artifact("unreal_process_log", process_log_path, run_root)
            )
        failed = RunManifest(
            run_id=run_id,
            status="failed",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            render_spec_sha256=request.render_spec_sha256,
            input_artifacts=input_artifacts,
            output_artifacts=failed_artifacts,
            timings=[RunTiming(stage="unreal_preview", duration_seconds=duration)],
            errors=[message],
        )
        _write_manifest(manifest_path, failed)
        if isinstance(exc, UnrealPreviewError):
            raise
        if isinstance(exc, subprocess.TimeoutExpired):
            raise UnrealPreviewError(f"Unreal preview timed out after {timeout_seconds}s") from exc
        raise UnrealPreviewError(f"failed to run Unreal preview: {exc}") from exc
