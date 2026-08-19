import json
import hashlib
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from render_master_bot.cli import (
    _configure_utf8_console,
    _read_run_prompt,
    _write_json,
    build_parser,
    cmd_assistant_actor_gpu_impact_review,
    cmd_assistant_insights_gpu_review,
)


class CliOutputTests(unittest.TestCase):
    def test_run_prompt_file_preserves_utf8_and_trims_outer_whitespace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prompt.txt"
            path.write_text("  创建一个有风化木材质的门。\n", encoding="utf-8")

            self.assertEqual(
                _read_run_prompt(None, str(path)),
                "创建一个有风化木材质的门。",
            )

    def test_run_prompt_file_rejects_empty_or_oversized_input(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty.txt"
            empty.write_text(" \n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot be empty"):
                _read_run_prompt(None, str(empty))

            large = Path(directory) / "large.txt"
            large.write_bytes(b"x" * (64 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, "64 KiB"):
                _read_run_prompt(None, str(large))

    def test_run_prompt_file_reports_invalid_utf8_as_input_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.txt"
            path.write_bytes(b"\xff\xfe\xfa")

            with self.assertRaisesRegex(ValueError, "could not read prompt file"):
                _read_run_prompt(None, str(path))

    def test_json_output_creates_missing_parent_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "run" / "report.json"
            _write_json({"status": "ok"}, str(output))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"status": "ok"})

    def test_unreal_scan_parser_captures_deterministic_inputs(self):
        args = build_parser().parse_args(
            [
                "unreal-scan-assets",
                "E:/Project/Project.uproject",
                "--engine-root",
                "E:/Unreal Engine/UE_5.7",
                "--raw-output",
                "raw.json",
                "--output",
                "cards.json",
                "--limit",
                "12",
            ]
        )

        self.assertEqual(args.limit, 12)
        self.assertEqual(args.path_prefix, "/Game")
        self.assertEqual(args.raw_output, "raw.json")

    def test_unreal_build_parser_captures_validated_inputs(self):
        args = build_parser().parse_args(
            [
                "unreal-build-scene",
                "E:/Project/Project.uproject",
                "--engine-root",
                "E:/Unreal Engine/UE_5.7",
                "--spec",
                "scene.json",
                "--assets",
                "asset_cards.json",
                "--output",
                "scene_build.json",
                "--fail-on-warning",
            ]
        )

        self.assertEqual(args.spec, "scene.json")
        self.assertEqual(args.assets, "asset_cards.json")
        self.assertEqual(args.output, "scene_build.json")
        self.assertTrue(args.fail_on_warning)

    def test_unreal_material_parser_requires_all_four_pbr_maps(self):
        args = build_parser().parse_args(
            [
                "unreal-import-pbr-material",
                "E:/Project/Project.uproject",
                "--engine-root",
                "E:/Unreal Engine/UE_5.7",
                "--destination-path",
                "/Game/RenderMasterBot/TestMaterials/Wood",
                "--material-name",
                "M_Wood",
                "--base-color",
                "base.jpg",
                "--normal",
                "normal.jpg",
                "--roughness",
                "roughness.jpg",
                "--ambient-occlusion",
                "ao.jpg",
                "--output",
                "material_import.json",
            ]
        )

        self.assertEqual(args.material_name, "M_Wood")
        self.assertEqual(args.ambient_occlusion, "ao.jpg")
        self.assertEqual(args.timeout, 300)

    def test_unreal_preview_parser_requires_run_identity_and_directory(self):
        args = build_parser().parse_args(
            [
                "unreal-render-preview",
                "E:/Project/Project.uproject",
                "--engine-root",
                "E:/Unreal Engine/UE_5.7",
                "--spec",
                "scene.json",
                "--assets",
                "asset_cards.json",
                "--run-dir",
                "preview-001",
                "--run-id",
                "preview_001",
            ]
        )

        self.assertEqual(args.run_dir, "preview-001")
        self.assertEqual(args.run_id, "preview_001")
        self.assertEqual(args.timeout, 600)

    def test_frame_camera_parser_captures_auditable_outputs(self):
        args = build_parser().parse_args(
            [
                "frame-camera",
                "source.json",
                "--assets",
                "assets.json",
                "--output",
                "framed.json",
                "--patch-output",
                "framing_patch.json",
                "--view-axis",
                "from-negative-x",
            ]
        )

        self.assertEqual(args.output, "framed.json")
        self.assertEqual(args.patch_output, "framing_patch.json")
        self.assertEqual(args.margin, 0.1)
        self.assertEqual(args.view_axis, "from-negative-x")

        top_down = build_parser().parse_args([
            "frame-camera",
            "source.json",
            "--assets",
            "assets.json",
            "--output",
            "framed.json",
            "--patch-output",
            "framing_patch.json",
            "--view-axis",
            "from-positive-z",
        ])
        self.assertEqual(top_down.view_axis, "from-positive-z")

    def test_apply_patch_parser_keeps_source_patch_and_output_distinct(self):
        args = build_parser().parse_args([
            "apply-patch",
            "source.json",
            "--patch",
            "exposure_patch.json",
            "--output",
            "corrected.json",
        ])

        self.assertEqual(args.path, "source.json")
        self.assertEqual(args.patch, "exposure_patch.json")
        self.assertEqual(args.output, "corrected.json")

    def test_evaluate_preview_parser_uses_run_relative_outputs(self):
        args = build_parser().parse_args(["evaluate-preview", "preview-005"])

        self.assertEqual(args.run_dir, "preview-005")
        self.assertEqual(args.output, "evaluation.json")
        self.assertEqual(args.metrics_output, "evaluation_metrics.json")

    def test_benchmark_evaluator_parser_requires_a_report_output(self):
        args = build_parser().parse_args([
            "benchmark-evaluator",
            "visual_suite.json",
            "--output",
            "benchmark_report.json",
        ])

        self.assertEqual(args.path, "visual_suite.json")
        self.assertEqual(args.output, "benchmark_report.json")
        self.assertIsNone(args.model)

    def test_plan_correction_parser_supports_patch_or_unresolved_output(self):
        args = build_parser().parse_args(["plan-correction", "preview-005"])

        self.assertEqual(args.evaluation, "evaluation.json")
        self.assertEqual(args.output, "correction.json")
        self.assertEqual(args.corrected_output, "corrected_render_spec.json")

    def test_plan_parser_enables_retrieval_constraints(self):
        args = build_parser().parse_args(
            ["plan", "--prompt", "add a door", "--retrieve-assets", "4"]
        )

        self.assertEqual(args.retrieve_assets, 4)
        self.assertEqual(args.retrieve_materials, 0)

        material_args = build_parser().parse_args(
            ["plan", "--prompt", "add a wood door", "--retrieve-materials", "3"]
        )
        self.assertEqual(material_args.retrieve_materials, 3)

    def test_run_parser_captures_bounded_workflow_controls(self):
        args = build_parser().parse_args([
            "run",
            "E:/Project/Project.uproject",
            "--engine-root",
            "E:/Unreal Engine/UE_5.7",
            "--prompt",
            "Render a dark wood door",
            "--assets",
            "asset_cards.json",
            "--workflow-dir",
            "workflow-001",
            "--workflow-id",
            "workflow_001",
            "--max-iterations",
            "3",
            "--view-axis",
            "from-negative-x",
        ])

        self.assertEqual(args.max_iterations, 3)
        self.assertEqual(args.retrieve_assets, 8)
        self.assertEqual(args.retrieve_materials, 5)
        self.assertEqual(args.view_axis, "from-negative-x")

        defaults = build_parser().parse_args([
            "run",
            "E:/Project/Project.uproject",
            "--engine-root",
            "E:/Unreal Engine/UE_5.7",
            "--prompt",
            "Render a product",
            "--assets",
            "asset_cards.json",
            "--workflow-dir",
            "workflow-defaults",
            "--workflow-id",
            "workflow_defaults",
        ])
        self.assertEqual(defaults.view_axis, "auto-product")
        self.assertEqual(defaults.margin, 0.02)
        self.assertFalse(defaults.no_studio_calibration)

        prompt_file = build_parser().parse_args([
            "run",
            "E:/Project/Project.uproject",
            "--engine-root",
            "E:/Unreal Engine/UE_5.7",
            "--prompt-file",
            "request.txt",
            "--assets",
            "asset_cards.json",
            "--workflow-dir",
            "workflow-panel",
            "--workflow-id",
            "workflow_panel",
        ])
        self.assertIsNone(prompt_file.prompt)
        self.assertEqual(prompt_file.prompt_file, "request.txt")

    def test_asset_search_parser_captures_query_and_limit(self):
        args = build_parser().parse_args(
            [
                "asset-search",
                "--query",
                "木门材质",
                "--limit",
                "3",
                "--asset-type",
                "material",
            ]
        )

        self.assertEqual(args.query, "木门材质")
        self.assertEqual(args.limit, 3)
        self.assertEqual(args.asset_type, ["material"])

    def test_assistant_material_parser_requires_safe_context_and_output(self):
        args = build_parser().parse_args(
            [
                "assistant-material-propose",
                "--prompt-file",
                "request.txt",
                "--context",
                "selection.json",
                "--assets",
                "asset_cards.json",
                "--proposal-id",
                "material_001",
                "--output",
                "proposal.json",
            ]
        )

        self.assertIsNone(args.prompt)
        self.assertEqual(args.prompt_file, "request.txt")
        self.assertEqual(args.context, "selection.json")
        self.assertEqual(args.proposal_id, "material_001")
        self.assertEqual(args.limit, 5)

    def test_assistant_transform_parser_keeps_target_context_and_output_explicit(self):
        args = build_parser().parse_args(
            [
                "assistant-transform-propose",
                "--prompt",
                "Move this object up by 50 cm",
                "--context",
                "actor_transform.json",
                "--model",
                "planner-model",
                "--proposal-id",
                "transform_001",
                "--output",
                "proposal.json",
            ]
        )

        self.assertEqual(args.prompt, "Move this object up by 50 cm")
        self.assertIsNone(args.prompt_file)
        self.assertEqual(args.context, "actor_transform.json")
        self.assertEqual(args.model, "planner-model")
        self.assertEqual(args.proposal_id, "transform_001")
        self.assertEqual(args.output, "proposal.json")

    def test_assistant_transform_batch_parser_keeps_selection_and_output_explicit(self):
        args = build_parser().parse_args(
            [
                "assistant-transform-batch-propose",
                "--prompt-file",
                "request.txt",
                "--context",
                "transform_selection.json",
                "--model",
                "planner-model",
                "--proposal-id",
                "transform_batch_001",
                "--output",
                "batch_proposal.json",
            ]
        )

        self.assertIsNone(args.prompt)
        self.assertEqual(args.prompt_file, "request.txt")
        self.assertEqual(args.context, "transform_selection.json")
        self.assertEqual(args.model, "planner-model")
        self.assertEqual(args.proposal_id, "transform_batch_001")
        self.assertEqual(args.output, "batch_proposal.json")

    def test_assistant_light_parser_keeps_context_model_and_output_explicit(self):
        args = build_parser().parse_args(
            [
                "assistant-light-propose",
                "--prompt",
                "Make this light 20% brighter and 3200K",
                "--context",
                "light_context.json",
                "--model",
                "planner-model",
                "--proposal-id",
                "light_001",
                "--output",
                "light_proposal.json",
            ]
        )

        self.assertEqual(args.context, "light_context.json")
        self.assertEqual(args.model, "planner-model")
        self.assertEqual(args.proposal_id, "light_001")
        self.assertEqual(args.output, "light_proposal.json")

    def test_assistant_light_batch_parser_keeps_selection_and_output_explicit(self):
        args = build_parser().parse_args(
            [
                "assistant-light-batch-propose",
                "--prompt-file",
                "request.txt",
                "--context",
                "light_selection.json",
                "--model",
                "planner",
                "--proposal-id",
                "light_batch_001",
                "--output",
                "light_batch_proposal.json",
            ]
        )
        self.assertIsNone(args.prompt)
        self.assertEqual(args.prompt_file, "request.txt")
        self.assertEqual(args.context, "light_selection.json")
        self.assertEqual(args.model, "planner")
        self.assertEqual(args.proposal_id, "light_batch_001")
        self.assertEqual(args.output, "light_batch_proposal.json")

    def test_assistant_lighting_rig_parser_keeps_context_model_and_output_explicit(self):
        args = build_parser().parse_args(
            [
                "assistant-lighting-rig-propose",
                "--prompt-file",
                "request.txt",
                "--context",
                "lighting_rig_context.json",
                "--model",
                "planner",
                "--proposal-id",
                "rig_001",
                "--output",
                "lighting_rig_proposal.json",
            ]
        )
        self.assertIsNone(args.prompt)
        self.assertEqual(args.prompt_file, "request.txt")
        self.assertEqual(args.context, "lighting_rig_context.json")
        self.assertEqual(args.model, "planner")
        self.assertEqual(args.proposal_id, "rig_001")
        self.assertEqual(args.output, "lighting_rig_proposal.json")

    def test_assistant_lighting_review_parser_keeps_png_and_vision_model_explicit(self):
        args = build_parser().parse_args(
            [
                "assistant-lighting-rig-review",
                "--prompt",
                "Review and improve the applied rig",
                "--context",
                "lighting_rig_review_context.json",
                "--preview",
                "lighting_rig_preview.png",
                "--model",
                "vision-model",
                "--proposal-id",
                "rig_review_001",
                "--raw-output",
                "lighting_rig_review_raw.json",
                "--output",
                "lighting_rig_review.json",
            ]
        )
        self.assertEqual(args.context, "lighting_rig_review_context.json")
        self.assertEqual(args.preview, "lighting_rig_preview.png")
        self.assertEqual(args.model, "vision-model")
        self.assertEqual(args.proposal_id, "rig_review_001")
        self.assertEqual(args.raw_output, "lighting_rig_review_raw.json")
        self.assertEqual(args.output, "lighting_rig_review.json")

    def test_assistant_camera_parser_keeps_context_model_and_output_explicit(self):
        args = build_parser().parse_args(
            [
                "assistant-camera-propose",
                "--prompt",
                "Use an 85 mm lens and focus at 3.5 meters",
                "--context",
                "camera_context.json",
                "--model",
                "planner-model",
                "--proposal-id",
                "camera_001",
                "--output",
                "camera_proposal.json",
            ]
        )

        self.assertEqual(args.context, "camera_context.json")
        self.assertEqual(args.model, "planner-model")
        self.assertEqual(args.proposal_id, "camera_001")
        self.assertEqual(args.output, "camera_proposal.json")

    def test_assistant_camera_batch_parser_keeps_selection_and_output_explicit(self):
        args = build_parser().parse_args(
            [
                "assistant-camera-batch-propose",
                "--prompt-file",
                "request.txt",
                "--context",
                "camera_selection.json",
                "--model",
                "planner-model",
                "--proposal-id",
                "camera_batch_001",
                "--output",
                "camera_batch_proposal.json",
            ]
        )

        self.assertIsNone(args.prompt)
        self.assertEqual(args.prompt_file, "request.txt")
        self.assertEqual(args.context, "camera_selection.json")
        self.assertEqual(args.model, "planner-model")
        self.assertEqual(args.proposal_id, "camera_batch_001")
        self.assertEqual(args.output, "camera_batch_proposal.json")

    def test_assistant_performance_parser_keeps_selection_and_output_explicit(self):
        args = build_parser().parse_args(
            [
                "assistant-performance-propose",
                "--prompt-file",
                "request.txt",
                "--context",
                "performance_selection.json",
                "--model",
                "planner-model",
                "--proposal-id",
                "performance_001",
                "--output",
                "performance_proposal.json",
            ]
        )

        self.assertIsNone(args.prompt)
        self.assertEqual(args.prompt_file, "request.txt")
        self.assertEqual(args.context, "performance_selection.json")
        self.assertEqual(args.model, "planner-model")
        self.assertEqual(args.proposal_id, "performance_001")
        self.assertEqual(args.output, "performance_proposal.json")

    def test_runtime_performance_parser_keeps_capture_and_output_explicit(self):
        args = build_parser().parse_args(
            [
                "assistant-runtime-performance-review",
                "--prompt",
                "Diagnose this PIE capture",
                "--capture",
                "runtime_capture.json",
                "--report-id",
                "runtime_review_001",
                "--output",
                "runtime_report.json",
            ]
        )

        self.assertEqual(args.capture, "runtime_capture.json")
        self.assertEqual(args.report_id, "runtime_review_001")
        self.assertEqual(args.output, "runtime_report.json")

    def test_insights_gpu_handler_passes_exact_capture_file_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            capture_path = Path(directory) / "capture.json"
            output_path = Path(directory) / "report.json"
            capture_path.write_bytes(b'{"host_written": true}\r\n')
            args = build_parser().parse_args(
                [
                    "assistant-insights-gpu-review",
                    "--prompt",
                    "Review this trace",
                    "--capture",
                    str(capture_path),
                    "--output",
                    str(output_path),
                ]
            )
            report = SimpleNamespace(
                status="review_complete",
                capture=SimpleNamespace(gpu_queue_count=1, scopes=[]),
                findings=[],
                report_id="insights_test",
                model_dump=lambda mode: {"status": "review_complete"},
            )
            result = SimpleNamespace(
                report=report,
                response=SimpleNamespace(model="fake-model"),
                attempt_count=1,
                recovery_reason=None,
            )
            expected = hashlib.sha256(capture_path.read_bytes()).hexdigest()
            with patch(
                "render_master_bot.cli._settings",
                return_value=SimpleNamespace(planner_model="fake-model"),
            ), patch(
                "render_master_bot.cli._client",
                return_value=object(),
            ), patch(
                "render_master_bot.cli.load_insights_gpu_capture",
                return_value=object(),
            ), patch(
                "render_master_bot.cli.review_insights_gpu_capture",
                return_value=result,
            ) as review:
                self.assertEqual(cmd_assistant_insights_gpu_review(args), 0)
            self.assertEqual(review.call_args.kwargs["source_file_sha256"], expected)

    def test_actor_gpu_impact_parser_keeps_experiment_and_output_explicit(self):
        args = build_parser().parse_args(
            [
                "assistant-actor-gpu-impact-review",
                "--prompt-file",
                "request.txt",
                "--experiment",
                "actor_gpu_experiment.json",
                "--model",
                "planner-model",
                "--report-id",
                "actor_gpu_review_001",
                "--output",
                "actor_gpu_report.json",
            ]
        )

        self.assertIsNone(args.prompt)
        self.assertEqual(args.prompt_file, "request.txt")
        self.assertEqual(args.experiment, "actor_gpu_experiment.json")
        self.assertEqual(args.model, "planner-model")
        self.assertEqual(args.report_id, "actor_gpu_review_001")
        self.assertEqual(args.output, "actor_gpu_report.json")

    def test_actor_gpu_impact_handler_passes_exact_experiment_file_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            experiment_path = Path(directory) / "experiment.json"
            output_path = Path(directory) / "report.json"
            experiment_path.write_bytes(b'{"host_written": true}\r\n')
            args = build_parser().parse_args(
                [
                    "assistant-actor-gpu-impact-review",
                    "--prompt",
                    "Measure this selected Actor",
                    "--experiment",
                    str(experiment_path),
                    "--output",
                    str(output_path),
                ]
            )
            report = SimpleNamespace(
                status="review_complete",
                experiment=SimpleNamespace(
                    target=SimpleNamespace(actor_label="HeroStatue"),
                    deltas=[object()],
                ),
                findings=[],
                report_id="actor_gpu_test",
                model_dump=lambda mode: {"status": "review_complete"},
            )
            result = SimpleNamespace(
                report=report,
                response=SimpleNamespace(model="fake-model"),
                attempt_count=1,
                recovery_reason=None,
            )
            expected = hashlib.sha256(experiment_path.read_bytes()).hexdigest()
            with patch(
                "render_master_bot.cli._settings",
                return_value=SimpleNamespace(planner_model="fake-model"),
            ), patch(
                "render_master_bot.cli._client",
                return_value=object(),
            ), patch(
                "render_master_bot.cli.load_actor_gpu_impact_experiment",
                return_value=object(),
            ), patch(
                "render_master_bot.cli.review_actor_gpu_impact",
                return_value=result,
            ) as review:
                self.assertEqual(cmd_assistant_actor_gpu_impact_review(args), 0)
            self.assertEqual(review.call_args.kwargs["source_file_sha256"], expected)

    def test_external_material_import_parsers_separate_proposal_and_approval(self):
        assistant_external = build_parser().parse_args(
            [
                "assistant-external-material-prepare",
                "--prompt-file",
                "request.txt",
                "--library-root",
                "material-library",
                "--work-dir",
                "external-request",
                "--proposal-id",
                "external_wood_001",
                "--output",
                "assistant_external.json",
            ]
        )
        self.assertEqual(assistant_external.resolution, "1k")
        self.assertEqual(assistant_external.image_format, "jpg")
        self.assertEqual(assistant_external.prompt_file, "request.txt")

        proposal = build_parser().parse_args(
            [
                "external-material-propose-import",
                "acquisition.json",
                "--destination-path",
                "/Game/RenderMasterBot/Imported/Wood",
                "--material-name",
                "M_PH_Wood",
                "--proposal-id",
                "external_wood_001",
                "--output",
                "proposal.json",
            ]
        )
        self.assertEqual(proposal.acquisition, "acquisition.json")
        self.assertEqual(proposal.material_name, "M_PH_Wood")

        execution = build_parser().parse_args(
            [
                "external-material-execute-import",
                "E:/Project/Project.uproject",
                "--engine-root",
                "E:/Unreal Engine/UE_5.7",
                "--proposal",
                "proposal.json",
                "--approve-sha256",
                "a" * 64,
                "--import-output",
                "import.json",
                "--asset-catalog",
                "asset_cards.json",
                "--scan-output",
                "imported_scan.json",
                "--catalog-sync-output",
                "catalog_sync.json",
                "--output",
                "execution.json",
            ]
        )
        self.assertEqual(execution.approve_sha256, "a" * 64)
        self.assertEqual(execution.approved_by, "local_operator")
        self.assertEqual(execution.import_output, "import.json")
        self.assertEqual(execution.asset_catalog, "asset_cards.json")

        recovery = build_parser().parse_args(
            [
                "external-material-sync-import",
                "E:/Project/Project.uproject",
                "--engine-root",
                "E:/Unreal Engine/UE_5.7",
                "--proposal",
                "proposal.json",
                "--execution",
                "execution.json",
                "--asset-catalog",
                "asset_cards.json",
                "--scan-output",
                "imported_scan.json",
                "--output",
                "catalog_sync.json",
            ]
        )
        self.assertEqual(recovery.execution, "execution.json")
        self.assertEqual(recovery.asset_catalog, "asset_cards.json")

    def test_utf8_console_setup_tolerates_captured_streams(self):
        with patch("render_master_bot.cli.sys.stdout", new=StringIO()), patch(
            "render_master_bot.cli.sys.stderr",
            new=StringIO(),
        ):
            _configure_utf8_console()


if __name__ == "__main__":
    unittest.main()
