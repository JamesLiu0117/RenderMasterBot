import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from render_master_bot.asset_index import AssetSearchHit, asset_document
from render_master_bot.cli import build_parser
from render_master_bot.contracts import (
    ArtifactRecord,
    AssetCard,
    RenderWorkflowManifest,
    WorkflowIteration,
)
from render_master_bot.models import RenderSpec
from render_master_bot.training_data import (
    TrainingDataError,
    audit_training_dataset,
    build_training_dataset,
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class TrainingDataTests(unittest.TestCase):
    def _write_workflow(
        self,
        source_root: Path,
        *,
        workflow_id: str,
        prompt: str,
        succeeded: bool = True,
    ) -> Path:
        workflow_root = source_root / "workflows" / workflow_id
        card = AssetCard(
            asset_id="sm_door",
            engine_path="/Game/Test/SM_Door",
            display_name="SM_Door",
            asset_type="static_mesh",
            description="A production test door",
            tags=["door"],
            dimensions_cm={"x": 100, "y": 20, "z": 210},
            material_slots=["DoorSurface"],
        )
        hit = AssetSearchHit(
            rank=1,
            asset_id=card.asset_id,
            display_name=card.display_name,
            asset_type=card.asset_type,
            engine_path=card.engine_path,
            distance=0.1,
            similarity=0.9,
            document=asset_document(card),
        )
        spec = RenderSpec.model_validate(
            {
                "source_prompt": prompt,
                "scene_name": "door_scene",
                "objects": [
                    {
                        "object_id": "door",
                        "asset": {"asset_id": "sm_door"},
                    }
                ],
                "camera": {"camera_id": "main_camera", "transform": {}},
                "lights": [],
                "render": {},
                "notes": ["Untrusted planner commentary that is not executable."],
            }
        )
        paths = {
            "retrieval_results": workflow_root / "planning" / "retrieval.json",
            "retrieved_asset_catalog": (
                workflow_root / "planning" / "retrieved_asset_cards.json"
            ),
            "planner_raw": workflow_root / "planning" / "planner_raw.json",
            "initial_render_spec": workflow_root / "planning" / "render_spec.json",
        }
        _write_json(paths["retrieval_results"], [hit.__dict__])
        _write_json(
            paths["retrieved_asset_catalog"],
            [card.model_dump(mode="json")],
        )
        _write_json(paths["planner_raw"], spec.model_dump(mode="json"))
        _write_json(paths["initial_render_spec"], spec.model_dump(mode="json"))
        artifacts = [
            ArtifactRecord(
                role=role,
                path=path.relative_to(workflow_root).as_posix(),
                sha256=_sha256_file(path),
            )
            for role, path in paths.items()
        ]
        timestamp = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
        common = {
            "workflow_id": workflow_id,
            "stage": "complete",
            "prompt": prompt,
            "project_name": "OptimizationPlugin",
            "project_descriptor_sha256": "0" * 64,
            "planner": {"provider": "ollama", "model": "qwen3.5:9b"},
            "evaluator": {"provider": "ollama", "model": "qwen3-vl:8b-instruct"},
            "max_iterations": 1,
            "retrieved_asset_ids": ["sm_door"],
            "started_at": timestamp,
            "finished_at": timestamp,
            "output_artifacts": artifacts,
        }
        if succeeded:
            manifest = RenderWorkflowManifest(
                **common,
                status="succeeded",
                stop_reason="evaluator_passed",
                iterations=[
                    WorkflowIteration(
                        iteration=1,
                        run_directory="iterations/iteration-001",
                        render_spec_sha256=_sha256_file(paths["initial_render_spec"]),
                        preflight_report_sha256="1" * 64,
                        preflight_verdict="pass",
                        preview_manifest_sha256="2" * 64,
                        evaluation_report_sha256="3" * 64,
                        evaluation_verdict="pass",
                    )
                ],
            )
        else:
            manifest = RenderWorkflowManifest(
                **common,
                status="failed",
                stop_reason="stage_failed",
                errors=["synthetic failure"],
            )
        manifest_path = workflow_root / "workflow_manifest.json"
        _write_json(manifest_path, manifest.model_dump(mode="json"))
        return manifest_path

    def test_build_is_deterministic_and_grouped_without_leakage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            for index in range(3):
                self._write_workflow(
                    source,
                    workflow_id=f"workflow_{index}",
                    prompt=f"Create test door composition {index}",
                )

            first = build_training_dataset(
                source_root=source,
                output_directory=root / "dataset-a",
            )
            second = build_training_dataset(
                source_root=source,
                output_directory=root / "dataset-b",
            )

            self.assertEqual(first.manifest.dataset_sha256, second.manifest.dataset_sha256)
            self.assertEqual(
                first.manifest_path.read_bytes(),
                second.manifest_path.read_bytes(),
            )
            for name in ("train", "validation", "test"):
                self.assertEqual(
                    (root / "dataset-a" / "splits" / f"{name}.jsonl").read_bytes(),
                    (root / "dataset-b" / "splits" / f"{name}.jsonl").read_bytes(),
                )
            self.assertEqual(first.audit.integrity_status, "passed")
            self.assertEqual(first.audit.valid_example_count, 3)
            self.assertEqual(first.audit.cross_split_group_overlap_count, 0)
            self.assertEqual(first.audit.split_counts, {"train": 1, "validation": 1, "test": 1})
            self.assertFalse(first.audit.qlora_smoke_ready)
            for name in ("train", "validation", "test"):
                example = json.loads(
                    (root / "dataset-a" / "splits" / f"{name}.jsonl")
                    .read_text(encoding="utf-8")
                    .strip()
                )
                assistant = json.loads(example["messages"][-1]["content"])
                self.assertEqual(assistant["notes"], [])

    def test_nonpassing_hash_mismatch_and_duplicate_sources_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            self._write_workflow(
                source,
                workflow_id="accepted",
                prompt="Create the accepted door",
            )
            self._write_workflow(
                source,
                workflow_id="failed",
                prompt="Create the failed door",
                succeeded=False,
            )
            tampered = self._write_workflow(
                source,
                workflow_id="tampered",
                prompt="Create the tampered door",
            )
            (tampered.parent / "planning" / "render_spec.json").write_text(
                "{}\n",
                encoding="utf-8",
            )
            self._write_workflow(
                source,
                workflow_id="duplicate_a",
                prompt="Create one duplicate door",
            )
            self._write_workflow(
                source,
                workflow_id="duplicate_b",
                prompt="Create one duplicate door",
            )

            result = build_training_dataset(
                source_root=source,
                output_directory=root / "dataset",
            )

            reasons = [skip.reason for skip in result.manifest.skipped_sources]
            self.assertEqual(result.manifest.discovered_source_manifest_count, 5)
            self.assertEqual(result.manifest.total_example_count, 2)
            self.assertIn("nonpassing_workflow", reasons)
            self.assertIn("hash_mismatch", reasons)
            self.assertIn("duplicate_example", reasons)
            self.assertEqual(result.audit.integrity_status, "passed")

    def test_audit_detects_tampering_and_cross_split_group_leakage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            for index in range(3):
                self._write_workflow(
                    source,
                    workflow_id=f"workflow_{index}",
                    prompt=f"Build door {index}",
                )
            result = build_training_dataset(
                source_root=source,
                output_directory=root / "dataset",
            )
            train_path = root / "dataset" / "splits" / "train.jsonl"
            test_path = root / "dataset" / "splits" / "test.jsonl"
            train_path.write_bytes(train_path.read_bytes() + test_path.read_bytes())

            audit = audit_training_dataset(result.manifest_path)

            self.assertEqual(audit.integrity_status, "failed")
            self.assertGreater(audit.file_hash_error_count, 0)
            self.assertGreater(audit.manifest_membership_error_count, 0)
            self.assertGreater(audit.cross_split_example_overlap_count, 0)
            self.assertGreater(audit.cross_split_group_overlap_count, 0)
            self.assertFalse(audit.qlora_smoke_ready)

    def test_cli_exposes_build_and_audit_commands(self):
        build_args = build_parser().parse_args(
            [
                "training-data",
                "build",
                "--source-root",
                "private-data",
                "--output-dir",
                "private-training-v1",
            ]
        )
        audit_args = build_parser().parse_args(
            ["training-data", "audit", "private-training-v1/dataset_manifest.json"]
        )

        self.assertEqual(build_args.dataset_id, "renderbot_stage0_v1")
        self.assertEqual(build_args.validation_fraction, 0.1)
        self.assertEqual(audit_args.training_data_command, "audit")

    def test_invalid_dataset_identity_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            output = root / "dataset"

            with self.assertRaisesRegex(TrainingDataError, "dataset identity"):
                build_training_dataset(
                    source_root=source,
                    output_directory=output,
                    dataset_id="Invalid Dataset ID",
                )

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
