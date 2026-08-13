import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from render_master_bot.contracts import (
    AssetCard,
    CapabilityManifest,
    EvaluationReport,
    PatchOperation,
    RunManifest,
    TechniqueCard,
)
from render_master_bot.serialization import canonical_sha256


HASH_A = "a" * 64
HASH_B = "b" * 64


class SharedContractTests(unittest.TestCase):
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
