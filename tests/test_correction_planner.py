import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from render_master_bot.contracts import EvaluationReport, RunManifest
from render_master_bot.correction_planner import CorrectionPlanningError, plan_correction
from render_master_bot.models import RenderSpec
from render_master_bot.ollama import StructuredResponse
from render_master_bot.serialization import canonical_sha256


PNG_BYTES = b"\x89PNG\r\n\x1a\ncorrection-evidence"


class FakeCorrectionClient:
    def __init__(self, content: str):
        self.content = content
        self.last_request = None

    def chat_structured(self, **kwargs):
        self.last_request = kwargs
        return StructuredResponse(content=self.content, model="gpt-oss:20b")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_run(root: Path, *, report_category="material") -> tuple[Path, RenderSpec]:
    run_root = root / "run"
    inputs = run_root / "inputs"
    preview = run_root / "preview"
    inputs.mkdir(parents=True)
    preview.mkdir()
    spec = RenderSpec.model_validate({
        "source_prompt": "Render one wooden door.",
        "scene_name": "door_scene",
        "objects": [{
            "object_id": "door",
            "asset": {"asset_id": "door_asset"},
        }],
        "camera": {
            "camera_id": "camera",
            "transform": {"location_cm": {"x": -500}},
        },
        "lights": [{
            "light_id": "key",
            "kind": "directional",
            "intensity": 10,
            "intensity_unit": "lux",
        }],
    })
    spec_path = inputs / "render_spec.json"
    spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    assets_path = inputs / "asset_cards.json"
    assets_path.write_text(json.dumps([{
        "asset_id": "door_asset",
        "engine_path": "/Game/SM_Door",
        "display_name": "SM_Door",
        "asset_type": "static_mesh",
        "material_slots": ["Material_0"],
    }]), encoding="utf-8")
    preview_path = preview / "beauty.png"
    preview_path.write_bytes(PNG_BYTES)
    now = datetime.now(UTC)
    manifest = RunManifest.model_validate({
        "run_id": "correction_test",
        "status": "succeeded",
        "started_at": now,
        "finished_at": now,
        "render_spec_sha256": canonical_sha256(spec),
        "input_artifacts": [
            {
                "role": "render_spec",
                "path": "inputs/render_spec.json",
                "sha256": _sha256(spec_path),
            },
            {
                "role": "asset_catalog",
                "path": "inputs/asset_cards.json",
                "sha256": _sha256(assets_path),
            },
        ],
        "output_artifacts": [{
            "role": "beauty_preview",
            "path": "preview/beauty.png",
            "sha256": _sha256(preview_path),
        }],
    })
    (run_root / "run_manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    severe = report_category == "material"
    report = EvaluationReport.model_validate({
        "render_spec_sha256": canonical_sha256(spec),
        "evaluator": {"provider": "ollama", "model": "vision"},
        "evaluation_stage": "preview",
        "verdict": "fail" if severe else "needs_review",
        "summary": "The preview needs correction.",
        "preview_paths": ["preview/beauty.png"],
        "issues": [{
            "issue_id": "preview_issue",
            "category": report_category,
            "severity": "blocking" if severe else "warning",
            "confidence": 0.9,
            "message": "The material is missing." if severe else "The image is too dark.",
            "object_ids": ["door"],
            "evidence_paths": ["preview/beauty.png"],
        }],
    })
    (run_root / "evaluation.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    return run_root, spec


class CorrectionPlannerTests(unittest.TestCase):
    def test_material_gap_can_be_reported_as_unresolved(self):
        content = json.dumps({
            "outcome": "unresolved",
            "rationale": "The current contract cannot assign a wood material.",
            "operations": [],
            "missing_capabilities": ["material override support", "wood material asset"],
        })
        with tempfile.TemporaryDirectory() as directory:
            run_root, _ = prepare_run(Path(directory))
            result = plan_correction(
                FakeCorrectionClient(content),
                model="gpt-oss:20b",
                run_directory=run_root,
            )

        self.assertEqual(result.decision.outcome, "unresolved")
        self.assertIsNone(result.corrected_spec)
        self.assertIn("material override support", result.decision.missing_capabilities)

    def test_valid_lighting_replacement_is_applied_and_revalidated(self):
        content = json.dumps({
            "outcome": "patch",
            "rationale": "Increase the existing key light for the dark preview.",
            "operations": [{"path": "/lights/0/intensity", "value": 100}],
            "missing_capabilities": [],
        })
        with tempfile.TemporaryDirectory() as directory:
            run_root, spec = prepare_run(Path(directory), report_category="lighting")
            result = plan_correction(
                FakeCorrectionClient(content),
                model="gpt-oss:20b",
                run_directory=run_root,
            )

        self.assertEqual(result.decision.outcome, "patch")
        self.assertEqual(result.corrected_spec.lights[0].intensity, 100)
        self.assertEqual(result.decision.patch.base_spec_sha256, canonical_sha256(spec))

    def test_asset_replacement_path_is_rejected(self):
        content = json.dumps({
            "outcome": "patch",
            "rationale": "Attempt an unsafe asset replacement.",
            "operations": [{
                "path": "/objects/0/asset/asset_id",
                "value": "invented_asset",
            }],
            "missing_capabilities": [],
        })
        with tempfile.TemporaryDirectory() as directory:
            run_root, _ = prepare_run(Path(directory))
            with self.assertRaisesRegex(CorrectionPlanningError, "forbidden"):
                plan_correction(
                    FakeCorrectionClient(content),
                    model="gpt-oss:20b",
                    run_directory=run_root,
                )

    def test_report_hash_mismatch_is_rejected_before_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            run_root, _ = prepare_run(Path(directory))
            value = json.loads((run_root / "evaluation.json").read_text(encoding="utf-8"))
            value["render_spec_sha256"] = "a" * 64
            (run_root / "evaluation.json").write_text(json.dumps(value), encoding="utf-8")
            client = FakeCorrectionClient("{}")
            with self.assertRaisesRegex(CorrectionPlanningError, "hashes differ"):
                plan_correction(
                    client,
                    model="gpt-oss:20b",
                    run_directory=run_root,
                )
            self.assertIsNone(client.last_request)


if __name__ == "__main__":
    unittest.main()
