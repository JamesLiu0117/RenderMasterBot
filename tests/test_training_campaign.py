import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.asset_index import AssetSearchHit, asset_document
from render_master_bot.cli import build_parser
from render_master_bot.contracts import (
    ArtifactRecord,
    AssetCard,
    RenderWorkflowManifest,
    TrainingBaselineReport,
    TrainingCampaignRun,
    TrainingCampaignSuite,
    WorkflowIteration,
)
from render_master_bot.models import RenderSpec
from render_master_bot.serialization import canonical_sha256
from render_master_bot.training_campaign import (
    CAMPAIGN_MANIFEST_FILENAME,
    build_training_baseline_report,
    cancel_training_campaign,
    create_training_campaign_plan,
    load_training_campaign_run,
    _primary_failure_reason,
    run_training_campaign,
)
from render_master_bot.training_data import build_training_dataset


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class TrainingCampaignTests(unittest.TestCase):
    def _case(self, case_id: str, *, required_asset: str = "mesh_a") -> dict:
        return {
            "case_id": case_id,
            "category": "single_asset",
            "prompt": f"Create a product preview for {case_id}",
            "expectation": {
                "required_scene_asset_ids": [required_asset],
                "minimum_object_count": 1,
                "maximum_object_count": 1,
            },
        }

    def _suite_files(
        self,
        root: Path,
        *,
        cases: list[dict],
    ) -> tuple[Path, Path, Path, TrainingCampaignSuite]:
        project = root / "OptimizationPlugin.uproject"
        project.write_text('{"FileVersion":3}\n', encoding="utf-8")
        catalog = root / "asset_cards.json"
        _write_json(catalog, [])
        suite = TrainingCampaignSuite(
            suite_id="optimization_training_v1",
            description="Synthetic isolated campaign suite",
            project_name="OptimizationPlugin",
            project_descriptor_sha256=_sha256_file(project),
            asset_catalog_sha256=_sha256_file(catalog),
            cases=cases,
        )
        suite_path = root / "suite.json"
        _write_json(suite_path, suite.model_dump(mode="json"))
        return suite_path, project, catalog, suite

    def _write_successful_workflow(
        self,
        workflow_directory: Path,
        *,
        workflow_id: str,
        prompt: str,
        scene_asset_id: str = "mesh_a",
        material_asset_id: str | None = None,
    ) -> Path:
        mesh = AssetCard(
            asset_id=scene_asset_id,
            engine_path=f"/Game/Test/{scene_asset_id}",
            display_name=scene_asset_id,
            asset_type="static_mesh",
            description="Synthetic campaign mesh",
            dimensions_cm={"x": 100, "y": 100, "z": 100},
            material_slots=["Material_0"],
        )
        cards = [mesh]
        if material_asset_id is not None:
            cards.append(
                AssetCard(
                    asset_id=material_asset_id,
                    engine_path=f"/Game/Test/{material_asset_id}",
                    display_name=material_asset_id,
                    asset_type="material",
                    description="Synthetic campaign material",
                )
            )
        hits = [
            AssetSearchHit(
                rank=index,
                asset_id=card.asset_id,
                display_name=card.display_name,
                asset_type=card.asset_type,
                engine_path=card.engine_path,
                distance=0.1 * index,
                similarity=1.0 - 0.1 * index,
                document=asset_document(card),
            )
            for index, card in enumerate(cards, start=1)
        ]
        materials = (
            [
                {
                    "slot_name": "Material_0",
                    "material": {"asset_id": material_asset_id},
                }
            ]
            if material_asset_id is not None
            else []
        )
        spec = RenderSpec.model_validate(
            {
                "source_prompt": prompt,
                "scene_name": f"{scene_asset_id}_scene",
                "objects": [
                    {
                        "object_id": "subject",
                        "asset": {"asset_id": scene_asset_id},
                        "materials": materials,
                    }
                ],
                "camera": {"camera_id": "main_camera", "transform": {}},
                "lights": [],
                "render": {},
                "notes": [],
            }
        )
        paths = {
            "retrieval_results": workflow_directory / "planning" / "retrieval.json",
            "retrieved_asset_catalog": (
                workflow_directory / "planning" / "retrieved_asset_cards.json"
            ),
            "planner_raw": workflow_directory / "planning" / "planner_raw.json",
            "initial_render_spec": workflow_directory / "planning" / "render_spec.json",
            "final_render_spec": workflow_directory / "final_render_spec.json",
        }
        _write_json(paths["retrieval_results"], [hit.__dict__ for hit in hits])
        _write_json(
            paths["retrieved_asset_catalog"],
            [card.model_dump(mode="json") for card in cards],
        )
        for role in ("planner_raw", "initial_render_spec", "final_render_spec"):
            _write_json(paths[role], spec.model_dump(mode="json"))
        artifacts = [
            ArtifactRecord(
                role=role,
                path=path.relative_to(workflow_directory).as_posix(),
                sha256=_sha256_file(path),
            )
            for role, path in paths.items()
        ]
        timestamp = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
        manifest = RenderWorkflowManifest(
            workflow_id=workflow_id,
            status="succeeded",
            stage="complete",
            stop_reason="evaluator_passed",
            prompt=prompt,
            project_name="OptimizationPlugin",
            project_descriptor_sha256="0" * 64,
            planner={"provider": "ollama", "model": "base-planner"},
            evaluator={"provider": "ollama", "model": "base-vision"},
            max_iterations=1,
            retrieved_asset_ids=[card.asset_id for card in cards],
            started_at=timestamp,
            finished_at=timestamp,
            output_artifacts=artifacts,
            iterations=[
                WorkflowIteration(
                    iteration=1,
                    run_directory="iterations/iteration-001",
                    render_spec_sha256=_sha256_file(paths["final_render_spec"]),
                    preflight_report_sha256="1" * 64,
                    preflight_verdict="pass",
                    preview_manifest_sha256="2" * 64,
                    evaluation_report_sha256="3" * 64,
                    evaluation_verdict="pass",
                )
            ],
        )
        manifest_path = workflow_directory / "workflow_manifest.json"
        _write_json(manifest_path, manifest.model_dump(mode="json"))
        return manifest_path

    def _create_plan(
        self,
        root: Path,
        *,
        cases: list[dict],
        max_cases: int | None = None,
        case_ids: list[str] | None = None,
        max_attempts: int = 2,
    ) -> tuple[Path, Path, TrainingCampaignRun]:
        suite_path, project, catalog, _ = self._suite_files(root, cases=cases)
        campaign = root / "campaign"
        manifest_path, run = create_training_campaign_plan(
            suite_path=suite_path,
            campaign_directory=campaign,
            campaign_id="campaign_v1",
            project_path=project,
            asset_catalog_path=catalog,
            planner_model="base-planner",
            evaluator_model="base-vision",
            max_cases=max_cases,
            case_ids=case_ids,
            max_attempts_per_case=max_attempts,
        )
        return campaign, manifest_path, run

    def test_plan_is_deterministic_budgeted_and_non_executing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = [self._case(f"case_{index}") for index in range(3)]
            first_root = root / "first"
            second_root = root / "second"
            first_root.mkdir()
            second_root.mkdir()
            _, first_manifest, first = self._create_plan(
                first_root,
                cases=cases,
                max_cases=2,
            )
            _, second_manifest, second = self._create_plan(
                second_root,
                cases=cases,
                max_cases=2,
            )

            self.assertEqual(first.status, "planned")
            self.assertEqual(first.selected_case_ids, ["case_0", "case_1"])
            self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))
            self.assertEqual(first_manifest.read_bytes(), second_manifest.read_bytes())
            self.assertFalse((first_manifest.parent / "workflows").exists())

    def test_plan_can_select_an_exact_ordered_case_subset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = [self._case(f"case_{index}") for index in range(3)]
            _, _, run = self._create_plan(
                root,
                cases=cases,
                case_ids=["case_2", "case_0"],
            )

            self.assertEqual(run.selected_case_ids, ["case_2", "case_0"])

    def test_plan_rejects_conflicting_case_selectors_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_path, project, catalog, _ = self._suite_files(
                root,
                cases=[self._case("case_0"), self._case("case_1")],
            )
            campaign = root / "campaign"

            with self.assertRaisesRegex(Exception, "cannot be used together"):
                create_training_campaign_plan(
                    suite_path=suite_path,
                    campaign_directory=campaign,
                    campaign_id="campaign_v1",
                    project_path=project,
                    asset_catalog_path=catalog,
                    planner_model="base-planner",
                    evaluator_model="base-vision",
                    max_cases=1,
                    case_ids=["case_1"],
                )

            self.assertFalse(campaign.exists())

    def test_run_isolates_failure_and_freezes_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign, manifest_path, _ = self._create_plan(
                root,
                cases=[self._case("good"), self._case("bad")],
            )

            def runner(case, workflow_directory, workflow_id):
                if case.case_id == "bad":
                    raise RuntimeError("synthetic isolated failure")
                return self._write_successful_workflow(
                    workflow_directory,
                    workflow_id=workflow_id,
                    prompt=case.prompt,
                )

            run = run_training_campaign(
                campaign_manifest_path=manifest_path,
                case_runner=runner,
            )
            baseline = build_training_baseline_report(
                run,
                report_id="baseline_v1",
            )

            self.assertEqual(run.status, "completed")
            self.assertEqual([result.status for result in run.case_results], ["succeeded", "failed"])
            self.assertEqual(sum(result.training_eligible for result in run.case_results), 1)
            self.assertEqual(baseline.workflow_succeeded_count, 1)
            self.assertEqual(baseline.training_eligible_count, 1)
            self.assertEqual(baseline.failure_reason_counts, {"workflow_failed": 1})
            self.assertEqual(
                baseline.report_sha256,
                canonical_sha256(
                    baseline.model_dump(mode="json", exclude={"report_sha256"})
                ),
            )
            self.assertTrue((campaign / CAMPAIGN_MANIFEST_FILENAME).is_file())

    def test_host_expectation_blocks_visually_passing_wrong_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest_path, _ = self._create_plan(
                root,
                cases=[self._case("wrong_asset", required_asset="mesh_a")],
            )

            def runner(case, workflow_directory, workflow_id):
                return self._write_successful_workflow(
                    workflow_directory,
                    workflow_id=workflow_id,
                    prompt=case.prompt,
                    scene_asset_id="mesh_b",
                )

            run = run_training_campaign(
                campaign_manifest_path=manifest_path,
                case_runner=runner,
            )
            attempt = run.case_results[0].attempts[-1]

            self.assertEqual(attempt.outcome, "succeeded")
            self.assertEqual(attempt.visual_verdict, "pass")
            self.assertFalse(attempt.expectation_passed)
            self.assertFalse(attempt.training_eligible)
            self.assertIn("missing required scene assets", attempt.expectation_failures[0])

    def test_host_expectation_blocks_unsolicited_material_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest_path, _ = self._create_plan(
                root,
                cases=[self._case("neutral_asset")],
            )

            def runner(case, workflow_directory, workflow_id):
                return self._write_successful_workflow(
                    workflow_directory,
                    workflow_id=workflow_id,
                    prompt=case.prompt,
                    material_asset_id="material_a",
                )

            run = run_training_campaign(
                campaign_manifest_path=manifest_path,
                case_runner=runner,
            )
            attempt = run.case_results[0].attempts[-1]

            self.assertEqual(attempt.outcome, "succeeded")
            self.assertFalse(attempt.expectation_passed)
            self.assertFalse(attempt.training_eligible)
            self.assertTrue(
                any(
                    failure.startswith("material assignment count above maximum")
                    for failure in attempt.expectation_failures
                )
            )

    def test_retry_preserves_logical_workflow_group_and_prior_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest_path, _ = self._create_plan(
                root,
                cases=[self._case("retry_case")],
                max_attempts=2,
            )

            first = run_training_campaign(
                campaign_manifest_path=manifest_path,
                case_runner=lambda *_: (_ for _ in ()).throw(RuntimeError("first failure")),
            )
            self.assertFalse(first.case_results[0].training_eligible)

            def retry_runner(case, workflow_directory, workflow_id):
                return self._write_successful_workflow(
                    workflow_directory,
                    workflow_id=workflow_id,
                    prompt=case.prompt,
                )

            second = run_training_campaign(
                campaign_manifest_path=manifest_path,
                case_runner=retry_runner,
                retry_failed=True,
            )
            attempts = second.case_results[0].attempts

            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[0].workflow_id, attempts[1].workflow_id)
            self.assertNotEqual(
                attempts[0].workflow_directory,
                attempts[1].workflow_directory,
            )
            self.assertTrue(second.case_results[0].training_eligible)

    def test_active_terminal_attempt_is_recovered_without_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign, manifest_path, planned = self._create_plan(
                root,
                cases=[self._case("recover_case")],
            )
            started = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
            active = TrainingCampaignRun.model_validate(
                {
                    **planned.model_dump(mode="json"),
                    "status": "running",
                    "started_at": started,
                    "active_case_id": "recover_case",
                    "active_attempt": 1,
                    "active_started_at": started,
                }
            )
            _write_json(manifest_path, active.model_dump(mode="json"))
            workflow_directory = (
                campaign / "workflows" / "recover_case" / "attempt-001"
            )
            self._write_successful_workflow(
                workflow_directory,
                workflow_id="campaign_v1_recover_case",
                prompt="Create a product preview for recover_case",
            )
            calls = []

            run = run_training_campaign(
                campaign_manifest_path=manifest_path,
                case_runner=lambda *args: calls.append(args),
            )

            self.assertEqual(calls, [])
            self.assertEqual(run.status, "completed")
            self.assertTrue(run.case_results[0].training_eligible)

    def test_cancel_recovers_active_evidence_and_remains_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign, manifest_path, planned = self._create_plan(
                root,
                cases=[self._case("cancel_case")],
            )
            started = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
            active = TrainingCampaignRun.model_validate(
                {
                    **planned.model_dump(mode="json"),
                    "status": "running",
                    "started_at": started,
                    "active_case_id": "cancel_case",
                    "active_attempt": 1,
                    "active_started_at": started,
                }
            )
            _write_json(manifest_path, active.model_dump(mode="json"))
            self._write_successful_workflow(
                campaign / "workflows" / "cancel_case" / "attempt-001",
                workflow_id="campaign_v1_cancel_case",
                prompt="Create a product preview for cancel_case",
            )

            cancelled = cancel_training_campaign(
                manifest_path,
                reason="paused for operator review",
            )
            repeated = cancel_training_campaign(manifest_path)

            self.assertEqual(cancelled.status, "cancelled")
            self.assertIsNone(cancelled.active_case_id)
            self.assertEqual(len(cancelled.case_results), 1)
            self.assertEqual(cancelled.case_results[0].status, "succeeded")
            self.assertIn("paused for operator review", cancelled.errors)
            self.assertEqual(repeated, cancelled)

            calls = []
            resumed = run_training_campaign(
                campaign_manifest_path=manifest_path,
                case_runner=lambda *args: calls.append(args),
            )
            self.assertEqual(calls, [])
            self.assertEqual(resumed.status, "completed")

    def test_cancel_command_parser_preserves_reason(self):
        args = build_parser().parse_args(
            [
                "training-campaign",
                "cancel",
                "campaign_manifest.json",
                "--reason",
                "paused after operator request",
            ]
        )
        self.assertEqual(args.reason, "paused after operator request")

    def test_campaign_gate_allows_only_hash_bound_eligible_workflows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign, manifest_path, _ = self._create_plan(
                root,
                cases=[
                    self._case("eligible", required_asset="mesh_a"),
                    self._case("ineligible", required_asset="mesh_a"),
                ],
            )

            def runner(case, workflow_directory, workflow_id):
                asset_id = "mesh_a" if case.case_id == "eligible" else "mesh_b"
                return self._write_successful_workflow(
                    workflow_directory,
                    workflow_id=workflow_id,
                    prompt=case.prompt,
                    scene_asset_id=asset_id,
                )

            run_training_campaign(
                campaign_manifest_path=manifest_path,
                case_runner=runner,
            )
            dataset = build_training_dataset(
                source_root=campaign,
                output_directory=root / "dataset",
                dataset_id="campaign_dataset_v1",
                campaign_run_path=manifest_path,
            )

            self.assertEqual(dataset.manifest.total_example_count, 1)
            example = json.loads(
                (root / "dataset" / "splits" / "train.jsonl")
                .read_text(encoding="utf-8")
                .strip()
            )
            self.assertIn(
                "final_render_spec",
                {artifact["role"] for artifact in example["source_artifacts"]},
            )
            self.assertEqual(
                [skip.reason for skip in dataset.manifest.skipped_sources],
                ["campaign_not_eligible"],
            )
            self.assertEqual(
                dataset.manifest.campaign_run_path,
                CAMPAIGN_MANIFEST_FILENAME,
            )
            self.assertEqual(
                dataset.manifest.campaign_run_sha256,
                canonical_sha256(load_training_campaign_run(manifest_path)),
            )
            self.assertEqual(dataset.audit.integrity_status, "passed")

    def test_baseline_contract_rejects_tampered_aggregate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest_path, _ = self._create_plan(
                root,
                cases=[self._case("good")],
            )

            def runner(case, workflow_directory, workflow_id):
                return self._write_successful_workflow(
                    workflow_directory,
                    workflow_id=workflow_id,
                    prompt=case.prompt,
                )

            run = run_training_campaign(
                campaign_manifest_path=manifest_path,
                case_runner=runner,
            )
            baseline = build_training_baseline_report(run, report_id="baseline_v1")
            value = baseline.model_dump(mode="json")
            value["training_eligible_count"] = 0

            with self.assertRaises(ValidationError):
                TrainingBaselineReport.model_validate(value)

    def test_baseline_failure_taxonomy_preserves_actionable_root_causes(self):
        base = {
            "attempt": 1,
            "workflow_id": "workflow_v1",
            "workflow_directory": "workflows/attempt-001",
            "started_at": "2026-08-24T12:00:00Z",
            "finished_at": "2026-08-24T12:00:01Z",
            "duration_seconds": 1,
            "outcome": "failed",
            "expectation_passed": False,
            "expectation_failures": ["workflow did not pass"],
            "training_eligible": False,
        }
        cases = {
            "no meaningful visible pixels": "black_or_blank_preview",
            "correction model proposed an invalid patch": "invalid_correction_patch",
            "global exposure correction is unsupported": "unsupported_exposure_correction",
            "Ollama returned empty message.content": "ollama_empty_content",
            "asset has no dimensions_cm evidence": "dimensionless_asset_selected",
            "unclassified failure": "workflow_failed",
        }
        from render_master_bot.contracts import TrainingCampaignAttempt

        for detail, expected in cases.items():
            with self.subTest(detail=detail):
                attempt = TrainingCampaignAttempt.model_validate({**base, "error": detail})
                self.assertEqual(_primary_failure_reason(attempt), expected)

    def test_cli_exposes_campaign_and_campaign_gated_dataset_commands(self):
        plan = build_parser().parse_args(
            [
                "training-campaign",
                "plan",
                "--suite",
                "suite.json",
                "--campaign-dir",
                "campaign",
                "--campaign-id",
                "campaign_v1",
                "--path",
                "OptimizationPlugin.uproject",
                "--assets",
                "asset_cards.json",
                "--max-cases",
                "3",
            ]
        )
        dataset = build_parser().parse_args(
            [
                "training-data",
                "build",
                "--source-root",
                "campaign",
                "--output-dir",
                "dataset",
                "--campaign-run",
                "campaign/campaign_manifest.json",
            ]
        )

        self.assertEqual(plan.training_campaign_command, "plan")
        self.assertEqual(plan.max_cases, 3)
        self.assertIsNone(plan.case_id)
        self.assertEqual(dataset.campaign_run, "campaign/campaign_manifest.json")


if __name__ == "__main__":
    unittest.main()
