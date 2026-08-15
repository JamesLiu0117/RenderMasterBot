import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from render_master_bot.asset_index import AssetSearchHit
from render_master_bot.contracts import (
    AssetCard,
    CorrectionDecision,
    EvaluationReport,
    PatchOperation,
    RenderSpecPatch,
    RenderWorkflowManifest,
    RunManifest,
)
from render_master_bot.correction_planner import (
    CorrectionPlanningError,
    CorrectionPlanningResult,
)
from render_master_bot.models import RenderSpec
from render_master_bot.ollama import StructuredResponse
from render_master_bot.orchestrator import WorkflowError, run_render_workflow
from render_master_bot.patching import apply_render_spec_patch
from render_master_bot.serialization import canonical_sha256
from render_master_bot.visual_evaluator import PreviewEvaluationResult


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def asset_cards() -> list[AssetCard]:
    return [
        AssetCard(
            asset_id="door_asset",
            engine_path="/Game/Props/SM_Door",
            display_name="Studio Door",
            asset_type="static_mesh",
            description="A rectangular door panel for product previews.",
            dimensions_cm={"x": 10, "y": 100, "z": 200},
            material_slots=["Material_0"],
        ),
        AssetCard(
            asset_id="wood_material",
            engine_path="/Game/Materials/M_Wood",
            display_name="Dark Wood",
            asset_type="material",
            description="A dark weathered wood PBR material.",
        ),
    ]


def planned_spec() -> RenderSpec:
    return RenderSpec.model_validate({
        "source_prompt": "Render a front-facing dark wood door product preview.",
        "scene_name": "door_product",
        "objects": [{
            "object_id": "door",
            "asset": {"asset_id": "door_asset"},
            "materials": [{
                "slot_name": "Material_0",
                "material": {"asset_id": "wood_material"},
            }],
        }],
        "camera": {
            "camera_id": "camera",
            "transform": {"location_cm": {"x": -300, "z": 100}},
        },
        "lights": [{
            "light_id": "sun",
            "kind": "directional",
            "intensity": 10_000,
            "intensity_unit": "lux",
        }],
        "render": {"width_px": 640, "height_px": 360},
    })


class FakePlannerClient:
    def chat_structured(self, **_kwargs):
        return StructuredResponse(
            content=planned_spec().model_dump_json(),
            model="gpt-oss:20b",
            total_duration_ns=1_000_000_000,
            prompt_tokens=100,
            output_tokens=200,
        )


class InvalidPlannerClient:
    def chat_structured(self, **_kwargs):
        return StructuredResponse(
            content='{"source_prompt":"cut',
            model="gpt-oss:20b",
            output_tokens=256,
            done_reason="length",
        )


class FakeAssetSearcher:
    def __init__(self, *, unknown: bool = False):
        self.unknown = unknown

    def search(self, _query, *, limit=5, asset_types=None):
        if asset_types == ["material"]:
            return [
                AssetSearchHit(
                    rank=1,
                    asset_id="wood_material",
                    display_name="Dark Wood",
                    asset_type="material",
                    engine_path="/Game/Materials/M_Wood",
                    distance=0.1,
                    similarity=0.9,
                    document="Description: dark weathered wood",
                )
            ][:limit]
        asset_id = "unknown_asset" if self.unknown else "door_asset"
        return [
            AssetSearchHit(
                rank=1,
                asset_id=asset_id,
                display_name="Studio Door",
                asset_type="static_mesh",
                engine_path="/Game/Props/SM_Door",
                distance=0.1,
                similarity=0.9,
                document="Description: rectangular product door; Material slots: Material_0",
            )
        ][:limit]


def prepare_inputs(root: Path) -> tuple[Path, Path]:
    project = root / "Project.uproject"
    project.write_text("{}", encoding="utf-8")
    catalog = root / "asset_cards.json"
    catalog.write_text(
        json.dumps([card.model_dump(mode="json") for card in asset_cards()]),
        encoding="utf-8",
    )
    return project, catalog


class FakePreviewRunner:
    def __init__(self):
        self.specs: list[RenderSpec] = []

    def __call__(
        self,
        _project,
        *,
        render_spec_path,
        run_directory,
        run_id,
        **_kwargs,
    ):
        run_root = Path(run_directory)
        inputs = run_root / "inputs"
        preview = run_root / "preview"
        inputs.mkdir(parents=True)
        preview.mkdir()
        spec = RenderSpec.model_validate_json(Path(render_spec_path).read_text(encoding="utf-8"))
        self.specs.append(spec)
        spec_path = inputs / "render_spec.json"
        spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        preview_path = preview / "beauty.png"
        preview_path.write_bytes(b"fake-preview")
        now = datetime.now(UTC)
        manifest = RunManifest(
            run_id=run_id,
            status="succeeded",
            started_at=now,
            finished_at=now,
            render_spec_sha256=canonical_sha256(spec),
            output_artifacts=[{
                "role": "beauty_preview",
                "path": "preview/beauty.png",
                "sha256": _sha256(preview_path),
            }],
        )
        (run_root / "run_manifest.json").write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return manifest, object()


