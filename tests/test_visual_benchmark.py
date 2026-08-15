import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from datetime import UTC, datetime
from pathlib import Path

from render_master_bot.contracts import RunManifest, VisualBenchmarkSuite
from render_master_bot.models import RenderSpec
from render_master_bot.ollama import StructuredResponse
from render_master_bot.serialization import canonical_sha256
from render_master_bot.visual_benchmark import analyze_preview_png, run_visual_benchmark


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def make_rgba_png(
    width: int,
    height: int,
    pixel_at,
) -> bytes:
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(pixel_at(x, y))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows)))
        + _png_chunk(b"IEND", b"")
    )


class FakeVisionClient:
    def __init__(self, contents: list[str], model: str = "qwen3-vl:8b-instruct"):
        self.contents = iter(contents)
        self.model = model

    def chat_structured(self, **_kwargs):
        return StructuredResponse(content=next(self.contents), model=self.model)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_run(root: Path, image_bytes: bytes) -> None:
    run_root = root / "runs" / "approved"
    inputs = run_root / "inputs"
    preview = run_root / "preview"
    inputs.mkdir(parents=True)
    preview.mkdir()
    spec = RenderSpec.model_validate({
        "source_prompt": "Show the centered product against a black background.",
        "scene_name": "benchmark_scene",
        "objects": [{
            "object_id": "product",
            "asset": {"asset_id": "product_asset"},
        }],
        "camera": {"camera_id": "camera", "transform": {}},
        "render": {"width_px": 64, "height_px": 64},
    })
    spec_path = inputs / "render_spec.json"
    preview_path = preview / "beauty.png"
    spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    preview_path.write_bytes(image_bytes)
    now = datetime.now(UTC)
    manifest = RunManifest.model_validate({
        "run_id": "benchmark_run",
        "status": "succeeded",
        "started_at": now,
        "finished_at": now,
        "render_spec_sha256": canonical_sha256(spec),
        "input_artifacts": [{
            "role": "render_spec",
            "path": "inputs/render_spec.json",
            "sha256": _sha256(spec_path),
        }],
        "output_artifacts": [{
            "role": "beauty_preview",
            "path": "preview/beauty.png",
            "sha256": _sha256(preview_path),
        }],
    })
    (run_root / "run_manifest.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )


def suite(repetitions: int = 2) -> VisualBenchmarkSuite:
    return VisualBenchmarkSuite.model_validate({
        "suite_id": "product_preview",
        "description": "Ground-truth checks for one centered product preview.",
        "repetitions": repetitions,
        "cases": [{
            "case_id": "approved_product",
            "description": "A visible centered product that should pass.",
            "run_directory": "runs/approved",
            "expectation": {
                "accepted_verdicts": ["pass"],
                "forbidden_issue_categories": ["geometry"],
                "image_metrics": [{
                    "metric": "foreground_fraction",
                    "minimum": 0.1,
                }],
            },
        }],
    })


def evaluation(verdict: str) -> str:
    if verdict == "pass":
        return json.dumps({
            "verdict": "pass",
            "summary": "The centered product is visible and correctly exposed.",
            "issues": [],
        })
    return json.dumps({
        "verdict": "fail",
        "summary": "The model incorrectly claims that no product is visible.",
        "issues": [{
            "issue_id": "missing_product",
            "category": "geometry",
            "severity": "blocking",
            "confidence": 1.0,
            "message": "No product is visible in the frame.",
            "object_ids": ["product"],
        }],
    })


class PngStatisticsTests(unittest.TestCase):
    def test_oversized_decoded_payload_is_rejected_before_decompression(self):
        header = struct.pack(">IIBBBBB", 16384, 16384, 8, 6, 0, 0, 0)
        image = (
            b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", header)
            + _png_chunk(b"IDAT", zlib.compress(b""))
            + _png_chunk(b"IEND", b"")
        )

        with self.assertRaisesRegex(RuntimeError, "expands to"):
            analyze_preview_png(image)

    def test_uniform_frame_is_classified_as_blank_like(self):
        image = make_rgba_png(64, 64, lambda _x, _y: (128, 64, 16, 255))

        result = analyze_preview_png(image)

        self.assertTrue(result.blank_like)
        self.assertEqual(result.foreground_fraction, 0.0)
        self.assertEqual(result.width_px, 64)
        self.assertEqual(result.height_px, 64)

    def test_centered_subject_is_separated_from_border_background(self):
        image = make_rgba_png(
            64,
            64,
            lambda x, y: (220, 180, 120, 255)
            if 16 <= x < 48 and 16 <= y < 48
            else (0, 0, 0, 255),
        )

        result = analyze_preview_png(image)

        self.assertFalse(result.blank_like)
        self.assertGreater(result.foreground_fraction, 0.2)
        self.assertGreater(result.center_luminance, result.border_luminance)


class VisualBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.image = make_rgba_png(
            64,
            64,
            lambda x, y: (220, 180, 120, 255)
            if 16 <= x < 48 and 16 <= y < 48
            else (0, 0, 0, 255),
        )

    def test_repeated_matching_evaluations_produce_a_passing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root, self.image)

            report = run_visual_benchmark(
                FakeVisionClient([evaluation("pass"), evaluation("pass")]),
                model="qwen3-vl:8b-instruct",
                suite=suite(),
                suite_root=root,
            )

        self.assertTrue(report.passed)
        self.assertEqual(report.case_accuracy, 1.0)
        self.assertEqual(report.verdict_stability, 1.0)
        self.assertEqual(report.contradiction_count, 0)
        self.assertEqual(report.observation_count, 2)

    def test_false_failure_is_recorded_as_a_pixel_supported_contradiction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root, self.image)

            report = run_visual_benchmark(
                FakeVisionClient([evaluation("fail")]),
                model="qwen3-vl:8b-instruct",
                suite=suite(repetitions=1),
                suite_root=root,
            )

        self.assertFalse(report.passed)
        self.assertEqual(report.case_accuracy, 0.0)
        self.assertEqual(report.contradiction_count, 1)
        self.assertIn("human-approved", report.cases[0].contradictions[0])


if __name__ == "__main__":
    unittest.main()
