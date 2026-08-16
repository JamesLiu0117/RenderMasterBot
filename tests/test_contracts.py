import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from render_master_bot.contracts import (
    AssistantMaterialProposal,
    AssetCard,
    CapabilityManifest,
    CorrectionDecision,
    EvaluationReport,
    PatchOperation,
    RunManifest,
    TechniqueCard,
    UnrealSelectionContext,
    VisualBenchmarkSuite,
)
from render_master_bot.serialization import canonical_sha256


HASH_A = "a" * 64
HASH_B = "b" * 64


class SharedContractTests(unittest.TestCase):
    def test_selection_context_target_slot_must_be_observed(self):
        with self.assertRaisesRegex(ValidationError, "target material slot is not present"):
            UnrealSelectionContext.model_validate({
                "project_name": "OptimizationPlugin",
                "level_path": "/Game/Test",
                "actor_name": "Door",
                "actor_path": "/Game/Test.Door",
                "component_name": "Mesh",
                "mesh_path": "/Game/SM_Door",
                "material_slots": [{"slot_index": 0, "slot_name": "DoorSurface"}],
                "target_slot_index": 3,
            })

    def test_material_proposal_cannot_target_an_unobserved_slot(self):
        with self.assertRaisesRegex(ValidationError, "not present in the target context"):
            AssistantMaterialProposal.model_validate({
                "proposal_id": "material_001",
                "status": "proposed",
                "request": "Use weathered wood",
                "target": {
                    "project_name": "OptimizationPlugin",
                    "level_path": "/Game/Test",
                    "actor_name": "Door",
                    "actor_path": "/Game/Test.Door",
                    "component_name": "Mesh",
                    "mesh_path": "/Game/SM_Door",
                    "material_slots": [{
                        "slot_index": 0,
                        "slot_name": "DoorSurface",
                    }],
                },
                "proposed_by": {"provider": "local", "model": "embedding-test"},
                "selected_slot": {"slot_index": 1, "slot_name": "WrongSlot"},
                "selected_material": {
                    "rank": 1,
                    "asset_id": "weathered_wood",
                    "display_name": "Weathered Wood",
                    "engine_path": "/Game/M_Weathered",
                    "similarity": 0.9,
                },
                "rationale": "The material matches the request.",
            })

    def test_technique_card_requires_a_traceable_source(self):
        with self.assertRaises(ValidationError):
            TechniqueCard(
                technique_id="three_point_lighting",
                name="Three-point lighting",
                summary="A controllable key, fill, and rim-light arrangement.",
                problem_types=["studio lighting"],
                sources=[],
            )

    def test_asset_card_records_engine_identity_and_scale(self):
        card = AssetCard(
            asset_id="studio_chair",
            engine_path="/Game/Props/SM_StudioChair",
            display_name="Studio Chair",
            asset_type="static_mesh",
            dimensions_cm={"x": 62, "y": 70, "z": 94},
        )
        self.assertEqual(card.engine, "unreal")
        self.assertEqual(card.dimensions_cm.z, 94)

    def test_patch_cannot_modify_contract_identity(self):
        with self.assertRaises(ValidationError):
            PatchOperation(op="replace", path="/schema_version", value="9.9")

    def test_remove_patch_must_omit_value(self):
        with self.assertRaisesRegex(ValidationError, "must omit value"):
            PatchOperation(op="remove", path="/lights/0", value=None)

    def test_unresolved_correction_requires_a_capability_gap(self):
        with self.assertRaisesRegex(ValidationError, "missing_capabilities"):
            CorrectionDecision(
                render_spec_sha256=HASH_A,
                evaluation_report_sha256=HASH_B,
                planner={"provider": "local", "model": "repair"},
                outcome="unresolved",
                rationale="The contract cannot express the repair.",
            )

    def test_pass_evaluation_cannot_hide_a_blocking_issue(self):
        with self.assertRaisesRegex(ValidationError, "pass verdict"):
            EvaluationReport(
                render_spec_sha256=HASH_A,
                evaluator={"provider": "ollama", "model": "qwen3.5:9b"},
                verdict="pass",
                summary="The preview contains a blocking defect.",
                preview_paths=["preview/beauty.png"],
                issues=[{
                    "issue_id": "missing_subject",
                    "category": "composition",
                    "severity": "blocking",
                    "confidence": 0.98,
                    "message": "The requested subject is not visible.",
                }],
            )

    def test_suggested_patch_must_target_evaluated_spec(self):
        with self.assertRaisesRegex(ValidationError, "must target"):
            EvaluationReport(
                render_spec_sha256=HASH_A,
                evaluator={"provider": "ollama", "model": "qwen3.5:9b"},
                verdict="needs_review",
                summary="The light can be improved.",
                preview_paths=["preview/beauty.png"],
                suggested_patch={
                    "base_spec_sha256": HASH_B,
                    "rationale": "Increase key-light intensity.",
                    "proposed_by": {"provider": "ollama", "model": "qwen3.5:9b"},
                    "operations": [{
                        "op": "replace",
                        "path": "/lights/0/intensity",
                        "value": 1500,
                    }],
                },
            )

    def test_preflight_evaluation_does_not_require_an_image(self):
        report = EvaluationReport(
            render_spec_sha256=HASH_A,
            evaluator={"provider": "local", "model": "semantic_preflight_v1"},
            evaluation_stage="preflight",
            verdict="pass",
            summary="No semantic rule violations were detected.",
        )
        self.assertEqual(report.preview_paths, [])

    def test_visual_benchmark_rejects_parent_run_paths(self):
        with self.assertRaisesRegex(ValidationError, "parent segments"):
            VisualBenchmarkSuite.model_validate({
                "suite_id": "unsafe_suite",
                "description": "A suite whose run escapes its portable data root.",
                "cases": [{
                    "case_id": "unsafe_case",
                    "description": "This path must not be accepted.",
                    "run_directory": "../runs/preview-001",
                    "expectation": {"accepted_verdicts": ["fail"]},
                }],
            })

    def test_visual_benchmark_metric_range_must_be_ordered(self):
        with self.assertRaisesRegex(ValidationError, "minimum cannot exceed"):
            VisualBenchmarkSuite.model_validate({
                "suite_id": "bad_thresholds",
                "description": "A suite with an impossible deterministic metric range.",
                "cases": [{
                    "case_id": "bad_case",
                    "description": "The range is intentionally reversed.",
                    "run_directory": "runs/preview-001",
                    "expectation": {
                        "accepted_verdicts": ["fail"],
                        "image_metrics": [{
                            "metric": "mean_luminance",
                            "minimum": 0.8,
                            "maximum": 0.2,
                        }],
                    },
                }],
            })

    def test_terminal_run_requires_finished_at(self):
        with self.assertRaisesRegex(ValidationError, "require finished_at"):
            RunManifest(
                run_id="run_001",
                status="succeeded",
                started_at=datetime.now(UTC),
            )

    def test_run_cannot_finish_before_it_starts(self):
        started = datetime.now(UTC)
        with self.assertRaisesRegex(ValidationError, "earlier"):
            RunManifest(
                run_id="run_001",
                status="cancelled",
                started_at=started,
                finished_at=started - timedelta(seconds=1),
            )

    def test_capability_manifest_captures_unreal_features(self):
        manifest = CapabilityManifest(
            engine="unreal",
            engine_version="5.6.1",
            project_name="RenderLab",
            coordinate_system="unreal_z_up_cm",
            python_available=True,
            movie_render_queue_available=True,
            captured_at=datetime.now(UTC),
        )
        self.assertTrue(manifest.movie_render_queue_available)

    def test_canonical_hash_ignores_dictionary_key_order(self):
        self.assertEqual(
            canonical_sha256({"camera": {"focal_length": 50}, "seed": 7}),
            canonical_sha256({"seed": 7, "camera": {"focal_length": 50}}),
        )


if __name__ == "__main__":
    unittest.main()