class FakeEvaluator:
    def __init__(self, verdicts: list[str]):
        self.verdicts = iter(verdicts)

    def __call__(self, _client, *, run_directory, **_kwargs):
        spec = RenderSpec.model_validate_json(
            (Path(run_directory) / "inputs" / "render_spec.json").read_text(encoding="utf-8")
        )
        verdict = next(self.verdicts)
        issues = []
        if verdict == "needs_review":
            issues = [{
                "issue_id": "lighting_too_flat",
                "category": "lighting",
                "severity": "warning",
                "confidence": 0.9,
                "message": "The product lighting is too flat.",
                "evidence_paths": ["preview/beauty.png"],
            }]
        elif verdict == "fail":
            issues = [{
                "issue_id": "material_missing",
                "category": "material",
                "severity": "error",
                "confidence": 0.95,
                "message": "The requested material is not visible.",
                "evidence_paths": ["preview/beauty.png"],
            }]
        report = EvaluationReport(
            render_spec_sha256=canonical_sha256(spec),
            evaluator={"provider": "ollama", "model": "qwen3-vl:8b-instruct"},
            evaluation_stage="preview",
            verdict=verdict,
            summary=f"Controlled evaluator verdict: {verdict}.",
            preview_paths=["preview/beauty.png"],
            issues=issues,
        )
        return PreviewEvaluationResult(
            report=report,
            response=StructuredResponse(
                content="{}",
                model="qwen3-vl:8b-instruct",
                total_duration_ns=2_000_000_000,
            ),
        )


def patch_correction(_client, *, run_directory, **_kwargs) -> CorrectionPlanningResult:
    run_root = Path(run_directory)
    spec = RenderSpec.model_validate_json(
        (run_root / "inputs" / "render_spec.json").read_text(encoding="utf-8")
    )
    report = EvaluationReport.model_validate_json(
        (run_root / "evaluation.json").read_text(encoding="utf-8")
    )
    patch = RenderSpecPatch(
        base_spec_sha256=canonical_sha256(spec),
        rationale="Increase the key light for the next bounded attempt.",
        proposed_by={"provider": "ollama", "model": "gpt-oss:20b"},
        operations=[PatchOperation(op="replace", path="/lights/0/intensity", value=20_000)],
    )
    corrected = apply_render_spec_patch(spec, patch)
    decision = CorrectionDecision(
        render_spec_sha256=canonical_sha256(spec),
        evaluation_report_sha256=canonical_sha256(report),
        planner={"provider": "ollama", "model": "gpt-oss:20b"},
        outcome="patch",
        rationale=patch.rationale,
        patch=patch,
    )
    return CorrectionPlanningResult(
        decision=decision,
        response=StructuredResponse(content="{}", model="gpt-oss:20b"),
        corrected_spec=corrected,
    )


def unresolved_correction(_client, *, run_directory, **_kwargs) -> CorrectionPlanningResult:
    run_root = Path(run_directory)
    spec = RenderSpec.model_validate_json(
        (run_root / "inputs" / "render_spec.json").read_text(encoding="utf-8")
    )
    report = EvaluationReport.model_validate_json(
        (run_root / "evaluation.json").read_text(encoding="utf-8")
    )
    decision = CorrectionDecision(
        render_spec_sha256=canonical_sha256(spec),
        evaluation_report_sha256=canonical_sha256(report),
        planner={"provider": "ollama", "model": "gpt-oss:20b"},
        outcome="unresolved",
        rationale="The bounded patch surface cannot repair the asset.",
        missing_capabilities=["asset_geometry"],
    )
    return CorrectionPlanningResult(
        decision=decision,
        response=StructuredResponse(content="{}", model="gpt-oss:20b"),
    )


