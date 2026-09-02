import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from render_master_bot.cli import build_parser
from render_master_bot.contracts import (
    AssetCard,
    EvaluationReport,
    ImageStatistics,
    RunManifest,
    TrainingCampaignSuite,
    TrainingReferenceSuite,
)
from render_master_bot.models import RenderSpec
from render_master_bot.ollama import StructuredResponse
from render_master_bot.serialization import canonical_sha256
from render_master_bot.training_data import build_training_dataset
from render_master_bot.training_reference import (
    REFERENCE_SNAPSHOT_FILENAME,
    TrainingReferenceWorkflowResult,
    compile_single_asset_reference_suite,
    create_training_reference_campaign_plan,
    run_training_reference_workflow,
    validate_training_reference_suite,
)
from render_master_bot.visual_evaluator import PreviewEvaluationResult


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class _FakePreviewRunner:
    def __call__(
        self,
        _project,
        *,
        render_spec_path,
        run_directory,
        run_id,
        **_kwargs,
    ):
        root = Path(run_directory)
        inputs = root / "inputs"
        preview = root / "preview"
        inputs.mkdir(parents=True)
        preview.mkdir()
        spec = RenderSpec.model_validate_json(
            Path(render_spec_path).read_text(encoding="utf-8")
        )
        spec_path = inputs / "render_spec.json"
        _write_json(spec_path, spec.model_dump(mode="json"))
        preview_path = preview / "beauty.png"
        preview_path.write_bytes(b"synthetic-preview")
        timestamp = datetime.now(UTC)
        manifest = RunManifest(
            run_id=run_id,
            status="succeeded",
            started_at=timestamp,
            finished_at=timestamp,
            render_spec_sha256=canonical_sha256(spec),
            output_artifacts=[
                {
                    "role": "beauty_preview",
                    "path": "preview/beauty.png",
                    "sha256": _sha256_file(preview_path),
                }
            ],
        )
        _write_json(root / "run_manifest.json", manifest.model_dump(mode="json"))
        return manifest, object()


class _FakeEvaluator:
    def __init__(self, *, clipped_pixel_fraction: float = 0.01):
        self.clipped_pixel_fraction = clipped_pixel_fraction

    def __call__(self, _client, *, run_directory, model, **_kwargs):
        spec = RenderSpec.model_validate_json(
            (Path(run_directory) / "inputs" / "render_spec.json").read_text(
                encoding="utf-8"
            )
        )
        report = EvaluationReport(
            render_spec_sha256=canonical_sha256(spec),
            evaluator={"provider": "ollama", "model": model},
            evaluation_stage="preview",
            verdict="pass",
            summary="Synthetic reference preview passed.",
            preview_paths=["preview/beauty.png"],
        )
        return PreviewEvaluationResult(
            report=report,
            response=StructuredResponse(content="{}", model=model),
            statistics=ImageStatistics(
                sha256="a" * 64,
                width_px=1280,
                height_px=720,
                sampled_pixels=100_000,
                mean_luminance=0.25,
                luminance_stddev=0.15,
                p05_luminance=0.01,
                p95_luminance=0.75,
                dark_pixel_fraction=0.4,
                clipped_pixel_fraction=self.clipped_pixel_fraction,
                foreground_fraction=0.3,
                center_luminance=0.35,
                border_luminance=0.01,
                blank_like=False,
                underexposed_like=False,
                overexposed_like=False,
            ),
        )


