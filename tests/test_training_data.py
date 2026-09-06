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
    TrainingCampaignRun,
    WorkflowIteration,
)
from render_master_bot.models import RenderSpec
from render_master_bot.serialization import canonical_sha256
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

    def _write_campaign(self, source: Path, name: str, cases: list[tuple[str, str]]) -> Path:
        campaign = source / name
        results = []
        timestamp = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
        for case_id, prompt in cases:
            path = self._write_workflow(
                campaign, workflow_id=f"{name}_{case_id}", prompt=prompt,
            )
            workflow = json.loads(path.read_text(encoding="utf-8"))
            final_artifact = next(
                item.copy() for item in workflow["output_artifacts"]
                if item["role"] == "initial_render_spec"
            )
            final_artifact["role"] = "final_render_spec"
            workflow["output_artifacts"].append(final_artifact)
            _write_json(path, workflow)
            results.append({
                "case_id": case_id, "category": "single_asset", "status": "succeeded",
                "training_eligible": True,
                "attempts": [{
                    "attempt": 1, "workflow_id": workflow["workflow_id"],
                    "workflow_directory": path.parent.relative_to(campaign).as_posix(),
                    "started_at": timestamp, "finished_at": timestamp, "duration_seconds": 0,
                    "outcome": "succeeded", "stop_reason": "evaluator_passed",
                    "workflow_manifest_path": path.relative_to(campaign).as_posix(),
                    "workflow_manifest_sha256": _sha256_file(path),
                    "visual_verdict": "pass", "expectation_passed": True,
                    "training_eligible": True,
                }],
            })
        run = TrainingCampaignRun(
            campaign_id=name, suite_id="test_suite", suite_sha256="1" * 64,
            project_name="OptimizationPlugin", project_descriptor_sha256="0" * 64,
            asset_catalog_sha256="2" * 64,
            planner={"provider": "local", "model": "golden-reference-v0.1"},
            evaluator={"provider": "ollama", "model": "test"},
            status="completed", selected_case_ids=[case[0] for case in cases],
            started_at=timestamp, finished_at=timestamp, case_results=results,
        )
        path = campaign / "campaign_manifest.json"
        _write_json(path, run.model_dump(mode="json"))
        return path

    def test_merge_is_order_independent_and_excludes_unselected_campaigns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            a = self._write_campaign(source, "campaign_a", [("a", "Door A"), ("b", "Door B")])
            b = self._write_campaign(source, "campaign_b", [("c", "Door C")])
            self._write_workflow(source, workflow_id="unselected", prompt="Do not train on me")
            first = build_training_dataset(
                source_root=source, output_directory=root / "first", campaign_run_paths=[a, b],
            )
            second = build_training_dataset(
                source_root=source, output_directory=root / "second", campaign_run_paths=[b, a],
            )
            self.assertEqual(first.manifest_path.read_bytes(), second.manifest_path.read_bytes())
            self.assertEqual(first.manifest.discovered_source_manifest_count, 3)
            self.assertEqual(first.manifest.total_example_count, 3)
            self.assertEqual(first.manifest.builder_version, "0.3")
            self.assertTrue(first.audit.source_evidence_checked)
            self.assertEqual(first.audit.campaign_snapshot_count, 2)
            self.assertEqual(first.audit.integrity_status, "passed", first.audit.issues)
            for reference in first.manifest.campaign_sources:
                self.assertEqual(
                    (first.manifest_path.parent / reference.snapshot_path).read_bytes(),
                    (source / reference.source_run_path).read_bytes(),
                )
            portable_audit = audit_training_dataset(first.manifest_path)
            self.assertEqual(portable_audit.integrity_status, "passed")
            self.assertFalse(portable_audit.source_evidence_checked)

    def test_merge_deduplicates_content_and_keeps_case_reruns_in_one_split(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            a = self._write_campaign(source, "a", [
                ("shared", "Door shared A"), ("duplicate", "Identical door"), ("unique_a", "A"),
            ])
            b = self._write_campaign(source, "b", [
                ("shared", "Door shared B"), ("duplicate", "Identical door"), ("unique_b", "B"),
            ])
            result = build_training_dataset(
                source_root=source, output_directory=root / "dataset", campaign_run_paths=[a, b],
            )
            self.assertEqual(result.manifest.total_example_count, 5)
            self.assertEqual([s.reason for s in result.manifest.skipped_sources], ["duplicate_example"])
            shared = []
            for split in result.manifest.splits:
                for line in (result.manifest_path.parent / split.jsonl_path).read_text().splitlines():
                    example = json.loads(line)
                    if "shared" in example["source_manifest_path"]:
                        shared.append((split.name, example["source_group_id"]))
            self.assertEqual(len(shared), 2)
            self.assertEqual(shared[0], shared[1])
            self.assertEqual(result.audit.integrity_status, "passed", result.audit.issues)

    def test_merge_rejects_invalid_campaign_inputs_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            a = self._write_campaign(source, "a", [("a", "Door A")])
            b = self._write_campaign(source, "b", [("b", "Door B")])
            original = b.read_text()
            for failure in ("duplicate_path", "duplicate_identity", "empty", "identity",
                            "unfinished", "missing", "outside"):
                with self.subTest(failure=failure):
                    b.write_text(original)
                    paths = [a, b]
                    payload = json.loads(original)
                    if failure == "duplicate_path":
                        paths = [a, a]
                    elif failure == "duplicate_identity":
                        payload["campaign_id"] = "a"
                    elif failure == "empty":
                        paths = []
                    elif failure == "identity":
                        payload["asset_catalog_sha256"] = "3" * 64
                    elif failure == "unfinished":
                        payload["status"] = "cancelled"
                    elif failure == "missing":
                        payload["case_results"][0]["attempts"][0]["workflow_manifest_path"] = "missing.json"
                    elif failure == "outside":
                        paths = [a, root / "outside.json"]
                    _write_json(b, payload)
                    output = root / f"output_{failure}"
                    with self.assertRaises(TrainingDataError):
                        build_training_dataset(
                            source_root=source, output_directory=output, campaign_run_paths=paths,
                        )
                    self.assertFalse(output.exists())

    def test_merge_audit_detects_snapshot_and_original_evidence_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            a = self._write_campaign(source, "a", [("a", "Door A")])
            result = build_training_dataset(
                source_root=source, output_directory=root / "dataset", campaign_run_paths=[a],
            )
            reference = result.manifest.campaign_sources[0]
            snapshot = result.manifest_path.parent / reference.snapshot_path
            original_snapshot = snapshot.read_bytes()
            snapshot.write_bytes(b"{}")
            self.assertEqual(audit_training_dataset(result.manifest_path).integrity_status, "failed")
            snapshot.write_bytes(original_snapshot)
            # A source change is visible only to the explicitly requested source audit.
            artifact = source / "a/workflows/a_a/planning/render_spec.json"
            original_artifact = artifact.read_bytes()
            artifact.write_text("{}")
            self.assertEqual(audit_training_dataset(result.manifest_path).integrity_status, "passed")
            audit = audit_training_dataset(result.manifest_path, source_root=source)
            self.assertEqual(audit.integrity_status, "failed")
            self.assertGreater(audit.file_hash_error_count, 0)
            artifact.write_bytes(original_artifact)
            a.write_bytes(a.read_bytes() + b"\n")
            campaign_audit = audit_training_dataset(result.manifest_path, source_root=source)
            self.assertEqual(campaign_audit.integrity_status, "failed")
            self.assertTrue(any("original campaign" in issue for issue in campaign_audit.issues))

    def test_merge_audit_rejects_rehashed_case_group_forgery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            campaign = self._write_campaign(source, "a", [("a", "Door A")])
            result = build_training_dataset(
                source_root=source, output_directory=root / "dataset", campaign_run_paths=[campaign],
            )
            split_path = root / "dataset/splits/train.jsonl"
            example = json.loads(split_path.read_text())
            example["source_group_id"] = "forged_case_group"
            example["example_sha256"] = canonical_sha256({
                key: value for key, value in example.items() if key != "example_sha256"
            })
            split_path.write_text(json.dumps(example) + "\n", encoding="utf-8")
            manifest = json.loads(result.manifest_path.read_text())
            manifest["splits"][0]["source_group_ids"] = ["forged_case_group"]
            manifest["splits"][0]["sha256"] = _sha256_file(split_path)
            manifest["dataset_sha256"] = canonical_sha256({
                key: value for key, value in manifest.items()
                if key != "dataset_sha256" and value is not None
            })
            _write_json(result.manifest_path, manifest)
            audit = audit_training_dataset(result.manifest_path)
            self.assertEqual(audit.integrity_status, "failed")
            self.assertTrue(any("campaign case group" in issue for issue in audit.issues))

    def test_merge_excludes_ineligible_and_changed_workflows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            campaign = self._write_campaign(source, "a", [
                ("accepted", "A"), ("changed", "B"), ("ineligible", "C"),
            ])
            payload = json.loads(campaign.read_text())
            rejected = payload["case_results"][2]
            rejected["training_eligible"] = False
            rejected["attempts"][0]["training_eligible"] = False
            rejected["attempts"][0]["visual_verdict"] = "needs_review"
            _write_json(campaign, payload)
            changed = source / "a/workflows/a_changed/workflow_manifest.json"
            changed.write_bytes(changed.read_bytes() + b"\n")
            result = build_training_dataset(
                source_root=source, output_directory=root / "dataset", campaign_run_paths=[campaign],
            )
            self.assertEqual(result.manifest.total_example_count, 1)
            self.assertEqual({s.reason for s in result.manifest.skipped_sources},
                             {"hash_mismatch", "campaign_not_eligible"})
            self.assertEqual(result.audit.integrity_status, "passed", result.audit.issues)

    def test_cli_merge_accepts_repeated_campaigns_and_source_audit(self):
        args = build_parser().parse_args([
            "training-data", "merge", "--source-root", "private", "--output-dir", "merged",
            "--campaign-run", "a/campaign_manifest.json", "--campaign-run", "b/campaign_manifest.json",
        ])
        self.assertEqual(args.campaign_runs, ["a/campaign_manifest.json", "b/campaign_manifest.json"])
        audit = build_parser().parse_args([
            "training-data", "audit", "merged/dataset_manifest.json", "--source-root", "private",
        ])
        self.assertEqual(audit.source_root, "private")

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
