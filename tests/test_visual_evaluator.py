import base64
import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from datetime import UTC, datetime
from pathlib import Path

from render_master_bot.contracts import RunManifest
from render_master_bot.models import RenderSpec
from render_master_bot.ollama import StructuredResponse
from render_master_bot.serialization import canonical_sha256
from render_master_bot.visual_evaluator import (
    SYSTEM_PROMPT,
    VisualEvaluationError,
    evaluate_preview_run,
)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _test_png() -> bytes:
    width = height = 8
    rows = bytearray()
    for _y in range(height):
        rows.append(0)
        rows.extend((96, 96, 96, 255) * width)
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows)))
        + _png_chunk(b"IEND", b"")
    )


PNG_BYTES = _test_png()


class FakeVisionClient:
    def __init__(self, content: str, model: str = "qwen3.5:9b"):
        self.content = content
        self.model = model
        self.last_request = None

    def chat_structured(self, **kwargs):
        self.last_request = kwargs
        return StructuredResponse(content=self.content, model=self.model)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_run(root: Path) -> tuple[Path, RenderSpec]:
    run_root = root / "run"
    inputs = run_root / "inputs"
    preview = run_root / "preview"
    inputs.mkdir(parents=True)
    preview.mkdir()
    spec = RenderSpec.model_validate({
        "source_prompt": "Render one visible door.",
        "scene_name": "door_scene",
        "objects": [{
            "object_id": "door",
            "asset": {"asset_id": "door_asset"},
        }],
        "camera": {"camera_id": "camera", "transform": {}},
    })
    spec_path = inputs / "render_spec.json"
    spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    preview_path = preview / "beauty.png"
    preview_path.write_bytes(PNG_BYTES)
    now = datetime.now(UTC)
    manifest = RunManifest.model_validate({
        "run_id": "vision_test",
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
    return run_root, spec


def valid_draft(**overrides) -> str:
    value = {
        "verdict": "needs_review",
        "summary": "The requested subject is visible but lacks useful detail.",
        "issues": [{
            "issue_id": "flat_subject_detail",
            "category": "material",
            "severity": "warning",
            "confidence": 0.9,
            "message": "The visible subject appears uniformly gray.",
            "object_ids": ["door"],
        }],
    }
    value.update(overrides)
    return json.dumps(value)


class VisualEvaluatorTests(unittest.TestCase):
    def test_system_prompt_requires_all_visible_constraints_before_pass(self):
        self.assertIn("every non-excluded visible requirement", SYSTEM_PROMPT)
        self.assertIn("wrong requested view", SYSTEM_PROMPT)
        self.assertIn("rather than assuming", SYSTEM_PROMPT)
        self.assertIn("Distinguish clipping or exposure problems", SYSTEM_PROMPT)
        self.assertIn("underexposed_like", SYSTEM_PROMPT)
        self.assertIn("tall product in a landscape frame", SYSTEM_PROMPT)

    def test_host_verifies_evidence_and_owns_report_identity_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            run_root, spec = prepare_run(Path(directory))
            client = FakeVisionClient(valid_draft())

            result = evaluate_preview_run(
                client,
                model="qwen3.5:9b",
                run_directory=run_root,
            )

        self.assertEqual(result.report.render_spec_sha256, canonical_sha256(spec))
        self.assertEqual(result.report.evaluator.model, "qwen3.5:9b")
        self.assertEqual(result.report.preview_paths, ["preview/beauty.png"])
        self.assertEqual(
            result.report.issues[0].evidence_paths,
            ["preview/beauty.png"],
        )
        encoded = client.last_request["messages"][1]["images"][0]
        self.assertEqual(base64.b64decode(encoded), PNG_BYTES)
        self.assertEqual(client.last_request["json_schema"]["title"], "VisualEvaluationDraft")
        self.assertIs(client.last_request["think"], False)
        self.assertIsNotNone(result.statistics)
        self.assertEqual(result.statistics.sha256, hashlib.sha256(PNG_BYTES).hexdigest())
        self.assertIn(
            '"center_luminance"',
            client.last_request["messages"][1]["content"],
        )

    def test_unknown_scene_object_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            run_root, _ = prepare_run(Path(directory))
            content = valid_draft(issues=[{
                "issue_id": "invented_object",
                "category": "asset",
                "severity": "warning",
                "confidence": 0.8,
                "message": "An invented object was referenced.",
                "object_ids": ["not_in_scene"],
            }])

            with self.assertRaisesRegex(VisualEvaluationError, "unknown scene object"):
                evaluate_preview_run(
                    FakeVisionClient(content),
                    model="qwen3.5:9b",
                    run_directory=run_root,
                )

    def test_tampered_preview_is_rejected_before_model_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            run_root, _ = prepare_run(Path(directory))
            (run_root / "preview" / "beauty.png").write_bytes(PNG_BYTES + b"tampered")
            client = FakeVisionClient(valid_draft())

            with self.assertRaisesRegex(VisualEvaluationError, "SHA-256 mismatch"):
                evaluate_preview_run(
                    client,
                    model="qwen3.5:9b",
                    run_directory=run_root,
                )
            self.assertIsNone(client.last_request)

    def test_invalid_model_json_preserves_response_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            run_root, _ = prepare_run(Path(directory))
            with self.assertRaises(VisualEvaluationError) as raised:
                evaluate_preview_run(
                    FakeVisionClient("{}"),
                    model="qwen3.5:9b",
                    run_directory=run_root,
                )

        self.assertIsNotNone(raised.exception.response)

    def test_invalid_ollama_model_identity_is_wrapped(self):
        with tempfile.TemporaryDirectory() as directory:
            run_root, _ = prepare_run(Path(directory))
            with self.assertRaisesRegex(VisualEvaluationError, "trusted EvaluationReport"):
                evaluate_preview_run(
                    FakeVisionClient(valid_draft(), model=""),
                    model="qwen3.5:9b",
                    run_directory=run_root,
                )


if __name__ == "__main__":
    unittest.main()