class TrainingReferenceTests(unittest.TestCase):
    def _inputs(self, root: Path, *, bad_slot: bool = False):
        prompt = "Create a clean studio preview of one door."
        project = root / "OptimizationPlugin.uproject"
        project.write_text('{"FileVersion":3}\n', encoding="utf-8")
        card = AssetCard(
            asset_id="door_asset",
            engine_path="/Game/Test/SM_Door",
            display_name="SM_Door",
            asset_type="static_mesh",
            description="A synthetic product door.",
            dimensions_cm={"x": 10, "y": 100, "z": 200},
            material_slots=["Material_0"],
        )
        catalog = root / "asset_cards.json"
        _write_json(catalog, [card.model_dump(mode="json")])
        campaign = TrainingCampaignSuite(
            suite_id="optimization_training_v1",
            description="Synthetic Golden Reference suite",
            project_name="OptimizationPlugin",
            project_descriptor_sha256=_sha256_file(project),
            asset_catalog_sha256=_sha256_file(catalog),
            cases=[
                {
                    "case_id": "single_door",
                    "category": "single_asset",
                    "prompt": prompt,
                    "expectation": {
                        "required_scene_asset_ids": ["door_asset"],
                        "minimum_object_count": 1,
                        "maximum_object_count": 1,
                    },
                }
            ],
        )
        campaign_path = root / "campaign_suite.json"
        _write_json(campaign_path, campaign.model_dump(mode="json"))
        materials = (
            [
                {
                    "slot_name": "MissingSlot" if bad_slot else "Material_0",
                    "material": {"asset_id": "door_asset"},
                }
            ]
            if bad_slot
            else []
        )
        spec = RenderSpec.model_validate(
            {
                "source_prompt": prompt,
                "scene_name": "door_reference",
                "objects": [
                    {
                        "object_id": "door",
                        "asset": {"asset_id": "door_asset"},
                        "materials": materials,
                    }
                ],
                "camera": {
                    "camera_id": "main_camera",
                    "transform": {"location_cm": {"x": -300, "z": 100}},
                },
                "lights": [
                    {
                        "light_id": "key",
                        "kind": "directional",
                        "intensity": 20_000,
                        "intensity_unit": "lux",
                    }
                ],
                "render": {"width_px": 640, "height_px": 360},
                "notes": [],
            }
        )
        reference = TrainingReferenceSuite(
            reference_suite_id="optimization_references_v1",
            campaign_suite_id=campaign.suite_id,
            campaign_suite_sha256=canonical_sha256(campaign),
            cases=[
                {
                    "case_id": "single_door",
                    "authoring_source": "deterministic_compiler",
                    "rationale": "Known asset, bounded camera, and neutral key light.",
                    "retrieved_asset_ids": ["door_asset"],
                    "render_spec": spec.model_dump(mode="json"),
                    "render_spec_sha256": canonical_sha256(spec),
                }
            ],
        )
        reference_path = root / "reference_suite.json"
        _write_json(reference_path, reference.model_dump(mode="json"))
        return project, catalog, campaign_path, reference_path, campaign, reference

    def test_static_validation_accepts_a_hash_bound_non_model_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, catalog, suite, references, _, _ = self._inputs(root)

            report = validate_training_reference_suite(
                reference_suite_path=references,
                campaign_suite_path=suite,
                project_path=project,
                asset_catalog_path=catalog,
            )

            self.assertEqual(report.integrity_status, "passed")
            self.assertTrue(report.ready_to_run)
            self.assertTrue(report.full_coverage)
            self.assertEqual(report.ready_case_count, 1)

    def test_static_validation_rejects_wrong_asset_type_and_material_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, catalog, suite, references, _, _ = self._inputs(
                root,
                bad_slot=True,
            )

            report = validate_training_reference_suite(
                reference_suite_path=references,
                campaign_suite_path=suite,
                project_path=project,
                asset_catalog_path=catalog,
            )

            self.assertEqual(report.integrity_status, "failed")
            codes = report.case_results[0].issue_codes
            self.assertIn("material_asset_type_door", codes)
            self.assertIn("material_slot_door", codes)
            self.assertIn("material_assignment_count", codes)

    def test_plan_binds_reference_hash_without_invoking_a_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, catalog, suite, references, _, reference = self._inputs(root)

            manifest_path, run, report = create_training_reference_campaign_plan(
                reference_suite_path=references,
                campaign_suite_path=suite,
                campaign_directory=root / "campaign",
                campaign_id="reference_campaign_v1",
                project_path=project,
                asset_catalog_path=catalog,
                evaluator_model="qwen3-vl:8b-instruct",
            )

            self.assertEqual(run.status, "planned")
            self.assertEqual(run.planner.provider, "local")
            self.assertEqual(run.reference_suite_sha256, canonical_sha256(reference))
            self.assertEqual(run.reference_suite_sha256, report.reference_suite_sha256)
            self.assertTrue(manifest_path.is_file())
            self.assertTrue((manifest_path.parent / REFERENCE_SNAPSHOT_FILENAME).is_file())

    def test_single_asset_compiler_is_deterministic_and_statically_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, catalog, suite, _, _, _ = self._inputs(root)
            first_path = root / "compiled_first.json"
            second_path = root / "compiled_second.json"

            first = compile_single_asset_reference_suite(
                campaign_suite_path=suite,
                project_path=project,
                asset_catalog_path=catalog,
                output_path=first_path,
                reference_suite_id="compiled_references_v1",
            )
            second = compile_single_asset_reference_suite(
                campaign_suite_path=suite,
                project_path=project,
                asset_catalog_path=catalog,
                output_path=second_path,
                reference_suite_id="compiled_references_v1",
            )
            report = validate_training_reference_suite(
                reference_suite_path=first_path,
                campaign_suite_path=suite,
                project_path=project,
                asset_catalog_path=catalog,
            )

            self.assertEqual(canonical_sha256(first), canonical_sha256(second))
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
            self.assertEqual(report.integrity_status, "passed")
            camera = first.cases[0].render_spec.camera
            self.assertNotEqual(camera.transform.location_cm.x, 0)
            self.assertNotEqual(camera.transform.location_cm.y, 0)
            self.assertGreater(camera.transform.location_cm.z, 0)
            self.assertEqual(camera.exposure.fixed_ev100, 11)
            self.assertEqual(len(first.cases[0].render_spec.lights), 2)

    def test_reference_workflow_is_dataset_compatible_and_preserves_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, catalog, _, _, campaign, references = self._inputs(root)
            workflow_root = root / "evidence" / "single_door"

            result: TrainingReferenceWorkflowResult = run_training_reference_workflow(
                reference=references.cases[0],
                campaign_case=campaign.cases[0],
                vision_client=object(),
                vision_model="qwen3-vl:8b-instruct",
                uproject_path=project,
                engine_root=root / "Engine",
                asset_catalog_path=catalog,
                workflow_directory=workflow_root,
                workflow_id="golden_single_door",
                preview_runner=_FakePreviewRunner(),
                preview_evaluator=_FakeEvaluator(),
            )
            dataset = build_training_dataset(
                source_root=root / "evidence",
                output_directory=root / "dataset",
            )

            self.assertEqual(result.manifest.status, "succeeded")
            self.assertEqual(result.manifest.planner.provider, "local")
            self.assertEqual(dataset.manifest.total_example_count, 1)
            example = json.loads(
                (root / "dataset" / "splits" / "train.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            roles = {item["role"] for item in example["source_artifacts"]}
            self.assertIn("golden_reference_case", roles)

    def test_reference_workflow_rejects_excessive_clipping_after_visual_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, catalog, _, _, campaign, references = self._inputs(root)

            result = run_training_reference_workflow(
                reference=references.cases[0],
                campaign_case=campaign.cases[0],
                vision_client=object(),
                vision_model="qwen3-vl:8b-instruct",
                uproject_path=project,
                engine_root=root / "Engine",
                asset_catalog_path=catalog,
                workflow_directory=root / "evidence" / "single_door",
                workflow_id="clipped_single_door",
                preview_runner=_FakePreviewRunner(),
                preview_evaluator=_FakeEvaluator(clipped_pixel_fraction=0.1),
            )

            self.assertEqual(result.manifest.status, "stopped")
            self.assertEqual(result.manifest.stop_reason, "reference_quality_rejected")
            self.assertNotIn(
                "final_render_spec",
                {artifact.role for artifact in result.manifest.output_artifacts},
            )

    def test_cli_exposes_reference_validate_plan_and_run(self):
        parser = build_parser()
        validate = parser.parse_args(
            [
                "training-reference",
                "validate",
                "--references",
                "references.json",
                "--suite",
                "suite.json",
                "--path",
                "OptimizationPlugin.uproject",
                "--assets",
                "assets.json",
            ]
        )
        compile_references = parser.parse_args(
            [
                "training-reference",
                "compile-single-assets",
                "--suite",
                "suite.json",
                "--path",
                "OptimizationPlugin.uproject",
                "--assets",
                "assets.json",
                "--reference-suite-id",
                "references_v1",
                "--output",
                "references.json",
            ]
        )
        plan = parser.parse_args(
            [
                "training-reference",
                "plan",
                "--references",
                "references.json",
                "--suite",
                "suite.json",
                "--campaign-dir",
                "campaign",
                "--campaign-id",
                "references_v1",
                "--path",
                "OptimizationPlugin.uproject",
                "--assets",
                "assets.json",
            ]
        )
        run = parser.parse_args(
            [
                "training-reference",
                "run",
                "--references",
                "references.json",
                "--suite",
                "suite.json",
                "--campaign-dir",
                "campaign",
                "--path",
                "OptimizationPlugin.uproject",
                "--engine-root",
                "UE_5.7",
                "--assets",
                "assets.json",
            ]
        )

        self.assertEqual(validate.training_reference_command, "validate")
        self.assertEqual(
            compile_references.training_reference_command,
            "compile-single-assets",
        )
        self.assertEqual(plan.training_reference_command, "plan")
        self.assertEqual(run.training_reference_command, "run")


if __name__ == "__main__":
    unittest.main()
