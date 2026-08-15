"""Repeatable visual-evaluator benchmarks with deterministic PNG evidence."""

from __future__ import annotations

import hashlib
import math
import statistics
import struct
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path

from render_master_bot.contracts import (
    ImageStatistics,
    ModelIdentity,
    VisualBenchmarkCase,
    VisualBenchmarkCaseResult,
    VisualBenchmarkObservation,
    VisualBenchmarkReport,
    VisualBenchmarkSuite,
)
from render_master_bot.serialization import canonical_sha256
from render_master_bot.visual_evaluator import (
    StructuredVisionClient,
    evaluate_preview_run,
    load_preview_run_evidence,
)


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_SAMPLED_PIXELS = 150_000
MAX_DECODED_BYTES = 256 * 1024 * 1024


class VisualBenchmarkError(RuntimeError):
    """Raised when a suite or its deterministic evidence cannot be trusted."""


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


def _decode_png(image_bytes: bytes) -> tuple[int, int, int, list[bytes]]:
    """Decode the non-interlaced 8-bit RGB(A) PNGs emitted by Unreal MRQ."""

    if not image_bytes.startswith(PNG_SIGNATURE):
        raise VisualBenchmarkError("benchmark preview is not a PNG file")
    offset = len(PNG_SIGNATURE)
    header: tuple[int, int, int, int, int, int, int] | None = None
    compressed_parts: list[bytes] = []
    saw_end = False
    while offset + 12 <= len(image_bytes):
        length = struct.unpack(">I", image_bytes[offset : offset + 4])[0]
        chunk_type = image_bytes[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(image_bytes):
            raise VisualBenchmarkError("PNG chunk extends beyond the preview file")
        chunk_data = image_bytes[data_start:data_end]
        expected_crc = struct.unpack(">I", image_bytes[data_end:crc_end])[0]
        observed_crc = zlib.crc32(chunk_type)
        observed_crc = zlib.crc32(chunk_data, observed_crc) & 0xFFFFFFFF
        if observed_crc != expected_crc:
            raise VisualBenchmarkError(
                f"PNG chunk CRC mismatch for {chunk_type.decode('ascii', errors='replace')}"
            )
        if chunk_type == b"IHDR":
            if header is not None or length != 13:
                raise VisualBenchmarkError("PNG must contain one valid IHDR chunk")
            header = struct.unpack(">IIBBBBB", chunk_data)
        elif chunk_type == b"IDAT":
            compressed_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            saw_end = True
            break
        offset = crc_end

    if header is None or not compressed_parts or not saw_end:
        raise VisualBenchmarkError("PNG is missing IHDR, IDAT, or IEND data")
    width, height, bit_depth, color_type, compression, filter_method, interlace = header
    if width <= 0 or height <= 0 or width > 16384 or height > 16384:
        raise VisualBenchmarkError("PNG dimensions are outside the supported range")
    if bit_depth != 8 or color_type not in {2, 6}:
        raise VisualBenchmarkError("benchmark PNG must use 8-bit RGB or RGBA pixels")
    if compression != 0 or filter_method != 0 or interlace != 0:
        raise VisualBenchmarkError("benchmark PNG uses an unsupported encoding mode")

    channels = 3 if color_type == 2 else 4
    row_bytes = width * channels
    expected_bytes = height * (row_bytes + 1)
    if expected_bytes > MAX_DECODED_BYTES:
        raise VisualBenchmarkError(
            f"PNG expands to {expected_bytes} bytes; limit is {MAX_DECODED_BYTES}"
        )
    try:
        filtered = zlib.decompress(b"".join(compressed_parts))
    except zlib.error as exc:
        raise VisualBenchmarkError(f"cannot decompress benchmark PNG: {exc}") from exc
    if len(filtered) != expected_bytes:
        raise VisualBenchmarkError(
            f"PNG pixel payload has {len(filtered)} bytes; expected {expected_bytes}"
        )

    rows: list[bytes] = []
    previous = bytearray(row_bytes)
    source_offset = 0
    for _ in range(height):
        filter_type = filtered[source_offset]
        source_offset += 1
        row = bytearray(filtered[source_offset : source_offset + row_bytes])
        source_offset += row_bytes
        if filter_type > 4:
            raise VisualBenchmarkError(f"PNG uses unknown row filter {filter_type}")
        for index in range(row_bytes):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                row[index] = (row[index] + left) & 0xFF
            elif filter_type == 2:
                row[index] = (row[index] + above) & 0xFF
            elif filter_type == 3:
                row[index] = (row[index] + ((left + above) // 2)) & 0xFF
            elif filter_type == 4:
                row[index] = (
                    row[index] + _paeth_predictor(left, above, upper_left)
                ) & 0xFF
        rows.append(bytes(row))
        previous = row
    return width, height, channels, rows


def _percentile(sorted_values: list[float], fraction: float) -> float:
    index = round((len(sorted_values) - 1) * fraction)
    return sorted_values[index]


def analyze_preview_png(image_bytes: bytes) -> ImageStatistics:
    """Extract bounded, reproducible luminance and foreground statistics."""

    width, height, channels, rows = _decode_png(image_bytes)
    sample_step = max(1, math.ceil(math.sqrt((width * height) / MAX_SAMPLED_PIXELS)))
    luminances: list[float] = []
    center_luminances: list[float] = []
    border_luminances: list[float] = []
    colors: list[tuple[float, float, float]] = []
    border_colors: list[tuple[float, float, float]] = []
    border_x = max(1, round(width * 0.05))
    border_y = max(1, round(height * 0.05))
    center_left = width * 0.25
    center_right = width * 0.75
    center_top = height * 0.25
    center_bottom = height * 0.75

    for y in range(0, height, sample_step):
        row = rows[y]
        for x in range(0, width, sample_step):
            start = x * channels
            red, green, blue = (row[start + channel] / 255.0 for channel in range(3))
            if channels == 4:
                alpha = row[start + 3] / 255.0
                red *= alpha
                green *= alpha
                blue *= alpha
            luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
            color = (red, green, blue)
            luminances.append(luminance)
            colors.append(color)
            is_border = (
                x < border_x
                or x >= width - border_x
                or y < border_y
                or y >= height - border_y
            )
            if is_border:
                border_luminances.append(luminance)
                border_colors.append(color)
            if center_left <= x < center_right and center_top <= y < center_bottom:
                center_luminances.append(luminance)

    if not luminances or not border_luminances or not center_luminances:
        raise VisualBenchmarkError("PNG sampling produced insufficient center or border evidence")
    mean_luminance = statistics.fmean(luminances)
    variance = statistics.fmean((value - mean_luminance) ** 2 for value in luminances)
    sorted_luminances = sorted(luminances)
    background = tuple(
        statistics.median(color[channel] for color in border_colors)
        for channel in range(3)
    )
    foreground_pixels = sum(
        math.sqrt(sum((color[channel] - background[channel]) ** 2 for channel in range(3)) / 3)
        >= 0.08
        for color in colors
    )
    dark_fraction = sum(value <= 0.02 for value in luminances) / len(luminances)
    clipped_fraction = sum(value >= 0.98 for value in luminances) / len(luminances)
    foreground_fraction = foreground_pixels / len(colors)
    stddev = math.sqrt(variance)
    p05 = _percentile(sorted_luminances, 0.05)
    p95 = _percentile(sorted_luminances, 0.95)

    return ImageStatistics(
        sha256=hashlib.sha256(image_bytes).hexdigest(),
        width_px=width,
        height_px=height,
        sampled_pixels=len(luminances),
        mean_luminance=round(mean_luminance, 8),
        luminance_stddev=round(stddev, 8),
        p05_luminance=round(p05, 8),
        p95_luminance=round(p95, 8),
        dark_pixel_fraction=round(dark_fraction, 8),
        clipped_pixel_fraction=round(clipped_fraction, 8),
        foreground_fraction=round(foreground_fraction, 8),
        center_luminance=round(statistics.fmean(center_luminances), 8),
        border_luminance=round(statistics.fmean(border_luminances), 8),
        blank_like=stddev <= 0.01 and foreground_fraction <= 0.01,
        underexposed_like=p95 <= 0.05,
        overexposed_like=p05 >= 0.95,
    )


def _image_expectation_failures(
    case: VisualBenchmarkCase,
    statistics: ImageStatistics,
) -> list[str]:
    failures: list[str] = []
    for expectation in case.expectation.image_metrics:
        value = getattr(statistics, expectation.metric)
        if expectation.minimum is not None and value < expectation.minimum:
            failures.append(
                f"{expectation.metric}={value:g} is below minimum {expectation.minimum:g}"
            )
        if expectation.maximum is not None and value > expectation.maximum:
            failures.append(
                f"{expectation.metric}={value:g} exceeds maximum {expectation.maximum:g}"
            )
    return failures


def _observation_matches(
    case: VisualBenchmarkCase,
    report,
) -> tuple[bool, bool, bool]:
    categories = {issue.category for issue in report.issues}
    return (
        report.verdict in case.expectation.accepted_verdicts,
        set(case.expectation.required_issue_categories) <= categories,
        not bool(set(case.expectation.forbidden_issue_categories) & categories),
    )


def _case_contradictions(
    case: VisualBenchmarkCase,
    statistics: ImageStatistics,
    image_failures: list[str],
    observations: list[VisualBenchmarkObservation],
) -> list[str]:
    contradictions: list[str] = []
    valid_reports = [item.report for item in observations if item.report is not None]
    verdicts = {report.verdict for report in valid_reports}
    if len(verdicts) > 1:
        contradictions.append(
            "repeated evaluations disagree on verdict: " + ", ".join(sorted(verdicts))
        )
    if any(report.verdict == "pass" for report in valid_reports):
        if statistics.blank_like:
            contradictions.append("the model passed a frame classified as blank-like by pixels")
        if statistics.underexposed_like:
            contradictions.append(
                "the model passed a frame classified as underexposed-like by pixels"
            )
        if statistics.overexposed_like:
            contradictions.append(
                "the model passed a frame classified as overexposed-like by pixels"
            )
    if set(case.expectation.accepted_verdicts) == {"pass"} and not image_failures:
        if any(report.verdict == "fail" for report in valid_reports):
            contradictions.append(
                "the model failed a human-approved case whose deterministic image ranges passed"
            )
    if "pass" not in case.expectation.accepted_verdicts:
        if any(report.verdict == "pass" for report in valid_reports):
            contradictions.append("the model passed a human-rejected benchmark case")
    return contradictions


def run_visual_benchmark(
    client: StructuredVisionClient,
    *,
    model: str,
    suite: VisualBenchmarkSuite,
    suite_root: str | Path,
) -> VisualBenchmarkReport:
    """Evaluate every verified run in a suite and compare it with frozen labels."""

    root = Path(suite_root).expanduser().resolve()
    case_results: list[VisualBenchmarkCaseResult] = []
    total_duration = 0.0
    for case in suite.cases:
        run_directory = (root / case.run_directory).resolve()
        try:
            run_directory.relative_to(root)
        except ValueError as exc:
            raise VisualBenchmarkError(
                f"benchmark case {case.case_id} escapes the suite root"
            ) from exc
        try:
            evidence = load_preview_run_evidence(run_directory)
            statistics = analyze_preview_png(evidence.image_bytes)
        except (OSError, RuntimeError) as exc:
            raise VisualBenchmarkError(
                f"cannot load benchmark case {case.case_id}: {exc}"
            ) from exc

        image_failures = _image_expectation_failures(case, statistics)
        if statistics.width_px != evidence.spec.render.width_px:
            image_failures.append(
                f"width_px={statistics.width_px} does not match RenderSpec "
                f"width_px={evidence.spec.render.width_px}"
            )
        if statistics.height_px != evidence.spec.render.height_px:
            image_failures.append(
                f"height_px={statistics.height_px} does not match RenderSpec "
                f"height_px={evidence.spec.render.height_px}"
            )

        observations: list[VisualBenchmarkObservation] = []
        for repetition in range(1, suite.repetitions + 1):
            started = time.perf_counter()
            try:
                result = evaluate_preview_run(
                    client,
                    model=model,
                    run_directory=run_directory,
                )
                duration = time.perf_counter() - started
                matches = _observation_matches(case, result.report)
                observation = VisualBenchmarkObservation(
                    repetition=repetition,
                    status="valid",
                    duration_seconds=duration,
                    report=result.report,
                    verdict_matched=matches[0],
                    required_categories_matched=matches[1],
                    forbidden_categories_absent=matches[2],
                )
            except (OSError, RuntimeError) as exc:
                duration = time.perf_counter() - started
                observation = VisualBenchmarkObservation(
                    repetition=repetition,
                    status="invalid",
                    duration_seconds=duration,
                    error=str(exc)[:4000] or type(exc).__name__,
                )
            total_duration += duration
            observations.append(observation)

        valid_verdicts = [
            item.report.verdict
            for item in observations
            if item.report is not None
        ]
        verdict_stable = (
            len(valid_verdicts) == suite.repetitions and len(set(valid_verdicts)) == 1
        )
        contradictions = _case_contradictions(
            case,
            statistics,
            image_failures,
            observations,
        )
        observations_passed = all(
            item.status == "valid"
            and item.verdict_matched
            and item.required_categories_matched
            and item.forbidden_categories_absent
            for item in observations
        )
        case_results.append(
            VisualBenchmarkCaseResult(
                case_id=case.case_id,
                run_directory=case.run_directory,
                image_statistics=statistics,
                image_expectation_failures=image_failures,
                observations=observations,
                verdict_stable=verdict_stable,
                contradictions=contradictions,
                passed=not image_failures and verdict_stable and observations_passed,
            )
        )

    case_count = len(case_results)
    passed_case_count = sum(case.passed for case in case_results)
    observation_count = sum(len(case.observations) for case in case_results)
    valid_observation_count = sum(
        observation.status == "valid"
        for case in case_results
        for observation in case.observations
    )
    stable_case_count = sum(case.verdict_stable for case in case_results)
    contradiction_count = sum(len(case.contradictions) for case in case_results)
    return VisualBenchmarkReport(
        suite_id=suite.suite_id,
        suite_sha256=canonical_sha256(suite),
        evaluator=ModelIdentity(provider="ollama", model=model),
        completed_at=datetime.now(UTC),
        case_count=case_count,
        passed_case_count=passed_case_count,
        observation_count=observation_count,
        valid_observation_count=valid_observation_count,
        case_accuracy=passed_case_count / case_count,
        verdict_stability=stable_case_count / case_count,
        contradiction_count=contradiction_count,
        total_duration_seconds=total_duration,
        cases=case_results,
        passed=passed_case_count == case_count,
    )