class OrchestratorTests(unittest.TestCase):
    def run_workflow(self, root: Path, evaluator, **overrides):
        project, catalog = prepare_inputs(root)
        preview_runner = overrides.pop("preview_runner", FakePreviewRunner())
        values = {
            "planner_client": FakePlannerClient(),
            "vision_client": object(),
            "correction_client": object(),
            "asset_searcher": FakeAssetSearcher(),
            "planner_model": "gpt-oss:20b",
            "vision_model": "qwen3-vl:8b-instruct",
            "prompt": "Render a front-facing dark wood door product preview.",
            "uproject_path": project,
            "engine_root": root / "UE",
            "asset_catalog_path": catalog,
            "workflow_directory": root / "workflow",
            "workflow_id": "door_workflow",
            "retrieve_assets": 1,
            "retrieve_materials": 1,
            "max_iterations": 2,
            "preview_runner": preview_runner,
            "preview_evaluator": evaluator,
        }
        values.update(overrides)
        return run_render_workflow(**values), preview_runner

    def test_first_passing_preview_completes_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, preview_runner = self.run_workflow(root, FakeEvaluator(["pass"]))

            saved = RenderWorkflowManifest.model_validate_json(
                (root / "workflow" / "workflow_manifest.json").read_text(encoding="utf-8")
            )
            retrieved_catalog = json.loads(
                (root / "workflow" / "planning" / "retrieved_asset_cards.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(result.manifest.status, "succeeded")
        self.assertEqual(result.manifest.stop_reason, "evaluator_passed")
        self.assertEqual(len(result.manifest.iterations), 1)
        self.assertEqual(saved, result.manifest)
        self.assertEqual(len(preview_runner.specs), 1)
        self.assertIn("final_render_spec", {item.role for item in saved.output_artifacts})
        self.assertEqual(
            {item["asset_id"] for item in retrieved_catalog},
            {"door_asset", "wood_material"},
        )
        self.assertIn(
            "retrieved_asset_catalog",
            {item.role for item in saved.output_artifacts},
        )

    def test_patch_rerenders_once_and_then_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, preview_runner = self.run_workflow(
                root,
                FakeEvaluator(["needs_review", "pass"]),
                correction_planner=patch_correction,
            )

        self.assertEqual(result.manifest.status, "succeeded")
        self.assertEqual(len(result.manifest.iterations), 2)
        self.assertEqual(result.manifest.iterations[0].correction_outcome, "patch")
        self.assertEqual(preview_runner.specs[0].lights[0].intensity, 10_000)
        self.assertEqual(preview_runner.specs[1].lights[0].intensity, 20_000)

    def test_unresolved_correction_stops_without_another_render(self):
        with tempfile.TemporaryDirectory() as directory:
            result, preview_runner = self.run_workflow(
                Path(directory),
                FakeEvaluator(["fail"]),
                correction_planner=unresolved_correction,
            )

        self.assertEqual(result.manifest.status, "stopped")
        self.assertEqual(result.manifest.stop_reason, "correction_unresolved")
        self.assertEqual(len(preview_runner.specs), 1)

    def test_iteration_limit_stops_before_requesting_an_unused_patch(self):
        def forbidden_correction(*_args, **_kwargs):
            raise AssertionError("correction must not run after the final allowed preview")

        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.run_workflow(
                Path(directory),
                FakeEvaluator(["needs_review"]),
                max_iterations=1,
                correction_planner=forbidden_correction,
            )

        self.assertEqual(result.manifest.status, "stopped")
        self.assertEqual(result.manifest.stop_reason, "max_iterations_reached")

    def test_retrieval_outside_catalog_fails_with_a_terminal_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(WorkflowError, "outside the supplied asset catalog"):
                self.run_workflow(
                    root,
                    FakeEvaluator(["pass"]),
                    asset_searcher=FakeAssetSearcher(unknown=True),
                )
            saved = RenderWorkflowManifest.model_validate_json(
                (root / "workflow" / "workflow_manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(saved.status, "failed")
        self.assertEqual(saved.stop_reason, "stage_failed")
        self.assertTrue(saved.errors)

    def test_invalid_correction_response_is_preserved_before_failure(self):
        def invalid_correction(*_args, **_kwargs):
            raise CorrectionPlanningError(
                "invalid decision after retry",
                response=StructuredResponse(
                    content='{"outcome":"patch"',
                    model="gpt-oss:20b",
                    output_tokens=512,
                    done_reason="length",
                ),
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(WorkflowError, "invalid decision after retry"):
                self.run_workflow(
                    root,
                    FakeEvaluator(["fail"]),
                    correction_planner=invalid_correction,
                )
            workflow_root = root / "workflow"
            iteration_root = workflow_root / "iterations" / "iteration-001"
            metrics = json.loads(
                (iteration_root / "correction_invalid_metrics.json").read_text(
                    encoding="utf-8"
                )
            )
            saved = RenderWorkflowManifest.model_validate_json(
                (workflow_root / "workflow_manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(metrics["done_reason"], "length")
        self.assertEqual(metrics["output_tokens"], 512)
        self.assertEqual(saved.status, "failed")
        roles = {item.role for item in saved.output_artifacts}
        self.assertIn("iteration_001_correction_invalid_raw", roles)
        self.assertIn("iteration_001_correction_invalid_metrics", roles)

    def test_invalid_planner_response_is_preserved_after_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(WorkflowError, "after one format retry"):
                self.run_workflow(
                    root,
                    FakeEvaluator(["pass"]),
                    planner_client=InvalidPlannerClient(),
                )
            workflow_root = root / "workflow"
            metrics = json.loads(
                (workflow_root / "planning" / "planner_invalid_metrics.json").read_text(
                    encoding="utf-8"
                )
            )
            saved = RenderWorkflowManifest.model_validate_json(
                (workflow_root / "workflow_manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(metrics["attempt_count"], 2)
        self.assertEqual(metrics["done_reason"], "length")
        self.assertEqual(saved.status, "failed")
        roles = {item.role for item in saved.output_artifacts}
        self.assertIn("planner_invalid_raw", roles)
        self.assertIn("planner_invalid_metrics", roles)


if __name__ == "__main__":
    unittest.main()
