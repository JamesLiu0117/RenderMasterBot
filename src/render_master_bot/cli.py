"""Command-line interface for inspecting and exercising the planning boundary."""

from __future__ import annotations

import argparse
import json
import sys
from time import perf_counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.asset_index import (
    ASSET_TYPES,
    AssetIndexError,
    load_asset_card_catalog,
    open_persistent_asset_index,
)
from render_master_bot.assistant_materials import (
    MaterialProposalError,
    load_selection_context,
    propose_material_change,
)
from render_master_bot.assistant_transforms import (
    TransformProposalError,
    load_transform_context,
    load_transform_selection_context,
    propose_transform_batch_change,
    propose_transform_change,
)
from render_master_bot.assistant_lights import (
    LightProposalError,
    load_light_context,
    load_light_selection_context,
    propose_light_batch_change,
    propose_light_change,
)
from render_master_bot.assistant_light_rigs import (
    LightingRigProposalError,
    load_lighting_rig_context,
    propose_lighting_rig,
)
from render_master_bot.assistant_lighting_reviews import (
    LightingRigReviewError,
    load_lighting_rig_review_context,
    propose_lighting_rig_review,
)
from render_master_bot.assistant_cameras import (
    CameraProposalError,
    load_camera_context,
    propose_camera_change,
)
from render_master_bot.assistant_camera_batches import (
    CameraBatchProposalError,
    load_camera_selection_context,
    propose_camera_batch_change,
)
from render_master_bot.assistant_conversation import (
    ConversationRoutingError,
    load_conversation_request,
    route_conversation_turn,
)
from render_master_bot.assistant_performance import (
    PerformanceProposalError,
    load_performance_selection_context,
    propose_performance_review,
)
from render_master_bot.assistant_level_audits import (
    build_read_only_static_triage,
    LevelAuditError,
    load_level_audit_context,
    review_level_audit,
)
from render_master_bot.assistant_runtime_performance import (
    RuntimePerformanceReviewError,
    load_runtime_performance_capture,
    review_runtime_performance,
)
from render_master_bot.assistant_insights_gpu import (
    InsightsGpuReviewError,
    hash_insights_gpu_capture_file,
    load_insights_gpu_capture,
    review_insights_gpu_capture,
)
from render_master_bot.assistant_actor_gpu_impact import (
    ActorGpuImpactReviewError,
    hash_actor_gpu_impact_file,
    load_actor_gpu_impact_experiment,
    review_actor_gpu_impact,
)
from render_master_bot.assistant_actor_gpu_impact_studies import (
    ActorGpuImpactStudyReviewError,
    hash_actor_gpu_impact_study_file,
    load_actor_gpu_impact_study_capture_set,
    review_actor_gpu_impact_study,
)
from render_master_bot.assistant_level_profiling import (
    LevelProfilingSynthesisError,
    analyze_level_profiling_run,
    build_level_profiling_run,
    load_actor_study_report,
    load_gpu_scope_report,
    load_level_audit_report,
    load_level_profiling_skips,
    load_runtime_performance_report,
)
from render_master_bot.assistant_shadow_optimization import (
    ShadowOptimizationAnalysisError,
    analyze_shadow_optimization_file,
)
from render_master_bot.assistant_shadow_optimization_verification import (
    ShadowOptimizationVerificationError,
    analyze_shadow_optimization_verification_files,
)
from render_master_bot.assistant_external_materials import (
    ExternalMaterialAssistantError,
    prepare_external_material_assistant_proposal,
)
from render_master_bot.camera_framing import VIEW_AXES, CameraFramingError, frame_camera
from render_master_bot.contracts import CONTRACT_MODELS, RenderSpecPatch, VisualBenchmarkSuite
from render_master_bot.correction_planner import CorrectionPlanningError, plan_correction
from render_master_bot.external_materials import (
    ExternalMaterialError,
    acquire_polyhaven_material,
    discover_polyhaven_materials,
    load_external_material_search,
)
from render_master_bot.material_variants import (
    MaterialVariantError,
    ScalarParameterOverride,
    VectorParameterOverride,
    create_material_variant,
    inspect_material_parameters,
)
from render_master_bot.material_import_workflow import (
    MaterialImportWorkflowError,
    create_external_material_import_proposal,
    execute_external_material_import,
    load_external_material_acquisition,
    load_external_material_import_proposal,
    load_material_import_execution,
)
from render_master_bot.material_catalog_sync import (
    MaterialCatalogSyncError,
    catalog_sync_evidence,
    commit_asset_catalog,
    enrich_imported_asset_cards,
    load_and_merge_asset_catalog,
)
from render_master_bot.models import RenderSpec
from render_master_bot.ollama import OllamaClient, OllamaError
from render_master_bot.orchestrator import WorkflowError, run_render_workflow
from render_master_bot.patching import PatchApplicationError, apply_render_spec_patch
from render_master_bot.planner import PlanningError, ScenePlanner
from render_master_bot.preflight import run_preflight
from render_master_bot.product_baseline import (
    ProductBaselineError,
    capture_product_runtime_baseline,
    load_capability_evidence_ledger,
    load_product_benchmark_suite,
)
from render_master_bot.schemas import contract_model, contract_schema
from render_master_bot.serialization import canonical_sha256
from render_master_bot.settings import Settings
from render_master_bot.training_data import (
    DEFAULT_DATASET_ID,
    DEFAULT_SPLIT_SEED,
    TrainingDataError,
    audit_training_dataset,
    build_training_dataset,
)
from render_master_bot.training_campaign import (
    CAMPAIGN_MANIFEST_FILENAME,
    TrainingCampaignError,
    build_training_baseline_report,
    cancel_training_campaign,
    campaign_status_summary,
    create_training_campaign_plan,
    load_training_campaign_run,
    run_training_campaign,
    validate_training_campaign_runtime,
)
from render_master_bot.training_reference import (
    TrainingReferenceError,
    compile_material_reference_suite,
    compile_single_asset_reference_suite,
    create_training_reference_campaign_plan,
    reference_campaign_manifest,
    run_training_reference_workflow,
    validate_training_reference_campaign_runtime,
    validate_training_reference_suite,
)
from render_master_bot.unreal_assets import UnrealAssetScanError, run_unreal_asset_scan
from render_master_bot.unreal_executor import UnrealSceneBuildError, run_unreal_scene_build
from render_master_bot.unreal_materials import (
    UnrealMaterialImportError,
    run_unreal_pbr_material_import,
)
from render_master_bot.unreal_preview import UnrealPreviewError, run_unreal_preview
from render_master_bot.unreal_probe import UnrealProbeError, probe_unreal_project
from render_master_bot.visual_benchmark import VisualBenchmarkError, run_visual_benchmark
from render_master_bot.visual_evaluator import VisualEvaluationError, evaluate_preview_run


def _settings() -> Settings:
    return Settings.from_env()


def _client(
    settings: Settings | None = None,
    *,
    num_ctx: int | None = None,
) -> OllamaClient:
    settings = settings or _settings()
    return OllamaClient(
        settings.ollama_base_url,
        num_ctx=num_ctx if num_ctx is not None else settings.num_ctx,
    )


def _asset_index(settings: Settings):
    return open_persistent_asset_index(
        settings.chroma_dir,
        _client(settings),
        embedding_model=settings.embedding_model,
        collection_name=settings.asset_collection,
    )


def _write_json(value: object, output: str | None) -> None:
    text = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"Wrote {output}")
    else:
        print(text, end="")


def _configure_utf8_console() -> None:
    """Keep Chinese prompts and JSON usable on legacy Windows code pages."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _response_metrics(response, *, status: str, error: str | None = None) -> dict:
    return {
        "status": status,
        "model": response.model,
        "total_duration_ns": response.total_duration_ns,
        "total_duration_seconds": (
            response.total_duration_ns / 1_000_000_000
            if response.total_duration_ns is not None
            else None
        ),
        "prompt_tokens": response.prompt_tokens,
        "output_tokens": response.output_tokens,
        "done_reason": response.done_reason,
        "error": error,
    }


def cmd_doctor(_: argparse.Namespace) -> int:
    settings = _settings()
    try:
        models = _client(settings).list_models()
    except OllamaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Ollama: reachable")
    if models:
        print("Models:")
        for name in models:
            print(f"  - {name}")
    else:
        print("Models: none installed")
    installed = set(models)
    planner_ready = settings.planner_model in installed
    vision_ready = settings.vision_model in installed
    embedding_ready = settings.embedding_model in installed
    print(f"Planner: {settings.planner_model} ({'ready' if planner_ready else 'missing'})")
    print(f"Vision:  {settings.vision_model} ({'ready' if vision_ready else 'missing'})")
    print(
        f"Embedding: {settings.embedding_model} "
        f"({'ready' if embedding_ready else 'missing'})"
    )
    print(f"Context: {settings.num_ctx} tokens")
    print(f"Vision context: {settings.vision_num_ctx} tokens")
    return 0 if planner_ready and vision_ready and embedding_ready else 1


def cmd_config(_: argparse.Namespace) -> int:
    settings = _settings()
    _write_json(
        {
            "ollama_base_url": settings.ollama_base_url,
            "planner_model": settings.planner_model,
            "vision_model": settings.vision_model,
            "embedding_model": settings.embedding_model,
            "num_ctx": settings.num_ctx,
            "vision_num_ctx": settings.vision_num_ctx,
            "data_dir": str(settings.data_dir),
            "chroma_dir": str(settings.chroma_dir),
            "asset_collection": settings.asset_collection,
        },
        None,
    )
    return 0


def cmd_product_benchmark_validate(args: argparse.Namespace) -> int:
    try:
        suite = load_product_benchmark_suite(args.path)
    except ProductBaselineError as exc:
        print(f"ERROR: product benchmark validation failed: {exc}", file=sys.stderr)
        return 1
    category_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    language_counts: dict[str, int] = {}
    for case in suite.cases:
        category_counts[case.category] = category_counts.get(case.category, 0) + 1
        stage_counts[case.evaluation_stage] = (
            stage_counts.get(case.evaluation_stage, 0) + 1
        )
        language_counts[case.language] = language_counts.get(case.language, 0) + 1
    _write_json(
        {
            "suite_id": suite.suite_id,
            "status": suite.status,
            "case_count": len(suite.cases),
            "context_profile_count": len(suite.context_profiles),
            "category_counts": dict(sorted(category_counts.items())),
            "evaluation_stage_counts": dict(sorted(stage_counts.items())),
            "language_counts": dict(sorted(language_counts.items())),
            "suite_sha256": suite.suite_sha256,
        },
        args.output,
    )
    return 0


def cmd_capability_evidence_validate(args: argparse.Namespace) -> int:
    try:
        ledger = load_capability_evidence_ledger(args.path)
    except ProductBaselineError as exc:
        print(f"ERROR: capability evidence validation failed: {exc}", file=sys.stderr)
        return 1
    level_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for entry in ledger.entries:
        level_counts[entry.highest_evidence_level] = (
            level_counts.get(entry.highest_evidence_level, 0) + 1
        )
        status_counts[entry.implementation_status] = (
            status_counts.get(entry.implementation_status, 0) + 1
        )
    _write_json(
        {
            "ledger_id": ledger.ledger_id,
            "assessed_at_utc": ledger.assessed_at_utc.isoformat(),
            "capability_count": len(ledger.entries),
            "implementation_status_counts": dict(sorted(status_counts.items())),
            "highest_evidence_level_counts": dict(sorted(level_counts.items())),
            "ledger_sha256": ledger.ledger_sha256,
        },
        args.output,
    )
    return 0


def cmd_product_baseline_capture(args: argparse.Namespace) -> int:
    settings = _settings()
    try:
        baseline = capture_product_runtime_baseline(
            baseline_id=args.baseline_id,
            source_root=args.source_root,
            unreal_project_path=args.path,
            engine_root=args.engine_root,
            asset_catalog_path=args.assets,
            training_dataset_manifest_path=args.dataset,
            product_benchmark_suite_path=args.benchmark_suite,
            capability_evidence_ledger_path=args.evidence_ledger,
            settings=settings,
            client=_client(settings),
        )
    except (ProductBaselineError, ValueError) as exc:
        print(f"ERROR: product baseline capture failed: {exc}", file=sys.stderr)
        return 1
    _write_json(baseline.model_dump(mode="json"), args.output)
    return 0


def cmd_training_data_build(args: argparse.Namespace) -> int:
    try:
        result = build_training_dataset(
            source_root=args.source_root,
            output_directory=args.output_dir,
            dataset_id=args.dataset_id,
            split_seed=args.split_seed,
            validation_group_fraction=args.validation_fraction,
            test_group_fraction=args.test_fraction,
            campaign_run_path=getattr(args, "campaign_run", None),
            campaign_run_paths=getattr(args, "campaign_runs", None),
        )
    except TrainingDataError as exc:
        print(f"ERROR: training dataset build failed: {exc}", file=sys.stderr)
        return 1
    _write_json(
        {
            "dataset_id": result.manifest.dataset_id,
            "manifest_path": str(result.manifest_path),
            "audit_path": str(result.audit_path),
            "discovered_source_manifest_count": (
                result.manifest.discovered_source_manifest_count
            ),
            "accepted_source_manifest_count": len(
                result.manifest.accepted_source_manifests
            ),
            "skipped_source_count": len(result.manifest.skipped_sources),
            "total_example_count": result.manifest.total_example_count,
            "campaign_count": (
                len(result.manifest.campaign_sources or [])
                or int(result.manifest.campaign_run_path is not None)
            ),
            "source_evidence_checked": result.audit.source_evidence_checked,
            "split_counts": result.audit.split_counts,
            "integrity_status": result.audit.integrity_status,
            "qlora_smoke_ready": result.audit.qlora_smoke_ready,
            "product_finetune_ready": result.audit.product_finetune_ready,
            "readiness_reasons": result.audit.readiness_reasons,
        },
        args.output,
    )
    return 0 if result.audit.integrity_status == "passed" else 1


def cmd_training_data_audit(args: argparse.Namespace) -> int:
    try:
        report = audit_training_dataset(args.manifest, source_root=args.source_root)
    except TrainingDataError as exc:
        print(f"ERROR: training dataset audit failed: {exc}", file=sys.stderr)
        return 1
    _write_json(report.model_dump(mode="json"), args.output)
    return 0 if report.integrity_status == "passed" else 1


def cmd_training_campaign_plan(args: argparse.Namespace) -> int:
    settings = _settings()
    try:
        manifest_path, run = create_training_campaign_plan(
            suite_path=args.suite,
            campaign_directory=args.campaign_dir,
            campaign_id=args.campaign_id,
            project_path=args.path,
            asset_catalog_path=args.assets,
            planner_model=args.planner_model or settings.planner_model,
            evaluator_model=args.vision_model or settings.vision_model,
            max_cases=args.max_cases,
            case_ids=args.case_id,
            max_attempts_per_case=args.max_attempts,
        )
    except TrainingCampaignError as exc:
        print(f"ERROR: training campaign plan failed: {exc}", file=sys.stderr)
        return 1
    summary = campaign_status_summary(run)
    summary["manifest_path"] = str(manifest_path)
    _write_json(summary, args.output)
    return 0


def cmd_training_campaign_run(args: argparse.Namespace) -> int:
    campaign_manifest = (
        Path(args.campaign_dir).expanduser().resolve() / CAMPAIGN_MANIFEST_FILENAME
    )
    settings = _settings()
    try:
        run, _ = validate_training_campaign_runtime(
            campaign_manifest_path=campaign_manifest,
            project_path=args.path,
            asset_catalog_path=args.assets,
        )
        cards = load_asset_card_catalog(args.assets)
        planner_client = _client(settings)
        vision_client = _client(settings, num_ctx=settings.vision_num_ctx)
        with _asset_index(settings) as index:
            index_report = index.sync(cards)

            def case_runner(case, workflow_directory, workflow_id):
                run_render_workflow(
                    planner_client=planner_client,
                    vision_client=vision_client,
                    correction_client=planner_client,
                    asset_searcher=index,
                    planner_model=run.planner.model,
                    vision_model=run.evaluator.model,
                    prompt=case.prompt,
                    uproject_path=args.path,
                    engine_root=args.engine_root,
                    asset_catalog_path=args.assets,
                    workflow_directory=workflow_directory,
                    workflow_id=workflow_id,
                    retrieve_assets=case.retrieve_assets,
                    retrieve_materials=case.retrieve_materials,
                    max_iterations=case.max_iterations,
                    view_axis=case.view_axis,
                    margin_fraction=case.margin_fraction,
                    timeout_seconds=case.timeout_seconds,
                    fail_on_warning=False,
                    studio_calibration=True,
                )
                return workflow_directory / "workflow_manifest.json"

            print(
                "CAMPAIGN INDEX: "
                f"total={index_report.total} inserted={index_report.inserted} "
                f"updated={index_report.updated} deleted={index_report.deleted}",
                flush=True,
            )
            result = run_training_campaign(
                campaign_manifest_path=campaign_manifest,
                case_runner=case_runner,
                retry_failed=args.retry_failed,
                progress=lambda message: print(f"CAMPAIGN {message}", flush=True),
            )
    except KeyboardInterrupt:
        print("TRAINING CAMPAIGN: cancelled by operator", file=sys.stderr)
        return 130
    except (TrainingCampaignError, AssetIndexError, OllamaError) as exc:
        print(f"ERROR: training campaign run failed: {exc}", file=sys.stderr)
        return 1
    summary = campaign_status_summary(result)
    summary["manifest_path"] = str(campaign_manifest)
    _write_json(summary, args.output)
    return 0 if result.status == "completed" else 2


def cmd_training_campaign_status(args: argparse.Namespace) -> int:
    try:
        run = load_training_campaign_run(args.manifest)
    except TrainingCampaignError as exc:
        print(f"ERROR: training campaign status failed: {exc}", file=sys.stderr)
        return 1
    _write_json(campaign_status_summary(run), args.output)
    return 0


def cmd_training_campaign_cancel(args: argparse.Namespace) -> int:
    try:
        run = cancel_training_campaign(args.manifest, reason=args.reason)
    except TrainingCampaignError as exc:
        print(f"ERROR: training campaign cancellation failed: {exc}", file=sys.stderr)
        return 1
    _write_json(campaign_status_summary(run), args.output)
    return 0


def cmd_training_campaign_baseline(args: argparse.Namespace) -> int:
    try:
        run = load_training_campaign_run(args.manifest)
        report = build_training_baseline_report(run, report_id=args.report_id)
    except (TrainingCampaignError, ValidationError) as exc:
        print(f"ERROR: training baseline failed: {exc}", file=sys.stderr)
        return 1
    _write_json(report.model_dump(mode="json"), args.output)
    return 0


def cmd_training_reference_validate(args: argparse.Namespace) -> int:
    try:
        report = validate_training_reference_suite(
            reference_suite_path=args.references,
            campaign_suite_path=args.suite,
            project_path=args.path,
            asset_catalog_path=args.assets,
        )
    except (TrainingReferenceError, TrainingCampaignError) as exc:
        print(f"ERROR: training reference validation failed: {exc}", file=sys.stderr)
        return 1
    _write_json(report.model_dump(mode="json"), args.output)
    return 0 if report.ready_to_run else 1


def cmd_training_reference_compile(args: argparse.Namespace) -> int:
    try:
        suite = compile_single_asset_reference_suite(
            campaign_suite_path=args.suite,
            project_path=args.path,
            asset_catalog_path=args.assets,
            output_path=args.output,
            reference_suite_id=args.reference_suite_id,
            retrieval_campaign_directory=args.retrieval_campaign,
            case_ids=args.case_id,
            reference_category=getattr(args, "reference_category", "single_asset"),
        )
    except (TrainingReferenceError, TrainingCampaignError) as exc:
        print(f"ERROR: training reference compile failed: {exc}", file=sys.stderr)
        return 1
    _write_json(
        {
            "reference_suite_id": suite.reference_suite_id,
            "reference_suite_sha256": canonical_sha256(suite),
            "compiled_case_count": len(suite.cases),
            "compiled_case_ids": [case.case_id for case in suite.cases],
            "output_path": str(Path(args.output).expanduser().resolve()),
        },
        None,
    )
    return 0


def cmd_training_reference_compile_materials(args: argparse.Namespace) -> int:
    try:
        suite = compile_material_reference_suite(
            campaign_suite_path=args.suite,
            project_path=args.path,
            asset_catalog_path=args.assets,
            output_path=args.output,
            reference_suite_id=args.reference_suite_id,
            retrieval_campaign_directory=args.retrieval_campaign,
            case_ids=args.case_id,
        )
    except (TrainingReferenceError, TrainingCampaignError) as exc:
        print(f"ERROR: training reference compile failed: {exc}", file=sys.stderr)
        return 1
    _write_json(
        {
            "reference_suite_id": suite.reference_suite_id,
            "reference_suite_sha256": canonical_sha256(suite),
            "compiled_case_count": len(suite.cases),
            "compiled_case_ids": [case.case_id for case in suite.cases],
            "output_path": str(Path(args.output).expanduser().resolve()),
        },
        None,
    )
    return 0


def cmd_training_reference_plan(args: argparse.Namespace) -> int:
    settings = _settings()
    try:
        manifest_path, run, report = create_training_reference_campaign_plan(
            reference_suite_path=args.references,
            campaign_suite_path=args.suite,
            campaign_directory=args.campaign_dir,
            campaign_id=args.campaign_id,
            project_path=args.path,
            asset_catalog_path=args.assets,
            evaluator_model=args.vision_model or settings.vision_model,
            max_attempts_per_case=args.max_attempts,
        )
    except (TrainingReferenceError, TrainingCampaignError) as exc:
        print(f"ERROR: training reference plan failed: {exc}", file=sys.stderr)
        return 1
    summary = campaign_status_summary(run)
    summary.update(
        {
            "manifest_path": str(manifest_path),
            "reference_suite_sha256": report.reference_suite_sha256,
            "ready_case_count": report.ready_case_count,
            "full_coverage": report.full_coverage,
        }
    )
    _write_json(summary, args.output)
    return 0


def cmd_training_reference_run(args: argparse.Namespace) -> int:
    campaign_manifest = reference_campaign_manifest(args.campaign_dir)
    settings = _settings()
    try:
        run, campaign_suite, reference_suite, _ = (
            validate_training_reference_campaign_runtime(
                campaign_manifest_path=campaign_manifest,
                reference_suite_path=args.references,
                campaign_suite_path=args.suite,
                project_path=args.path,
                asset_catalog_path=args.assets,
            )
        )
        campaign_cases = {case.case_id: case for case in campaign_suite.cases}
        references = {case.case_id: case for case in reference_suite.cases}
        vision_client = _client(settings, num_ctx=settings.vision_num_ctx)

        def case_runner(case, workflow_directory, workflow_id):
            run_training_reference_workflow(
                reference=references[case.case_id],
                campaign_case=campaign_cases[case.case_id],
                vision_client=vision_client,
                vision_model=run.evaluator.model,
                uproject_path=args.path,
                engine_root=args.engine_root,
                asset_catalog_path=args.assets,
                workflow_directory=workflow_directory,
                workflow_id=workflow_id,
            )
            return workflow_directory / "workflow_manifest.json"

        result = run_training_campaign(
            campaign_manifest_path=campaign_manifest,
            case_runner=case_runner,
            retry_failed=args.retry_failed,
            progress=lambda message: print(f"REFERENCE {message}", flush=True),
        )
    except KeyboardInterrupt:
        print("TRAINING REFERENCE: cancelled by operator", file=sys.stderr)
        return 130
    except (
        TrainingReferenceError,
        TrainingCampaignError,
        AssetIndexError,
        OllamaError,
    ) as exc:
        print(f"ERROR: training reference run failed: {exc}", file=sys.stderr)
        return 1
    summary = campaign_status_summary(result)
    summary["manifest_path"] = str(campaign_manifest)
    _write_json(summary, args.output)
    return 0 if result.status == "completed" else 2


def cmd_schema(args: argparse.Namespace) -> int:
    _write_json(contract_schema(args.contract), args.output)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        model = contract_model(args.contract)
        value = model.model_validate_json(Path(args.path).read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID {model.__name__} {value.schema_version}")
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    try:
        spec = RenderSpec.model_validate_json(Path(args.path).read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        print(f"INVALID RenderSpec: {exc}", file=sys.stderr)
        return 1

    report = run_preflight(spec)
    _write_json(report.model_dump(mode="json"), args.output)
    print(
        f"PREFLIGHT {report.verdict}: {len(report.issues)} issue(s)",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    if report.verdict == "fail":
        return 1
    if report.verdict == "needs_review" and args.fail_on_warning:
        return 2
    return 0


def cmd_apply_patch(args: argparse.Namespace) -> int:
    input_path = Path(args.path).expanduser().resolve()
    patch_path = Path(args.patch).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    if len({input_path, patch_path, output_path}) != 3:
        print(
            "ERROR: source, patch, and output paths must be distinct",
            file=sys.stderr,
        )
        return 1
    if output_path.exists():
        print("ERROR: patch output already exists; choose a new path", file=sys.stderr)
        return 1
    try:
        spec = RenderSpec.model_validate_json(input_path.read_text(encoding="utf-8-sig"))
        patch = RenderSpecPatch.model_validate_json(
            patch_path.read_text(encoding="utf-8-sig")
        )
        corrected = apply_render_spec_patch(spec, patch)
        _write_json(corrected.model_dump(mode="json"), args.output)
    except (OSError, ValidationError, PatchApplicationError) as exc:
        print(f"ERROR: patch application failed: {exc}", file=sys.stderr)
        return 1
    print(f"PATCH APPLIED: operations={len(patch.operations)}")
    return 0


def cmd_frame_camera(args: argparse.Namespace) -> int:
    input_path = Path(args.path).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    patch_path = Path(args.patch_output).expanduser().resolve()
    if len({input_path, output_path, patch_path}) != 3:
        print(
            "ERROR: input, framed output, and patch output paths must be distinct",
            file=sys.stderr,
        )
        return 1
    try:
        spec = RenderSpec.model_validate_json(input_path.read_text(encoding="utf-8-sig"))
        cards = load_asset_card_catalog(args.assets)
        result = frame_camera(
            spec,
            cards,
            margin_fraction=args.margin,
            view_axis=args.view_axis,
        )
        _write_json(result.spec.model_dump(mode="json"), args.output)
        _write_json(result.patch.model_dump(mode="json"), args.patch_output)
    except (OSError, ValidationError, AssetIndexError, CameraFramingError) as exc:
        print(f"ERROR: camera framing failed: {exc}", file=sys.stderr)
        return 1
    target = result.target_cm
    print(
        "CAMERA FRAMED: "
        f"objects={len(result.object_ids)} distance_cm={result.distance_cm:g} "
        f"target_cm=({target.x:g}, {target.y:g}, {target.z:g}) "
        f"view={result.view_axis}"
    )
    return 0


def cmd_evaluate_preview(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.vision_model
    run_root = Path(args.run_dir).expanduser().resolve()
    output_path = (run_root / args.output).resolve()
    metrics_path = (run_root / args.metrics_output).resolve()
    try:
        output_path.relative_to(run_root)
        metrics_path.relative_to(run_root)
    except ValueError:
        print("ERROR: evaluation outputs must stay inside the run directory", file=sys.stderr)
        return 1
    if output_path == metrics_path:
        print("ERROR: evaluation and metrics output paths must be distinct", file=sys.stderr)
        return 1
    if output_path.exists() or metrics_path.exists():
        print("ERROR: evaluation output already exists; choose new output names", file=sys.stderr)
        return 1
    try:
        result = evaluate_preview_run(
            _client(settings, num_ctx=settings.vision_num_ctx),
            model=model,
            run_directory=run_root,
        )
        _write_json(result.report.model_dump(mode="json"), str(output_path))
        _write_json(_response_metrics(result.response, status="valid"), str(metrics_path))
    except (OSError, VisualEvaluationError, OllamaError) as exc:
        print(f"ERROR: preview evaluation failed: {exc}", file=sys.stderr)
        return 1
    print(
        "PREVIEW EVALUATION: "
        f"verdict={result.report.verdict} issues={len(result.report.issues)} "
        f"model={result.response.model}"
    )
    return 0


def cmd_benchmark_evaluator(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.vision_model
    suite_path = Path(args.path).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    if suite_path == output_path:
        print("ERROR: benchmark suite and output paths must be distinct", file=sys.stderr)
        return 1
    if output_path.exists():
        print("ERROR: benchmark output already exists; choose a new path", file=sys.stderr)
        return 1
    try:
        suite = VisualBenchmarkSuite.model_validate_json(
            suite_path.read_text(encoding="utf-8-sig")
        )
        report = run_visual_benchmark(
            _client(settings, num_ctx=settings.vision_num_ctx),
            model=model,
            suite=suite,
            suite_root=suite_path.parent,
        )
        _write_json(report.model_dump(mode="json"), str(output_path))
    except (OSError, ValidationError, VisualBenchmarkError, OllamaError) as exc:
        print(f"ERROR: visual evaluator benchmark failed: {exc}", file=sys.stderr)
        return 1
    print(
        "VISUAL BENCHMARK: "
        f"passed={report.passed_case_count}/{report.case_count} "
        f"accuracy={report.case_accuracy:.3f} "
        f"stability={report.verdict_stability:.3f} "
        f"contradictions={report.contradiction_count} model={report.evaluator.model}"
    )
    return 0 if report.passed else 2


def cmd_plan_correction(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    run_root = Path(args.run_dir).expanduser().resolve()
    named_outputs = {
        "decision": args.output,
        "metrics": args.metrics_output,
        "corrected_spec": args.corrected_output,
    }
    output_paths = {
        name: (run_root / value).resolve()
        for name, value in named_outputs.items()
    }
    try:
        for path in output_paths.values():
            path.relative_to(run_root)
    except ValueError:
        print("ERROR: correction outputs must stay inside the run directory", file=sys.stderr)
        return 1
    if len(set(output_paths.values())) != len(output_paths):
        print("ERROR: correction output paths must be distinct", file=sys.stderr)
        return 1
    if any(path.exists() for path in output_paths.values()):
        print("ERROR: correction output already exists; choose new output names", file=sys.stderr)
        return 1
    try:
        result = plan_correction(
            _client(settings),
            model=model,
            run_directory=run_root,
            evaluation_path=args.evaluation,
        )
        _write_json(result.decision.model_dump(mode="json"), str(output_paths["decision"]))
        _write_json(
            _response_metrics(result.response, status="valid"),
            str(output_paths["metrics"]),
        )
        if result.corrected_spec is not None:
            _write_json(
                result.corrected_spec.model_dump(mode="json"),
                str(output_paths["corrected_spec"]),
            )
    except (OSError, CorrectionPlanningError, OllamaError) as exc:
        print(f"ERROR: correction planning failed: {exc}", file=sys.stderr)
        return 1
    print(
        "CORRECTION PLAN: "
        f"outcome={result.decision.outcome} model={result.response.model} "
        f"operations={len(result.decision.patch.operations) if result.decision.patch else 0}"
    )
    return 0


def cmd_unreal_probe(args: argparse.Namespace) -> int:
    try:
        manifest = probe_unreal_project(args.path, engine_root=args.engine_root)
    except UnrealProbeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _write_json(manifest.model_dump(mode="json"), args.output)
    print(
        "UNREAL PROBE: "
        f"engine={manifest.engine_version} "
        f"python={'yes' if manifest.python_available else 'no'} "
        f"mrq={'yes' if manifest.movie_render_queue_available else 'no'} "
        f"mrg={'yes' if manifest.movie_render_graph_available else 'no'} "
        f"warnings={len(manifest.warnings)}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_unreal_scan_assets(args: argparse.Namespace) -> int:
    try:
        scan, cards = run_unreal_asset_scan(
            args.path,
            engine_root=args.engine_root,
            raw_output=args.raw_output,
            limit=args.limit,
            path_prefix=args.path_prefix,
            timeout_seconds=args.timeout,
        )
    except UnrealAssetScanError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    try:
        _write_json([card.model_dump(mode="json") for card in cards], args.output)
    except OSError as exc:
        print(f"ERROR: cannot write AssetCard output: {exc}", file=sys.stderr)
        return 1
    print(
        "UNREAL ASSET SCAN: "
        f"project={scan.project_name} "
        f"discovered={scan.total_assets} "
        f"exported={len(cards)} "
        f"warnings={len(scan.warnings)}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_unreal_import_pbr_material(args: argparse.Namespace) -> int:
    try:
        request, result = run_unreal_pbr_material_import(
            args.path,
            engine_root=args.engine_root,
            destination_path=args.destination_path,
            material_name=args.material_name,
            base_color=args.base_color,
            normal=args.normal,
            roughness=args.roughness,
            ambient_occlusion=args.ambient_occlusion,
            output=args.output,
            timeout_seconds=args.timeout,
        )
    except UnrealMaterialImportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "UNREAL PBR MATERIAL IMPORT: "
        f"project={result.project_name} material={result.material_engine_path} "
        f"textures={len(result.textures)} destination={request.destination_path}"
    )
    print(f"Wrote {args.output}")
    return 0


def _scalar_overrides(values: list[str]) -> list[ScalarParameterOverride]:
    overrides = []
    for value in values:
        name, separator, raw = value.partition("=")
        if not separator or not name.strip():
            raise ValueError(f"invalid scalar override {value!r}; expected Name=Value")
        overrides.append(ScalarParameterOverride(name=name.strip(), value=float(raw)))
    return overrides


def _vector_overrides(values: list[str]) -> list[VectorParameterOverride]:
    overrides = []
    for value in values:
        name, separator, raw = value.partition("=")
        components = raw.split(",") if separator else []
        if not name.strip() or len(components) not in {3, 4}:
            raise ValueError(
                f"invalid vector override {value!r}; expected Name=R,G,B or Name=R,G,B,A"
            )
        numbers = [float(component) for component in components]
        if len(numbers) == 3:
            numbers.append(1.0)
        overrides.append(
            VectorParameterOverride(
                name=name.strip(),
                r=numbers[0],
                g=numbers[1],
                b=numbers[2],
                a=numbers[3],
            )
        )
    return overrides


def cmd_unreal_material_parameters(args: argparse.Namespace) -> int:
    try:
        result = inspect_material_parameters(
            args.path,
            engine_root=args.engine_root,
            material_path=args.material,
            output=args.output,
            timeout_seconds=args.timeout,
        )
    except MaterialVariantError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "UNREAL MATERIAL PARAMETERS: "
        f"material={result.material_path} scalar={len(result.scalar_parameters)} "
        f"vector={len(result.vector_parameters)}"
    )
    print(f"Wrote {args.output}")
    return 0


def cmd_unreal_create_material_variant(args: argparse.Namespace) -> int:
    try:
        request, result = create_material_variant(
            args.path,
            engine_root=args.engine_root,
            parent_material_path=args.parent_material,
            destination_path=args.destination_path,
            instance_name=args.instance_name,
            scalar_parameters=_scalar_overrides(args.scalar),
            vector_parameters=_vector_overrides(args.vector),
            output=args.output,
            timeout_seconds=args.timeout,
        )
    except (MaterialVariantError, ValidationError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "UNREAL MATERIAL VARIANT: "
        f"parent={request.parent_material_path} instance={result.instance_engine_path} "
        f"scalar={len(result.scalar_parameters)} vector={len(result.vector_parameters)}"
    )
    print(f"Wrote {args.output}")
    return 0


def cmd_external_material_search(args: argparse.Namespace) -> int:
    settings = _settings()
    try:
        report = discover_polyhaven_materials(
            query=args.query,
            embedder=_client(settings),
            embedding_model=settings.embedding_model,
            limit=args.limit,
            timeout_seconds=args.timeout,
        )
        _write_json(report.model_dump(mode="json"), args.output)
    except (ExternalMaterialError, OllamaError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "EXTERNAL MATERIAL SEARCH: "
        f"provider={report.provider} results={len(report.candidates)} "
        f"license=CC0-1.0 credit={report.provider_credit!r}"
    )
    return 0


def cmd_external_material_acquire(args: argparse.Namespace) -> int:
    try:
        report = load_external_material_search(args.search_report)
        matches = [
            candidate
            for candidate in report.candidates
            if candidate.provider_asset_id == args.asset_id
        ]
        if len(matches) != 1:
            raise ExternalMaterialError(
                f"asset {args.asset_id!r} is not a unique candidate in the search report"
            )
        acquisition = acquire_polyhaven_material(
            matches[0],
            destination_root=args.destination_root,
            resolution=args.resolution,
            image_format=args.image_format,
            timeout_seconds=args.timeout,
        )
        _write_json(acquisition.model_dump(mode="json"), args.output)
    except (ExternalMaterialError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "EXTERNAL MATERIAL ACQUISITION: "
        f"asset={acquisition.candidate.provider_asset_id} maps={len(acquisition.maps)} "
        f"resolution={acquisition.resolution} format={acquisition.image_format}"
    )
    return 0


def cmd_assistant_external_material_prepare(args: argparse.Namespace) -> int:
    settings = _settings()
    try:
        query = _read_run_prompt(args.prompt, args.prompt_file)
        _, _, _, proposal = prepare_external_material_assistant_proposal(
            query=query,
            embedder=_client(settings),
            embedding_model=settings.embedding_model,
            library_root=args.library_root,
            work_directory=args.work_dir,
            proposal_id=args.proposal_id,
            resolution=args.resolution,
            image_format=args.image_format,
            timeout_seconds=args.timeout,
        )
        _write_json(proposal.model_dump(mode="json"), args.output)
    except (
        ExternalMaterialAssistantError,
        ExternalMaterialError,
        MaterialImportWorkflowError,
        OllamaError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "ASSISTANT EXTERNAL MATERIAL: "
        f"asset={proposal.provider_asset_id} license={proposal.license} "
        f"maps={proposal.downloaded_map_count} status={proposal.status}"
    )
    print(f"APPROVAL SHA-256: {proposal.import_proposal_sha256}")
    return 0


def cmd_external_material_propose_import(args: argparse.Namespace) -> int:
    try:
        proposal = create_external_material_import_proposal(
            acquisition_path=args.acquisition,
            destination_path=args.destination_path,
            material_name=args.material_name,
            proposal_id=args.proposal_id,
        )
        proposal_sha256 = canonical_sha256(proposal)
        _write_json(proposal.model_dump(mode="json"), args.output)
    except (MaterialImportWorkflowError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "EXTERNAL MATERIAL IMPORT PROPOSAL: "
        f"asset={proposal.provider_asset_id} planned_assets={len(proposal.planned_asset_paths)} "
        f"status={proposal.status}"
    )
    print(f"APPROVAL SHA-256: {proposal_sha256}")
    return 0


def cmd_external_material_execute_import(args: argparse.Namespace) -> int:
    try:
        execution = execute_external_material_import(
            args.proposal,
            approved_proposal_sha256=args.approve_sha256,
            approved_by=args.approved_by,
            uproject_path=args.path,
            engine_root=args.engine_root,
            import_output=args.import_output,
            timeout_seconds=args.timeout,
        )
        _write_json(execution.model_dump(mode="json"), args.output)
        evidence = _sync_external_material_catalog(
            uproject_path=args.path,
            engine_root=args.engine_root,
            proposal_path=args.proposal,
            execution=execution,
            asset_catalog_path=args.asset_catalog,
            raw_scan_output=args.scan_output,
            timeout_seconds=args.timeout,
        )
        _write_json(evidence.model_dump(mode="json"), args.catalog_sync_output)
    except (
        AssetIndexError,
        MaterialCatalogSyncError,
        MaterialImportWorkflowError,
        OllamaError,
        OSError,
        UnrealAssetScanError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "EXTERNAL MATERIAL IMPORT: "
        f"material={execution.import_result.material_engine_path} "
        f"textures={len(execution.import_result.textures)} status={execution.status}"
    )
    return 0


def _sync_external_material_catalog(
    *,
    uproject_path: str,
    engine_root: str,
    proposal_path: str,
    execution,
    asset_catalog_path: str,
    raw_scan_output: str,
    timeout_seconds: int,
):
    proposal = load_external_material_import_proposal(proposal_path)
    if canonical_sha256(proposal) != execution.proposal_sha256:
        raise MaterialCatalogSyncError("execution does not match the exact import proposal")
    acquisition = load_external_material_acquisition(proposal.acquisition_path)
    if canonical_sha256(acquisition) != proposal.acquisition_sha256:
        raise MaterialCatalogSyncError("acquisition changed after approved import")
    _, scanned_cards = run_unreal_asset_scan(
        uproject_path,
        engine_root=engine_root,
        raw_output=raw_scan_output,
        limit=20,
        path_prefix=proposal.destination_path,
        timeout_seconds=timeout_seconds,
    )
    imported_cards = enrich_imported_asset_cards(
        scanned_cards,
        acquisition=acquisition,
        import_result=execution.import_result,
    )
    existing, merged = load_and_merge_asset_catalog(asset_catalog_path, imported_cards)
    settings = _settings()
    with _asset_index(settings) as index:
        index_report = index.sync(merged)
    backup = commit_asset_catalog(asset_catalog_path, merged)
    return catalog_sync_evidence(
        catalog_path=asset_catalog_path,
        backup_path=backup,
        raw_scan_path=raw_scan_output,
        before_count=len(existing),
        after_count=len(merged),
        imported_cards=imported_cards,
        index_report=index_report,
    )


def cmd_external_material_sync_import(args: argparse.Namespace) -> int:
    try:
        execution = load_material_import_execution(args.execution)
        evidence = _sync_external_material_catalog(
            uproject_path=args.path,
            engine_root=args.engine_root,
            proposal_path=args.proposal,
            execution=execution,
            asset_catalog_path=args.asset_catalog,
            raw_scan_output=args.scan_output,
            timeout_seconds=args.timeout,
        )
        _write_json(evidence.model_dump(mode="json"), args.output)
    except (
        AssetIndexError,
        MaterialCatalogSyncError,
        MaterialImportWorkflowError,
        OllamaError,
        OSError,
        UnrealAssetScanError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "EXTERNAL MATERIAL CATALOG SYNC: "
        f"before={evidence.before_count} after={evidence.after_count} "
        f"inserted={evidence.index_inserted} updated={evidence.index_updated}"
    )
    return 0


def cmd_unreal_build_scene(args: argparse.Namespace) -> int:
    try:
        request, result = run_unreal_scene_build(
            args.path,
            engine_root=args.engine_root,
            render_spec_path=args.spec,
            asset_catalog_path=args.assets,
            output=args.output,
            timeout_seconds=args.timeout,
            fail_on_warning=args.fail_on_warning,
        )
    except UnrealSceneBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "UNREAL SCENE BUILD: "
        f"project={result.project_name} "
        f"world={result.world_name} "
        f"scene={result.scene_name} "
        f"actors={len(result.actors)} "
        f"spec_sha256={request.render_spec_sha256}"
    )
    print(f"Wrote {args.output}")
    return 0


def cmd_unreal_render_preview(args: argparse.Namespace) -> int:
    try:
        manifest, result = run_unreal_preview(
            args.path,
            engine_root=args.engine_root,
            render_spec_path=args.spec,
            asset_catalog_path=args.assets,
            run_directory=args.run_dir,
            run_id=args.run_id,
            timeout_seconds=args.timeout,
            fail_on_warning=args.fail_on_warning,
        )
    except UnrealPreviewError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "UNREAL PREVIEW: "
        f"status={manifest.status} scene={result.scene_name} "
        f"actors={len(result.actors)} previews={len(result.preview_files)}"
    )
    print(f"Run directory: {Path(args.run_dir).expanduser().resolve()}")
    return 0


def cmd_asset_index(args: argparse.Namespace) -> int:
    settings = _settings()
    try:
        cards = load_asset_card_catalog(args.path)
        with _asset_index(settings) as index:
            report = index.sync(cards)
    except (AssetIndexError, OllamaError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _write_json(asdict(report), args.output)
    print(
        "ASSET INDEX: "
        f"collection={report.collection} "
        f"total={report.total} inserted={report.inserted} "
        f"updated={report.updated} deleted={report.deleted}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_asset_search(args: argparse.Namespace) -> int:
    settings = _settings()
    try:
        with _asset_index(settings) as index:
            hits = index.search(
                args.query,
                limit=args.limit,
                asset_types=args.asset_type,
            )
    except (AssetIndexError, OllamaError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _write_json([asdict(hit) for hit in hits], args.output)
    print(
        f"ASSET SEARCH: results={len(hits)} query={args.query!r}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_assistant_material_propose(args: argparse.Namespace) -> int:
    settings = _settings()
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        context = load_selection_context(args.context)
        with _asset_index(settings) as index:
            proposal = propose_material_change(
                prompt=prompt,
                context=context,
                asset_catalog_path=args.assets,
                searcher=index,
                embedding_model=settings.embedding_model,
                limit=args.limit,
                proposal_id=args.proposal_id,
            )
    except (MaterialProposalError, AssetIndexError, OllamaError, ValueError) as exc:
        print(f"ERROR: material proposal failed: {exc}", file=sys.stderr)
        return 1
    _write_json(proposal.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT MATERIAL: "
        f"status={proposal.status} target={proposal.target.actor_name} "
        f"proposal={proposal.proposal_id}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_assistant_conversation_turn(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    started = perf_counter()
    try:
        request = load_conversation_request(args.request)
        result = route_conversation_turn(
            request=request,
            client=_client(settings),
            model=model,
        )
    except (ConversationRoutingError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: assistant conversation turn failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.turn.model_dump(mode="json"), args.output)
    if args.output:
        _write_json({
            "conversation_id": result.turn.conversation_id,
            "turn_id": result.turn.turn_id,
            "configured_model": model,
            "resolved_model": result.response.model,
            "route_source": "host_rules" if result.response.model.startswith("host-rules:") else "ollama",
            "elapsed_seconds": round(perf_counter() - started, 6),
            "model_duration_ns": result.response.total_duration_ns,
            "prompt_tokens": result.response.prompt_tokens,
            "output_tokens": result.response.output_tokens,
            "done_reason": result.response.done_reason,
            "attempt_count": result.attempt_count,
        }, str(Path(args.output).with_suffix(".metrics.json")))
    print(
        "ASSISTANT CONVERSATION: "
        f"outcome={result.turn.outcome} intent={result.turn.intent} "
        f"conversation={result.turn.conversation_id} turn={result.turn.turn_id} "
        f"model={result.response.model} attempts={result.attempt_count}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_assistant_transform_propose(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        context = load_transform_context(args.context)
        result = propose_transform_change(
            prompt=prompt,
            context=context,
            client=_client(settings),
            model=model,
            proposal_id=args.proposal_id,
        )
    except (TransformProposalError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: transform proposal failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.proposal.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT TRANSFORM: "
        f"status={result.proposal.status} target={result.proposal.target.actor_name} "
        f"proposal={result.proposal.proposal_id} model={result.response.model} "
        f"attempts={result.attempt_count}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_assistant_transform_batch_propose(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        selection = load_transform_selection_context(args.context)
        result = propose_transform_batch_change(
            prompt=prompt,
            selection=selection,
            client=_client(settings),
            model=model,
            proposal_id=args.proposal_id,
        )
    except (TransformProposalError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: batch Transform proposal failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.proposal.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT TRANSFORM BATCH: "
        f"status={result.proposal.status} actors={len(result.proposal.selection.actors)} "
        f"space={result.proposal.coordinate_space} proposal={result.proposal.proposal_id} "
        f"model={result.response.model} attempts={result.attempt_count}"
    )
    return 0


def cmd_assistant_light_propose(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        context = load_light_context(args.context)
        result = propose_light_change(
            prompt=prompt,
            context=context,
            client=_client(settings),
            model=model,
            proposal_id=args.proposal_id,
        )
    except (LightProposalError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: light proposal failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.proposal.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT LIGHT: "
        f"status={result.proposal.status} target={result.proposal.target.actor_name} "
        f"kind={result.proposal.target.light_kind} "
        f"proposal={result.proposal.proposal_id} model={result.response.model} "
        f"attempts={result.attempt_count}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_assistant_light_batch_propose(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        selection = load_light_selection_context(args.context)
        result = propose_light_batch_change(
            prompt=prompt,
            selection=selection,
            client=_client(settings),
            model=model,
            proposal_id=args.proposal_id,
        )
    except (LightProposalError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: batch light proposal failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.proposal.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT LIGHT BATCH: "
        f"status={result.proposal.status} lights={len(result.proposal.selection.lights)} "
        f"proposal={result.proposal.proposal_id} model={result.response.model} "
        f"attempts={result.attempt_count}"
    )
    return 0


def cmd_assistant_lighting_rig_propose(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        context = load_lighting_rig_context(args.context)
        result = propose_lighting_rig(
            prompt=prompt,
            context=context,
            client=_client(settings),
            model=model,
            proposal_id=args.proposal_id,
        )
    except (LightingRigProposalError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: lighting-rig proposal failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.proposal.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT LIGHTING RIG: "
        f"status={result.proposal.status} lights={len(result.proposal.context.lights)} "
        f"contrast={result.proposal.contrast} palette={result.proposal.palette} "
        f"proposal={result.proposal.proposal_id} model={result.response.model} "
        f"attempts={result.attempt_count}"
    )
    return 0


def cmd_assistant_lighting_rig_review(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.vision_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        context = load_lighting_rig_review_context(args.context)
        result = propose_lighting_rig_review(
            request=prompt,
            context=context,
            preview_path=args.preview,
            client=_client(settings, num_ctx=settings.vision_num_ctx),
            model=model,
            proposal_id=args.proposal_id,
        )
    except LightingRigReviewError as exc:
        if args.raw_output and exc.response is not None:
            Path(args.raw_output).write_text(
                exc.response.content + "\n", encoding="utf-8"
            )
        print(f"ERROR: lighting-rig visual review failed: {exc}", file=sys.stderr)
        return 1
    except (OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: lighting-rig visual review failed: {exc}", file=sys.stderr)
        return 1
    if args.raw_output:
        Path(args.raw_output).write_text(
            result.response.content + "\n", encoding="utf-8"
        )
    _write_json(result.proposal.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT LIGHTING RIG REVIEW: "
        f"status={result.proposal.status} exposure={result.proposal.exposure} "
        f"fill={result.proposal.fill_balance} rim={result.proposal.rim_separation} "
        f"proposal={result.proposal.proposal_id} model={result.response.model} "
        f"attempts={result.attempt_count}"
    )
    return 0


def cmd_assistant_camera_propose(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        context = load_camera_context(args.context)
        result = propose_camera_change(
            prompt=prompt,
            context=context,
            client=_client(settings),
            model=model,
            proposal_id=args.proposal_id,
        )
    except (CameraProposalError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: camera proposal failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.proposal.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT CAMERA: "
        f"status={result.proposal.status} target={result.proposal.target.actor_name} "
        f"kind={result.proposal.target.camera_kind} "
        f"proposal={result.proposal.proposal_id} model={result.response.model} "
        f"attempts={result.attempt_count}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_assistant_camera_batch_propose(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        selection = load_camera_selection_context(args.context)
        result = propose_camera_batch_change(
            prompt=prompt,
            selection=selection,
            client=_client(settings),
            model=model,
            proposal_id=args.proposal_id,
        )
    except (CameraBatchProposalError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: camera batch proposal failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.proposal.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT CAMERA BATCH: "
        f"status={result.proposal.status} "
        f"cameras={len(result.proposal.selection.cameras)} "
        f"proposal={result.proposal.proposal_id} model={result.response.model} "
        f"attempts={result.attempt_count}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_assistant_performance_propose(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        selection = load_performance_selection_context(args.context)
        result = propose_performance_review(
            prompt=prompt,
            selection=selection,
            client=_client(settings),
            model=model,
            proposal_id=args.proposal_id,
        )
    except (PerformanceProposalError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: performance proposal failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.proposal.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT PERFORMANCE: "
        f"status={result.proposal.status} "
        f"actors={len(result.proposal.selection.actors)} "
        f"findings={len(result.proposal.findings)} "
        f"proposal={result.proposal.proposal_id} model={result.response.model} "
        f"attempts={result.attempt_count}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_assistant_level_audit(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        context = load_level_audit_context(args.context)
        if getattr(args, "evidence_only", False):
            result = build_read_only_static_triage(prompt=prompt, context=context)
        else:
            result = review_level_audit(
                prompt=prompt,
                context=context,
                client=_client(settings),
                model=model,
            )
    except (LevelAuditError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: level audit failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.report.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT LEVEL AUDIT: "
        f"status={result.report.status} "
        f"actors={result.report.statistics.captured_actor_count} "
        f"high_priority={result.report.statistics.high_priority_actor_count} "
        f"findings={len(result.report.findings)} "
        f"actions={len(result.report.actions)} "
        f"audit={result.report.audit_id} model={result.response.model} "
        f"attempts={result.attempt_count}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    if result.recovery_reason:
        print(
            f"ASSISTANT LEVEL AUDIT RECOVERY: {result.recovery_reason[:500]}",
            file=sys.stderr if args.output is None else sys.stdout,
        )
    return 0


def cmd_assistant_runtime_performance_review(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        capture = load_runtime_performance_capture(args.capture)
        result = review_runtime_performance(
            prompt=prompt,
            capture=capture,
            client=_client(settings),
            model=model,
            report_id=args.report_id,
        )
    except (RuntimePerformanceReviewError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: runtime performance review failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.report.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT RUNTIME PERFORMANCE: "
        f"status={result.report.status} "
        f"samples={result.report.capture.sample_count} "
        f"findings={len(result.report.findings)} "
        f"bottleneck={result.report.primary_bottleneck} "
        f"report={result.report.report_id} model={result.response.model} "
        f"attempts={result.attempt_count}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    if result.recovery_reason:
        print(
            "ASSISTANT RUNTIME PERFORMANCE RECOVERY: "
            f"{result.recovery_reason[:500]}",
            file=sys.stderr if args.output is None else sys.stdout,
        )
    return 0


def cmd_assistant_insights_gpu_review(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        capture = load_insights_gpu_capture(args.capture)
        result = review_insights_gpu_capture(
            prompt=prompt,
            capture=capture,
            client=_client(settings),
            model=model,
            report_id=args.report_id,
            source_file_sha256=hash_insights_gpu_capture_file(args.capture),
        )
    except (InsightsGpuReviewError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: GPU scope review failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.report.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT INSIGHTS GPU: "
        f"status={result.report.status} "
        f"queues={result.report.capture.gpu_queue_count} "
        f"scopes={len(result.report.capture.scopes)} "
        f"findings={len(result.report.findings)} "
        f"report={result.report.report_id} model={result.response.model} "
        f"attempts={result.attempt_count}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    if result.recovery_reason:
        print(
            "ASSISTANT INSIGHTS GPU RECOVERY: "
            f"{result.recovery_reason[:500]}",
            file=sys.stderr if args.output is None else sys.stdout,
        )
    return 0


def cmd_assistant_actor_gpu_impact_review(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        experiment = load_actor_gpu_impact_experiment(args.experiment)
        result = review_actor_gpu_impact(
            prompt=prompt,
            experiment=experiment,
            client=_client(settings),
            model=model,
            report_id=args.report_id,
            source_file_sha256=hash_actor_gpu_impact_file(args.experiment),
        )
    except (ActorGpuImpactReviewError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: Actor GPU impact review failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.report.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT ACTOR GPU IMPACT: "
        f"status={result.report.status} "
        f"actor={result.report.experiment.target.actor_label} "
        f"deltas={len(result.report.experiment.deltas)} "
        f"findings={len(result.report.findings)} "
        f"report={result.report.report_id} model={result.response.model} "
        f"attempts={result.attempt_count}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    if result.recovery_reason:
        print(
            "ASSISTANT ACTOR GPU IMPACT RECOVERY: "
            f"{result.recovery_reason[:500]}",
            file=sys.stderr if args.output is None else sys.stdout,
        )
    return 0


def cmd_assistant_actor_gpu_impact_study_review(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        capture_set = load_actor_gpu_impact_study_capture_set(args.capture_set)
        result = review_actor_gpu_impact_study(
            prompt=prompt,
            capture_set=capture_set,
            client=_client(settings),
            model=model,
            report_id=args.report_id,
            source_file_sha256=hash_actor_gpu_impact_study_file(args.capture_set),
        )
    except (ActorGpuImpactStudyReviewError, OllamaError, ValueError, OSError) as exc:
        print(f"ERROR: Actor GPU impact study review failed: {exc}", file=sys.stderr)
        return 1
    _write_json(result.report.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT ACTOR GPU IMPACT STUDY: "
        f"status={result.report.status} "
        f"actor={result.report.capture_set.target.actor_label} "
        f"captures={len(result.report.capture_set.sequence_capture_ids)} "
        f"aggregates={len(result.report.aggregates)} "
        f"findings={len(result.report.findings)} "
        f"report={result.report.report_id} model={result.response.model} "
        f"attempts={result.attempt_count}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    if result.recovery_reason:
        print(
            "ASSISTANT ACTOR GPU IMPACT STUDY RECOVERY: "
            f"{result.recovery_reason[:500]}",
            file=sys.stderr if args.output is None else sys.stdout,
        )
    return 0


def cmd_assistant_level_profiling(args: argparse.Namespace) -> int:
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        created_at_utc = datetime.fromisoformat(
            args.created_at_utc.replace("Z", "+00:00")
        )
        run = build_level_profiling_run(
            profiling_run_id=args.run_id,
            created_at_utc=created_at_utc,
            request=prompt,
            level_audit_report=load_level_audit_report(args.level_audit_report),
            runtime_performance_report=load_runtime_performance_report(
                args.runtime_report
            ),
            gpu_scope_report=load_gpu_scope_report(args.gpu_report),
            actor_study_reports=[
                load_actor_study_report(path) for path in args.actor_study_report
            ],
            skipped_actor_studies=load_level_profiling_skips(args.skip_manifest),
        )
        report = analyze_level_profiling_run(
            run,
            report_id=args.report_id or f"{args.run_id}_report",
        )
    except (LevelProfilingSynthesisError, ValidationError, ValueError, OSError) as exc:
        print(f"ERROR: level profiling synthesis failed: {exc}", file=sys.stderr)
        return 1
    _write_json(report.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT LEVEL PROFILING: "
        f"status={report.status} frames={report.run.runtime_performance_report.capture.sample_count} "
        f"gpu_scopes={len(report.run.gpu_scope_report.capture.scopes)} "
        f"actor_studies={report.measured_actor_count} "
        f"actor_skips={report.skipped_actor_count} "
        f"stable_candidates={report.stable_actor_candidate_count} "
        f"run={report.run.profiling_run_id}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_assistant_shadow_optimization(args: argparse.Namespace) -> int:
    try:
        report = analyze_shadow_optimization_file(
            args.capture_set,
            report_id=args.report_id,
        )
    except (ShadowOptimizationAnalysisError, ValidationError, ValueError, OSError) as exc:
        print(f"ERROR: shadow optimization analysis failed: {exc}", file=sys.stderr)
        return 1
    _write_json(report.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT SHADOW OPTIMIZATION: "
        f"status={report.status} decision={report.decision} "
        f"actor={report.capture_set.target.actor_name} "
        f"aggregates={len(report.aggregates)} "
        f"primary={report.primary_aggregate_id or 'none'} "
        f"experiment={report.capture_set.experiment_id}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_assistant_shadow_optimization_verification(
    args: argparse.Namespace,
) -> int:
    try:
        report = analyze_shadow_optimization_verification_files(
            shadow_report_path=args.shadow_report,
            baseline_report_path=args.baseline_level_report,
            candidate_report_path=args.candidate_level_report,
            report_id=args.report_id,
        )
    except (
        ShadowOptimizationVerificationError,
        ValidationError,
        ValueError,
        OSError,
    ) as exc:
        print(
            f"ERROR: shadow optimization verification failed: {exc}",
            file=sys.stderr,
        )
        return 1
    _write_json(report.model_dump(mode="json"), args.output)
    print(
        "ASSISTANT SHADOW OPTIMIZATION VERIFICATION: "
        f"status={report.status} decision={report.decision} "
        f"frame_delta_ms={report.metrics.frame_p95_delta_ms:.3f} "
        f"budget_miss_delta={report.metrics.frame_budget_miss_delta_fraction:.4f} "
        f"report={report.report_id}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    planner = ScenePlanner(_client(settings))
    asset_ids = list(dict.fromkeys(args.asset))
    asset_context = [f"{asset_id}: explicitly allowed by CLI" for asset_id in asset_ids]
    if args.retrieve_assets or args.retrieve_materials:
        try:
            with _asset_index(settings) as index:
                hits = []
                if args.retrieve_assets:
                    hits.extend(index.search(args.prompt, limit=args.retrieve_assets))
                if args.retrieve_materials:
                    hits.extend(index.search(
                        args.prompt,
                        limit=args.retrieve_materials,
                        asset_types=["material"],
                    ))
        except (AssetIndexError, OllamaError) as exc:
            print(f"ERROR: asset retrieval failed: {exc}", file=sys.stderr)
            return 1
        for hit in hits:
            if hit.asset_id not in asset_ids:
                asset_ids.append(hit.asset_id)
                asset_context.append(hit.planner_context())
        print(
            "Retrieved asset IDs: "
            + (", ".join(dict.fromkeys(hit.asset_id for hit in hits)) if hits else "none"),
            file=sys.stderr,
        )
    try:
        result = planner.plan(
            model=model,
            prompt=args.prompt,
            asset_ids=asset_ids,
            asset_context=asset_context,
        )
    except PlanningError as exc:
        if exc.response is not None:
            if args.raw_output:
                Path(args.raw_output).write_text(exc.response.content + "\n", encoding="utf-8")
                print(f"Wrote {args.raw_output}")
            if args.metrics_output:
                metrics = _response_metrics(exc.response, status="invalid", error=str(exc))
                metrics["attempt_count"] = exc.attempt_count
                _write_json(metrics, args.metrics_output)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OllamaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.raw_output:
        Path(args.raw_output).write_text(result.response.content + "\n", encoding="utf-8")
        print(f"Wrote {args.raw_output}")
    _write_json(result.spec.model_dump(mode="json"), args.output)
    metrics = _response_metrics(result.response, status="valid")
    metrics["attempt_count"] = result.attempt_count
    if args.metrics_output:
        _write_json(metrics, args.metrics_output)
    else:
        print(
            "Metrics: "
            f"model={metrics['model']} "
            f"duration={metrics['total_duration_seconds']}s "
            f"prompt_tokens={metrics['prompt_tokens']} "
            f"output_tokens={metrics['output_tokens']}"
        )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = _settings()
    planner_model = args.planner_model or settings.planner_model
    vision_model = args.vision_model or settings.vision_model
    planner_client = _client(settings)
    vision_client = _client(settings, num_ctx=settings.vision_num_ctx)
    try:
        prompt = _read_run_prompt(args.prompt, args.prompt_file)
        with _asset_index(settings) as index:
            result = run_render_workflow(
                planner_client=planner_client,
                vision_client=vision_client,
                correction_client=planner_client,
                asset_searcher=index,
                planner_model=planner_model,
                vision_model=vision_model,
                prompt=prompt,
                uproject_path=args.path,
                engine_root=args.engine_root,
                asset_catalog_path=args.assets,
                workflow_directory=args.workflow_dir,
                workflow_id=args.workflow_id,
                retrieve_assets=args.retrieve_assets,
                retrieve_materials=args.retrieve_materials,
                max_iterations=args.max_iterations,
                view_axis=args.view_axis,
                margin_fraction=args.margin,
                timeout_seconds=args.timeout,
                fail_on_warning=args.fail_on_warning,
                studio_calibration=not args.no_studio_calibration,
            )
    except (WorkflowError, AssetIndexError, OllamaError, ValueError) as exc:
        print(f"ERROR: render workflow failed: {exc}", file=sys.stderr)
        return 1
    manifest = result.manifest
    print(
        "RENDER WORKFLOW: "
        f"status={manifest.status} stop={manifest.stop_reason} "
        f"iterations={len(manifest.iterations)}/{manifest.max_iterations} "
        f"directory={Path(args.workflow_dir).expanduser().resolve()}"
    )
    return 0 if manifest.status == "succeeded" else 2


def _read_run_prompt(prompt: str | None, prompt_file: str | None) -> str:
    """Resolve one bounded UTF-8 prompt without putting panel text on a command line."""

    if prompt is not None:
        value = prompt
    elif prompt_file is not None:
        path = Path(prompt_file).expanduser().resolve()
        try:
            if not path.is_file():
                raise ValueError(f"prompt file does not exist: {path}")
            if path.stat().st_size > 64 * 1024:
                raise ValueError("prompt file exceeds the 64 KiB safety limit")
            value = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"could not read prompt file {path}: {exc}") from exc
    else:
        raise ValueError("one prompt source is required")
    value = value.strip()
    if not value:
        raise ValueError("workflow prompt cannot be empty")
    if len(value) > 4000:
        raise ValueError("workflow prompt cannot exceed 4000 characters")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="render-master")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check Ollama and list local models")
    doctor.set_defaults(handler=cmd_doctor)

    config = subparsers.add_parser("config", help="show resolved runtime configuration")
    config.set_defaults(handler=cmd_config)

    benchmark_validate = subparsers.add_parser(
        "product-benchmark-validate",
        help="validate and summarize a frozen product task suite",
    )
    benchmark_validate.add_argument("path", help="ProductBenchmarkSuite JSON file")
    benchmark_validate.add_argument("--output", "-o", help="write summary JSON")
    benchmark_validate.set_defaults(handler=cmd_product_benchmark_validate)

    evidence_validate = subparsers.add_parser(
        "capability-evidence-validate",
        help="validate and summarize a capability evidence ledger",
    )
    evidence_validate.add_argument("path", help="CapabilityEvidenceLedger JSON file")
    evidence_validate.add_argument("--output", "-o", help="write summary JSON")
    evidence_validate.set_defaults(handler=cmd_capability_evidence_validate)

    baseline_capture = subparsers.add_parser(
        "product-baseline-capture",
        help="capture auditable code, model, engine, data, and benchmark identities",
    )
    baseline_capture.add_argument("path", help="path to the target .uproject file")
    baseline_capture.add_argument("--baseline-id", required=True)
    baseline_capture.add_argument("--source-root", required=True)
    baseline_capture.add_argument("--engine-root", required=True)
    baseline_capture.add_argument("--assets", required=True, help="AssetCard catalog JSON")
    baseline_capture.add_argument(
        "--dataset",
        required=True,
        help="training dataset manifest JSON",
    )
    baseline_capture.add_argument(
        "--benchmark-suite",
        required=True,
        help="frozen ProductBenchmarkSuite JSON",
    )
    baseline_capture.add_argument(
        "--evidence-ledger",
        required=True,
        help="CapabilityEvidenceLedger JSON",
    )
    baseline_capture.add_argument("--output", "-o", required=True)
    baseline_capture.set_defaults(handler=cmd_product_baseline_capture)

    schema = subparsers.add_parser("schema", help="print a public contract JSON Schema")
    schema.add_argument("contract", nargs="?", default="render-spec", choices=CONTRACT_MODELS)
    schema.add_argument("--output", "-o")
    schema.set_defaults(handler=cmd_schema)

    validate = subparsers.add_parser("validate", help="validate a public contract JSON file")
    validate.add_argument("path")
    validate.add_argument("--contract", default="render-spec", choices=CONTRACT_MODELS)
    validate.set_defaults(handler=cmd_validate)

    preflight = subparsers.add_parser(
        "preflight",
        help="run deterministic semantic checks before invoking a renderer",
    )
    preflight.add_argument("path", help="RenderSpec JSON file")
    preflight.add_argument("--output", "-o", help="write an EvaluationReport JSON file")
    preflight.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="return exit code 2 when the verdict is needs_review",
    )
    preflight.set_defaults(handler=cmd_preflight)

    apply_patch_parser = subparsers.add_parser(
        "apply-patch",
        help="apply a hash-bound RenderSpecPatch and write a new RenderSpec",
    )
    apply_patch_parser.add_argument("path", help="source RenderSpec JSON file")
    apply_patch_parser.add_argument("--patch", required=True, help="RenderSpecPatch JSON file")
    apply_patch_parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="write the corrected RenderSpec without modifying the source",
    )
    apply_patch_parser.set_defaults(handler=cmd_apply_patch)

    frame_camera_parser = subparsers.add_parser(
        "frame-camera",
        help="create an auditable camera-framing patch from AssetCard bounds",
    )
    frame_camera_parser.add_argument("path", help="source RenderSpec JSON file")
    frame_camera_parser.add_argument(
        "--assets",
        required=True,
        help="validated AssetCard JSON array supplying object bounds",
    )
    frame_camera_parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="write the newly framed RenderSpec without modifying the source",
    )
    frame_camera_parser.add_argument(
        "--patch-output",
        required=True,
        help="write the exact RenderSpecPatch used to produce the framed spec",
    )
    frame_camera_parser.add_argument(
        "--margin",
        type=float,
        default=0.1,
        help="fractional safety margin on each image edge (default: 0.1)",
    )
    frame_camera_parser.add_argument(
        "--view-axis",
        choices=VIEW_AXES,
        default="preserve",
        help="preserve the current view or frame from an explicit Unreal world axis",
    )
    frame_camera_parser.set_defaults(handler=cmd_frame_camera)

    evaluate_preview = subparsers.add_parser(
        "evaluate-preview",
        help="evaluate a verified Unreal run with the configured local vision model",
    )
    evaluate_preview.add_argument("run_dir", help="completed Unreal preview run directory")
    evaluate_preview.add_argument("--model", help="override the configured vision model")
    evaluate_preview.add_argument(
        "--output",
        default="evaluation.json",
        help="run-relative EvaluationReport output path",
    )
    evaluate_preview.add_argument(
        "--metrics-output",
        default="evaluation_metrics.json",
        help="run-relative model metrics output path",
    )
    evaluate_preview.set_defaults(handler=cmd_evaluate_preview)

    benchmark_evaluator = subparsers.add_parser(
        "benchmark-evaluator",
        help="benchmark the vision evaluator on labeled, verified Unreal preview runs",
    )
    benchmark_evaluator.add_argument("path", help="VisualBenchmarkSuite JSON file")
    benchmark_evaluator.add_argument("--model", help="override the configured vision model")
    benchmark_evaluator.add_argument(
        "--output",
        "-o",
        required=True,
        help="write the VisualBenchmarkReport JSON file",
    )
    benchmark_evaluator.set_defaults(handler=cmd_benchmark_evaluator)

    correction = subparsers.add_parser(
        "plan-correction",
        help="plan a bounded RenderSpec patch or report a missing capability",
    )
    correction.add_argument("run_dir", help="evaluated Unreal preview run directory")
    correction.add_argument(
        "--evaluation",
        default="evaluation.json",
        help="run-relative EvaluationReport input path",
    )
    correction.add_argument("--model", help="override the configured correction model")
    correction.add_argument(
        "--output",
        default="correction.json",
        help="run-relative CorrectionDecision output path",
    )
    correction.add_argument(
        "--metrics-output",
        default="correction_metrics.json",
        help="run-relative model metrics output path",
    )
    correction.add_argument(
        "--corrected-output",
        default="corrected_render_spec.json",
        help="run-relative corrected RenderSpec output when outcome is patch",
    )
    correction.set_defaults(handler=cmd_plan_correction)

    unreal_probe = subparsers.add_parser(
        "unreal-probe",
        help="inspect an Unreal project and emit a CapabilityManifest",
    )
    unreal_probe.add_argument("path", help="path to a .uproject file")
    unreal_probe.add_argument(
        "--engine-root",
        help="explicit Unreal installation root containing the Engine directory",
    )
    unreal_probe.add_argument("--output", "-o", help="write CapabilityManifest JSON")
    unreal_probe.set_defaults(handler=cmd_unreal_probe)

    unreal_scan = subparsers.add_parser(
        "unreal-scan-assets",
        help="run Unreal headlessly and export validated AssetCard records",
    )
    unreal_scan.add_argument("path", help="path to a compiled .uproject file")
    unreal_scan.add_argument(
        "--engine-root",
        required=True,
        help="Unreal installation root containing the Engine directory",
    )
    unreal_scan.add_argument(
        "--raw-output",
        required=True,
        help="write the unconverted Unreal Asset Registry JSON",
    )
    unreal_scan.add_argument("--output", "-o", help="write validated AssetCard JSON array")
    unreal_scan.add_argument("--limit", type=int, default=20)
    unreal_scan.add_argument("--path-prefix", default="/Game")
    unreal_scan.add_argument("--timeout", type=int, default=300, help="Unreal timeout in seconds")
    unreal_scan.set_defaults(handler=cmd_unreal_scan_assets)

    unreal_material = subparsers.add_parser(
        "unreal-import-pbr-material",
        help="import four PBR textures and create a connected Unreal material",
    )
    unreal_material.add_argument("path", help="path to a compiled .uproject file")
    unreal_material.add_argument(
        "--engine-root",
        required=True,
        help="Unreal installation root containing the Engine directory",
    )
    unreal_material.add_argument(
        "--destination-path",
        required=True,
        help="new Unreal content folder such as /Game/RenderMasterBot/TestMaterials/Wood",
    )
    unreal_material.add_argument("--material-name", required=True)
    unreal_material.add_argument("--base-color", required=True, help="color texture image")
    unreal_material.add_argument("--normal", required=True, help="DirectX normal texture image")
    unreal_material.add_argument("--roughness", required=True, help="roughness texture image")
    unreal_material.add_argument(
        "--ambient-occlusion",
        required=True,
        help="ambient-occlusion texture image",
    )
    unreal_material.add_argument(
        "--output",
        "-o",
        required=True,
        help="write validated material-import evidence",
    )
    unreal_material.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Unreal timeout in seconds",
    )
    unreal_material.set_defaults(handler=cmd_unreal_import_pbr_material)

    material_parameters = subparsers.add_parser(
        "unreal-material-parameters",
        help="inspect scalar and vector parameters exposed by one Unreal material",
    )
    material_parameters.add_argument("path", help="path to a compiled .uproject file")
    material_parameters.add_argument("--engine-root", required=True)
    material_parameters.add_argument("--material", required=True, help="/Game material path")
    material_parameters.add_argument("--output", "-o", required=True)
    material_parameters.add_argument("--timeout", type=int, default=300)
    material_parameters.set_defaults(handler=cmd_unreal_material_parameters)

    material_variant = subparsers.add_parser(
        "unreal-create-material-variant",
        help="create a non-overwriting MaterialInstanceConstant from explicit parameters",
    )
    material_variant.add_argument("path", help="path to a compiled .uproject file")
    material_variant.add_argument("--engine-root", required=True)
    material_variant.add_argument("--parent-material", required=True)
    material_variant.add_argument("--destination-path", required=True)
    material_variant.add_argument("--instance-name", required=True)
    material_variant.add_argument(
        "--scalar",
        action="append",
        default=[],
        metavar="NAME=VALUE",
    )
    material_variant.add_argument(
        "--vector",
        action="append",
        default=[],
        metavar="NAME=R,G,B[,A]",
    )
    material_variant.add_argument("--output", "-o", required=True)
    material_variant.add_argument("--timeout", type=int, default=300)
    material_variant.set_defaults(handler=cmd_unreal_create_material_variant)

    external_material = subparsers.add_parser(
        "external-material-search",
        help="search official CC0 Poly Haven textures with local embeddings",
    )
    external_material.add_argument("--query", required=True)
    external_material.add_argument("--limit", type=int, default=5)
    external_material.add_argument("--timeout", type=float, default=30.0)
    external_material.add_argument("--output", "-o", required=True)
    external_material.set_defaults(handler=cmd_external_material_search)

    acquire_material = subparsers.add_parser(
        "external-material-acquire",
        help="download one selected Poly Haven material with MD5 and SHA-256 evidence",
    )
    acquire_material.add_argument("search_report")
    acquire_material.add_argument("--asset-id", required=True)
    acquire_material.add_argument("--destination-root", required=True)
    acquire_material.add_argument("--resolution", choices=("1k", "2k", "4k", "8k"), default="1k")
    acquire_material.add_argument("--image-format", choices=("jpg", "png"), default="jpg")
    acquire_material.add_argument("--timeout", type=float, default=60.0)
    acquire_material.add_argument("--output", "-o", required=True)
    acquire_material.set_defaults(handler=cmd_external_material_acquire)

    assistant_external_material = subparsers.add_parser(
        "assistant-external-material-prepare",
        help="search and cache one CC0 Poly Haven candidate for approval in the Editor",
    )
    assistant_external_prompt = assistant_external_material.add_mutually_exclusive_group(
        required=True
    )
    assistant_external_prompt.add_argument("--prompt")
    assistant_external_prompt.add_argument("--prompt-file")
    assistant_external_material.add_argument("--library-root", required=True)
    assistant_external_material.add_argument("--work-dir", required=True)
    assistant_external_material.add_argument("--proposal-id", required=True)
    assistant_external_material.add_argument(
        "--resolution", choices=("1k", "2k", "4k", "8k"), default="1k"
    )
    assistant_external_material.add_argument(
        "--image-format", choices=("jpg", "png"), default="jpg"
    )
    assistant_external_material.add_argument("--timeout", type=float, default=60.0)
    assistant_external_material.add_argument("--output", "-o", required=True)
    assistant_external_material.set_defaults(handler=cmd_assistant_external_material_prepare)

    propose_material_import = subparsers.add_parser(
        "external-material-propose-import",
        help="freeze a five-asset Unreal import proposal without modifying the project",
    )
    propose_material_import.add_argument("acquisition")
    propose_material_import.add_argument("--destination-path", required=True)
    propose_material_import.add_argument("--material-name", required=True)
    propose_material_import.add_argument("--proposal-id", required=True)
    propose_material_import.add_argument("--output", "-o", required=True)
    propose_material_import.set_defaults(handler=cmd_external_material_propose_import)

    execute_material_import = subparsers.add_parser(
        "external-material-execute-import",
        help="execute one exact approved external-material proposal in Unreal",
    )
    execute_material_import.add_argument("path", help="path to a compiled .uproject file")
    execute_material_import.add_argument("--engine-root", required=True)
    execute_material_import.add_argument("--proposal", required=True)
    execute_material_import.add_argument(
        "--approve-sha256",
        required=True,
        help="exact SHA-256 printed when the immutable proposal was created",
    )
    execute_material_import.add_argument("--approved-by", default="local_operator")
    execute_material_import.add_argument("--import-output", required=True)
    execute_material_import.add_argument("--asset-catalog", required=True)
    execute_material_import.add_argument("--scan-output", required=True)
    execute_material_import.add_argument("--catalog-sync-output", required=True)
    execute_material_import.add_argument("--output", "-o", required=True)
    execute_material_import.add_argument("--timeout", type=int, default=300)
    execute_material_import.set_defaults(handler=cmd_external_material_execute_import)

    sync_material_import = subparsers.add_parser(
        "external-material-sync-import",
        help="resume catalog and Chroma sync after a completed approved Unreal import",
    )
    sync_material_import.add_argument("path", help="path to a compiled .uproject file")
    sync_material_import.add_argument("--engine-root", required=True)
    sync_material_import.add_argument("--proposal", required=True)
    sync_material_import.add_argument("--execution", required=True)
    sync_material_import.add_argument("--asset-catalog", required=True)
    sync_material_import.add_argument("--scan-output", required=True)
    sync_material_import.add_argument("--output", "-o", required=True)
    sync_material_import.add_argument("--timeout", type=int, default=300)
    sync_material_import.set_defaults(handler=cmd_external_material_sync_import)

    unreal_build = subparsers.add_parser(
        "unreal-build-scene",
        help="build a validated RenderSpec as transient actors inside Unreal",
    )
    unreal_build.add_argument("path", help="path to a compiled .uproject file")
    unreal_build.add_argument(
        "--engine-root",
        required=True,
        help="Unreal installation root containing the Engine directory",
    )
    unreal_build.add_argument("--spec", required=True, help="validated RenderSpec JSON file")
    unreal_build.add_argument(
        "--assets",
        required=True,
        help="validated AssetCard JSON array used to resolve asset IDs",
    )
    unreal_build.add_argument(
        "--output",
        "-o",
        required=True,
        help="write validated transient scene-build evidence",
    )
    unreal_build.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Unreal timeout in seconds",
    )
    unreal_build.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="refuse to launch Unreal when semantic preflight needs review",
    )
    unreal_build.set_defaults(handler=cmd_unreal_build_scene)

    unreal_preview = subparsers.add_parser(
        "unreal-render-preview",
        help="build a temporary scene and render one PNG with Movie Render Pipeline",
    )
    unreal_preview.add_argument("path", help="path to a compiled .uproject file")
    unreal_preview.add_argument(
        "--engine-root",
        required=True,
        help="Unreal installation root containing the Engine directory",
    )
    unreal_preview.add_argument("--spec", required=True, help="validated RenderSpec JSON file")
    unreal_preview.add_argument(
        "--assets",
        required=True,
        help="validated AssetCard JSON array used to resolve asset IDs",
    )
    unreal_preview.add_argument(
        "--run-dir",
        required=True,
        help="new or empty directory for inputs, preview, evidence, and RunManifest",
    )
    unreal_preview.add_argument(
        "--run-id",
        required=True,
        help="stable lowercase run identifier",
    )
    unreal_preview.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Unreal render timeout in seconds",
    )
    unreal_preview.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="refuse to launch Unreal when semantic preflight needs review",
    )
    unreal_preview.set_defaults(handler=cmd_unreal_render_preview)

    asset_index = subparsers.add_parser(
        "asset-index",
        help="synchronize a validated AssetCard array into local Chroma",
    )
    asset_index.add_argument("path", help="AssetCard JSON array")
    asset_index.add_argument("--output", "-o", help="write index synchronization report")
    asset_index.set_defaults(handler=cmd_asset_index)

    asset_search = subparsers.add_parser(
        "asset-search",
        help="semantically search the local AssetCard collection",
    )
    asset_search.add_argument("--query", required=True)
    asset_search.add_argument("--limit", type=int, default=5)
    asset_search.add_argument(
        "--asset-type",
        action="append",
        choices=sorted(ASSET_TYPES),
        help="restrict results to this asset type; repeatable",
    )
    asset_search.add_argument("--output", "-o", help="write ranked retrieval results")
    asset_search.set_defaults(handler=cmd_asset_search)

    assistant_conversation = subparsers.add_parser(
        "assistant-conversation-turn",
        help="route one multi-turn Unreal Editor conversation request",
    )
    assistant_conversation.add_argument(
        "--request",
        required=True,
        help="AssistantConversationRequest JSON captured by the Editor plugin",
    )
    assistant_conversation.add_argument(
        "--model",
        help="override the configured planner model",
    )
    assistant_conversation.add_argument("--output", "-o", required=True)
    assistant_conversation.set_defaults(handler=cmd_assistant_conversation_turn)

    assistant_material = subparsers.add_parser(
        "assistant-material-propose",
        help="propose one catalog-backed material change for selected Unreal evidence",
    )
    assistant_prompt_source = assistant_material.add_mutually_exclusive_group(required=True)
    assistant_prompt_source.add_argument("--prompt", help="natural-language material request")
    assistant_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the material request",
    )
    assistant_material.add_argument(
        "--context",
        required=True,
        help="UnrealSelectionContext JSON captured by the Editor plugin",
    )
    assistant_material.add_argument(
        "--assets",
        required=True,
        help="validated complete AssetCard JSON catalog",
    )
    assistant_material.add_argument("--limit", type=int, default=5)
    assistant_material.add_argument("--proposal-id", default="material_proposal")
    assistant_material.add_argument("--output", "-o", required=True)
    assistant_material.set_defaults(handler=cmd_assistant_material_propose)

    assistant_transform = subparsers.add_parser(
        "assistant-transform-propose",
        help="propose one bounded world-space Transform change for a selected Actor",
    )
    transform_prompt_source = assistant_transform.add_mutually_exclusive_group(
        required=True
    )
    transform_prompt_source.add_argument(
        "--prompt", help="natural-language Transform request"
    )
    transform_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the Transform request",
    )
    assistant_transform.add_argument(
        "--context",
        required=True,
        help="UnrealActorTransformContext JSON captured by the Editor plugin",
    )
    assistant_transform.add_argument(
        "--model", help="override the configured planner model"
    )
    assistant_transform.add_argument("--proposal-id", default="transform_proposal")
    assistant_transform.add_argument("--output", "-o", required=True)
    assistant_transform.set_defaults(handler=cmd_assistant_transform_propose)

    assistant_transform_batch = subparsers.add_parser(
        "assistant-transform-batch-propose",
        help="propose one bounded world/local Transform change for an Actor selection",
    )
    transform_batch_prompt_source = assistant_transform_batch.add_mutually_exclusive_group(
        required=True
    )
    transform_batch_prompt_source.add_argument(
        "--prompt", help="natural-language Transform request for the full selection"
    )
    transform_batch_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the Transform request",
    )
    assistant_transform_batch.add_argument(
        "--context",
        required=True,
        help="UnrealTransformSelectionContext JSON captured by the Editor plugin",
    )
    assistant_transform_batch.add_argument(
        "--model", help="override the configured planner model"
    )
    assistant_transform_batch.add_argument(
        "--proposal-id", default="transform_batch_proposal"
    )
    assistant_transform_batch.add_argument("--output", "-o", required=True)
    assistant_transform_batch.set_defaults(
        handler=cmd_assistant_transform_batch_propose
    )

    assistant_light = subparsers.add_parser(
        "assistant-light-propose",
        help="propose one bounded property change for a selected Unreal light",
    )
    light_prompt_source = assistant_light.add_mutually_exclusive_group(required=True)
    light_prompt_source.add_argument(
        "--prompt", help="natural-language light property request"
    )
    light_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the light property request",
    )
    assistant_light.add_argument(
        "--context",
        required=True,
        help="UnrealLightContext JSON captured by the Editor plugin",
    )
    assistant_light.add_argument(
        "--model", help="override the configured planner model"
    )
    assistant_light.add_argument("--proposal-id", default="light_proposal")
    assistant_light.add_argument("--output", "-o", required=True)
    assistant_light.set_defaults(handler=cmd_assistant_light_propose)

    assistant_light_batch = subparsers.add_parser(
        "assistant-light-batch-propose",
        help="propose one bounded property change for a selected Unreal light group",
    )
    light_batch_prompt_source = assistant_light_batch.add_mutually_exclusive_group(
        required=True
    )
    light_batch_prompt_source.add_argument(
        "--prompt", help="natural-language light request for the full selection"
    )
    light_batch_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the light property request",
    )
    assistant_light_batch.add_argument(
        "--context",
        required=True,
        help="UnrealLightSelectionContext JSON captured by the Editor plugin",
    )
    assistant_light_batch.add_argument(
        "--model", help="override the configured planner model"
    )
    assistant_light_batch.add_argument(
        "--proposal-id", default="light_batch_proposal"
    )
    assistant_light_batch.add_argument("--output", "-o", required=True)
    assistant_light_batch.set_defaults(handler=cmd_assistant_light_batch_propose)

    assistant_lighting_rig = subparsers.add_parser(
        "assistant-lighting-rig-propose",
        help="propose one bounded Key/Fill/Rim rig for a frozen subject, camera, and three lights",
    )
    lighting_rig_prompt_source = assistant_lighting_rig.add_mutually_exclusive_group(
        required=True
    )
    lighting_rig_prompt_source.add_argument(
        "--prompt", help="natural-language three-point-lighting request"
    )
    lighting_rig_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the three-point-lighting request",
    )
    assistant_lighting_rig.add_argument(
        "--context",
        required=True,
        help="UnrealLightingRigContext JSON captured by the Editor plugin",
    )
    assistant_lighting_rig.add_argument(
        "--model", help="override the configured planner model"
    )
    assistant_lighting_rig.add_argument(
        "--proposal-id", default="lighting_rig_proposal"
    )
    assistant_lighting_rig.add_argument("--output", "-o", required=True)
    assistant_lighting_rig.set_defaults(handler=cmd_assistant_lighting_rig_propose)

    assistant_lighting_review = subparsers.add_parser(
        "assistant-lighting-rig-review",
        help="visually review an applied Key/Fill/Rim rig and propose one bounded intensity correction",
    )
    lighting_review_prompt_source = (
        assistant_lighting_review.add_mutually_exclusive_group(required=True)
    )
    lighting_review_prompt_source.add_argument(
        "--prompt", help="natural-language visual review request"
    )
    lighting_review_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the visual review request",
    )
    assistant_lighting_review.add_argument(
        "--context",
        required=True,
        help="UnrealLightingRigReviewContext JSON captured after rig approval",
    )
    assistant_lighting_review.add_argument(
        "--preview",
        required=True,
        help="camera-view PNG captured by the Unreal Editor plugin",
    )
    assistant_lighting_review.add_argument(
        "--model", help="override the configured vision model"
    )
    assistant_lighting_review.add_argument(
        "--proposal-id", default="lighting_rig_review"
    )
    assistant_lighting_review.add_argument(
        "--raw-output", help="save the vision model's final unvalidated response"
    )
    assistant_lighting_review.add_argument("--output", "-o", required=True)
    assistant_lighting_review.set_defaults(
        handler=cmd_assistant_lighting_rig_review
    )

    assistant_camera = subparsers.add_parser(
        "assistant-camera-propose",
        help="propose one bounded property change for a selected Unreal camera",
    )
    camera_prompt_source = assistant_camera.add_mutually_exclusive_group(required=True)
    camera_prompt_source.add_argument(
        "--prompt", help="natural-language camera property request"
    )
    camera_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the camera property request",
    )
    assistant_camera.add_argument(
        "--context",
        required=True,
        help="UnrealCameraContext JSON captured by the Editor plugin",
    )
    assistant_camera.add_argument(
        "--model", help="override the configured planner model"
    )
    assistant_camera.add_argument("--proposal-id", default="camera_proposal")
    assistant_camera.add_argument("--output", "-o", required=True)
    assistant_camera.set_defaults(handler=cmd_assistant_camera_propose)

    assistant_camera_batch = subparsers.add_parser(
        "assistant-camera-batch-propose",
        help="propose one coordinated bounded edit for 2-16 selected Unreal cameras",
    )
    camera_batch_prompt_source = (
        assistant_camera_batch.add_mutually_exclusive_group(required=True)
    )
    camera_batch_prompt_source.add_argument(
        "--prompt", help="natural-language coordinated camera request"
    )
    camera_batch_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the coordinated camera request",
    )
    assistant_camera_batch.add_argument(
        "--context",
        required=True,
        help="UnrealCameraSelectionContext JSON captured by the Editor plugin",
    )
    assistant_camera_batch.add_argument(
        "--model", help="override the configured planner model"
    )
    assistant_camera_batch.add_argument(
        "--proposal-id", default="camera_batch_proposal"
    )
    assistant_camera_batch.add_argument("--output", "-o", required=True)
    assistant_camera_batch.set_defaults(
        handler=cmd_assistant_camera_batch_propose
    )

    assistant_performance = subparsers.add_parser(
        "assistant-performance-propose",
        help="review measured evidence for 1-32 selected Unreal StaticMeshActors",
    )
    performance_prompt_source = assistant_performance.add_mutually_exclusive_group(
        required=True
    )
    performance_prompt_source.add_argument(
        "--prompt", help="natural-language performance review or safe-action request"
    )
    performance_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the performance request",
    )
    assistant_performance.add_argument(
        "--context",
        required=True,
        help="UnrealPerformanceSelectionContext JSON captured by the Editor plugin",
    )
    assistant_performance.add_argument(
        "--model", help="override the configured planner model"
    )
    assistant_performance.add_argument(
        "--proposal-id", default="performance_proposal"
    )
    assistant_performance.add_argument("--output", "-o", required=True)
    assistant_performance.set_defaults(handler=cmd_assistant_performance_propose)

    assistant_level_audit = subparsers.add_parser(
        "assistant-level-audit",
        help="review a deterministic static audit of one complete Unreal level",
    )
    level_audit_prompt_source = assistant_level_audit.add_mutually_exclusive_group(
        required=True
    )
    level_audit_prompt_source.add_argument(
        "--prompt", help="whole-level audit or bounded safe-action request"
    )
    level_audit_prompt_source.add_argument(
        "--prompt-file", help="UTF-8 text file containing the audit request"
    )
    assistant_level_audit.add_argument(
        "--context",
        required=True,
        help="UnrealLevelAuditContext JSON captured by the Editor plugin",
    )
    assistant_level_audit.add_argument(
        "--model", help="override the configured planner model"
    )
    assistant_level_audit.add_argument("--output", "-o", required=True)
    assistant_level_audit.add_argument("--evidence-only", action="store_true", help="host-computed read-only triage without model inference")
    assistant_level_audit.set_defaults(handler=cmd_assistant_level_audit)

    assistant_runtime_performance = subparsers.add_parser(
        "assistant-runtime-performance-review",
        help="review a host-measured Unreal PIE/SIE runtime capture",
    )
    runtime_performance_prompt_source = (
        assistant_runtime_performance.add_mutually_exclusive_group(required=True)
    )
    runtime_performance_prompt_source.add_argument(
        "--prompt", help="natural-language runtime performance question"
    )
    runtime_performance_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the runtime performance question",
    )
    assistant_runtime_performance.add_argument(
        "--capture",
        required=True,
        help="UnrealRuntimePerformanceCapture JSON emitted by the Editor plugin",
    )
    assistant_runtime_performance.add_argument(
        "--model", help="override the configured planner model"
    )
    assistant_runtime_performance.add_argument(
        "--report-id", default="runtime_performance_report"
    )
    assistant_runtime_performance.add_argument("--output", "-o", required=True)
    assistant_runtime_performance.set_defaults(
        handler=cmd_assistant_runtime_performance_review
    )

    assistant_insights_gpu = subparsers.add_parser(
        "assistant-insights-gpu-review",
        help="review Unreal Insights GPU scope evidence parsed by the Editor plugin",
    )
    insights_gpu_prompt_source = assistant_insights_gpu.add_mutually_exclusive_group(
        required=True
    )
    insights_gpu_prompt_source.add_argument(
        "--prompt", help="natural-language GPU scope performance question"
    )
    insights_gpu_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the GPU scope question",
    )
    assistant_insights_gpu.add_argument(
        "--capture",
        required=True,
        help="UnrealInsightsGpuCapture JSON emitted by the Editor plugin",
    )
    assistant_insights_gpu.add_argument(
        "--model", help="override the configured planner model"
    )
    assistant_insights_gpu.add_argument(
        "--report-id", default="insights_gpu_report"
    )
    assistant_insights_gpu.add_argument("--output", "-o", required=True)
    assistant_insights_gpu.set_defaults(handler=cmd_assistant_insights_gpu_review)

    assistant_actor_gpu_impact = subparsers.add_parser(
        "assistant-actor-gpu-impact-review",
        help="review a selected-Actor baseline versus runtime-hidden GPU experiment",
    )
    actor_gpu_prompt_source = (
        assistant_actor_gpu_impact.add_mutually_exclusive_group(required=True)
    )
    actor_gpu_prompt_source.add_argument(
        "--prompt", help="natural-language selected-Actor GPU impact question"
    )
    actor_gpu_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the selected-Actor GPU impact question",
    )
    assistant_actor_gpu_impact.add_argument(
        "--experiment",
        required=True,
        help="UnrealActorGpuImpactExperiment JSON emitted by the Editor plugin",
    )
    assistant_actor_gpu_impact.add_argument(
        "--model", help="override the configured planner model"
    )
    assistant_actor_gpu_impact.add_argument(
        "--report-id", default="actor_gpu_impact_report"
    )
    assistant_actor_gpu_impact.add_argument("--output", "-o", required=True)
    assistant_actor_gpu_impact.set_defaults(
        handler=cmd_assistant_actor_gpu_impact_review
    )

    assistant_actor_gpu_study = subparsers.add_parser(
        "assistant-actor-gpu-impact-study-review",
        help="review an AA plus counterbalanced AB/BA/AB selected-Actor GPU study",
    )
    actor_gpu_study_prompt_source = (
        assistant_actor_gpu_study.add_mutually_exclusive_group(required=True)
    )
    actor_gpu_study_prompt_source.add_argument(
        "--prompt", help="natural-language repeatable Actor GPU impact question"
    )
    actor_gpu_study_prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 file containing the repeatable Actor GPU impact question",
    )
    assistant_actor_gpu_study.add_argument(
        "--capture-set",
        required=True,
        help="UnrealActorGpuImpactStudyCaptureSet JSON emitted by the Editor plugin",
    )
    assistant_actor_gpu_study.add_argument(
        "--model", help="override the configured planner model"
    )
    assistant_actor_gpu_study.add_argument(
        "--report-id", default="actor_gpu_impact_study_report"
    )
    assistant_actor_gpu_study.add_argument("--output", "-o", required=True)
    assistant_actor_gpu_study.set_defaults(
        handler=cmd_assistant_actor_gpu_impact_study_review
    )

    assistant_level_profiling = subparsers.add_parser(
        "assistant-level-profiling",
        help="synthesize one profiling-first whole-level Unreal audit",
    )
    level_profiling_prompt_source = (
        assistant_level_profiling.add_mutually_exclusive_group(required=True)
    )
    level_profiling_prompt_source.add_argument(
        "--prompt", help="profiling-first whole-level analysis request"
    )
    level_profiling_prompt_source.add_argument(
        "--prompt-file", help="UTF-8 file containing the profiling request"
    )
    assistant_level_profiling.add_argument("--run-id", required=True)
    assistant_level_profiling.add_argument("--created-at-utc", required=True)
    assistant_level_profiling.add_argument("--level-audit-report", required=True)
    assistant_level_profiling.add_argument("--runtime-report", required=True)
    assistant_level_profiling.add_argument("--gpu-report", required=True)
    assistant_level_profiling.add_argument(
        "--actor-study-report", action="append", default=[]
    )
    assistant_level_profiling.add_argument("--skip-manifest")
    assistant_level_profiling.add_argument("--report-id")
    assistant_level_profiling.add_argument("--output", "-o", required=True)
    assistant_level_profiling.set_defaults(handler=cmd_assistant_level_profiling)

    assistant_shadow_optimization = subparsers.add_parser(
        "assistant-shadow-optimization",
        help="analyze an eight-trace Cast Shadow optimization experiment",
    )
    assistant_shadow_optimization.add_argument(
        "--capture-set",
        required=True,
        help="UnrealShadowOptimizationCaptureSet JSON emitted by the Editor plugin",
    )
    assistant_shadow_optimization.add_argument("--report-id")
    assistant_shadow_optimization.add_argument("--output", "-o", required=True)
    assistant_shadow_optimization.set_defaults(
        handler=cmd_assistant_shadow_optimization
    )

    assistant_shadow_verification = subparsers.add_parser(
        "assistant-shadow-optimization-verification",
        help="compare a fresh whole-level run after an approved shadow optimization",
    )
    assistant_shadow_verification.add_argument("--shadow-report", required=True)
    assistant_shadow_verification.add_argument(
        "--baseline-level-report", required=True
    )
    assistant_shadow_verification.add_argument(
        "--candidate-level-report", required=True
    )
    assistant_shadow_verification.add_argument("--report-id")
    assistant_shadow_verification.add_argument("--output", "-o", required=True)
    assistant_shadow_verification.set_defaults(
        handler=cmd_assistant_shadow_optimization_verification
    )

    plan = subparsers.add_parser("plan", help="ask a local Ollama model for a RenderSpec")
    plan.add_argument("--model", help="override the configured planner model")
    plan.add_argument("--prompt", required=True)
    plan.add_argument("--asset", action="append", default=[], help="allowed asset ID; repeatable")
    plan.add_argument(
        "--retrieve-assets",
        type=int,
        default=0,
        metavar="N",
        help="retrieve N semantic matches and restrict the planner to those asset IDs",
    )
    plan.add_argument(
        "--retrieve-materials",
        type=int,
        default=0,
        metavar="N",
        help="also retrieve N material-only matches for bounded material assignment",
    )
    plan.add_argument("--output", "-o")
    plan.add_argument("--raw-output", help="save the model's unvalidated response")
    plan.add_argument("--metrics-output", help="write timing and token metrics as JSON")
    plan.set_defaults(handler=cmd_plan)

    training_data = subparsers.add_parser(
        "training-data",
        help="build or audit private, workflow-derived model training data",
    )
    training_commands = training_data.add_subparsers(
        dest="training_data_command",
        required=True,
    )
    training_build = training_commands.add_parser(
        "build",
        help="create deterministic grouped JSONL splits from workflow evidence",
    )
    training_build.add_argument(
        "--source-root",
        required=True,
        help="local data root containing workflow_manifest.json evidence",
    )
    training_build.add_argument(
        "--output-dir",
        required=True,
        help="new or empty private directory for the generated dataset",
    )
    training_build.add_argument("--dataset-id", default=DEFAULT_DATASET_ID)
    training_build.add_argument("--split-seed", default=DEFAULT_SPLIT_SEED)
    training_build.add_argument("--validation-fraction", type=float, default=0.1)
    training_build.add_argument("--test-fraction", type=float, default=0.1)
    training_build.add_argument(
        "--campaign-run",
        help="optional completed campaign_manifest.json eligibility allow-list",
    )
    training_build.add_argument(
        "--output",
        help="optional JSON path for the compact build summary",
    )
    training_build.set_defaults(handler=cmd_training_data_build)
    training_merge = training_commands.add_parser(
        "merge",
        help="rebuild one audited dataset from explicitly selected completed campaigns",
    )
    training_merge.add_argument("--source-root", required=True, help="common private campaigns root")
    training_merge.add_argument("--output-dir", required=True, help="new or empty dataset directory")
    training_merge.add_argument("--dataset-id", default=DEFAULT_DATASET_ID)
    training_merge.add_argument("--split-seed", default=DEFAULT_SPLIT_SEED)
    training_merge.add_argument("--validation-fraction", type=float, default=0.1)
    training_merge.add_argument("--test-fraction", type=float, default=0.1)
    training_merge.add_argument(
        "--campaign-run", dest="campaign_runs", action="append", required=True,
        help="completed campaign_manifest.json; repeat for each campaign to include",
    )
    training_merge.add_argument("--output", help="optional compact build summary JSON")
    training_merge.set_defaults(handler=cmd_training_data_build)
    training_audit = training_commands.add_parser(
        "audit",
        help="verify hashes, contracts, split membership, leakage, and readiness",
    )
    training_audit.add_argument("manifest", help="generated dataset_manifest.json")
    training_audit.add_argument(
        "--source-root", help="also rehash original evidence and reproduce each training example",
    )
    training_audit.add_argument("--output", "-o", help="optional audit report JSON path")
    training_audit.set_defaults(handler=cmd_training_data_audit)

    training_reference = subparsers.add_parser(
        "training-reference",
        help="validate and render non-model-authored Golden Reference targets",
    )
    reference_commands = training_reference.add_subparsers(
        dest="training_reference_command",
        required=True,
    )
    reference_compile = reference_commands.add_parser(
        "compile-single-assets",
        help="compile deterministic Golden References for frozen single-asset cases",
    )
    reference_compile.add_argument("--suite", required=True)
    reference_compile.add_argument("--path", required=True, help="compiled .uproject file")
    reference_compile.add_argument("--assets", required=True)
    reference_compile.add_argument("--reference-suite-id", required=True)
    reference_compile.add_argument(
        "--retrieval-campaign",
        help="optional frozen Stage 1 campaign whose retrieval candidates are reused",
    )
    reference_compile.add_argument(
        "--case-id",
        action="append",
        help="compile one exact single_asset case; repeat for an ordered subset",
    )
    reference_compile.add_argument("--output", "-o", required=True)
    reference_compile.set_defaults(handler=cmd_training_reference_compile)

    reference_lighting = reference_commands.add_parser(
        "compile-lighting", help="compile the four reviewed single-object lighting recipes",
    )
    reference_lighting.add_argument("--suite", required=True)
    reference_lighting.add_argument("--path", required=True)
    reference_lighting.add_argument("--assets", required=True)
    reference_lighting.add_argument("--reference-suite-id", required=True)
    reference_lighting.add_argument("--retrieval-campaign")
    reference_lighting.add_argument("--case-id", action="append")
    reference_lighting.add_argument("--output", "-o", required=True)
    reference_lighting.set_defaults(
        handler=cmd_training_reference_compile, reference_category="lighting",
    )

    reference_compile_materials = reference_commands.add_parser(
        "compile-materials",
        help="compile deterministic Golden References for frozen material cases",
    )
    reference_compile_materials.add_argument("--suite", required=True)
    reference_compile_materials.add_argument(
        "--path", required=True, help="compiled .uproject file"
    )
    reference_compile_materials.add_argument("--assets", required=True)
    reference_compile_materials.add_argument("--reference-suite-id", required=True)
    reference_compile_materials.add_argument(
        "--retrieval-campaign",
        help="optional frozen Stage 1 campaign whose retrieval candidates are reused",
    )
    reference_compile_materials.add_argument(
        "--case-id",
        action="append",
        help="compile one exact material_assignment case; repeat for an ordered subset",
    )
    reference_compile_materials.add_argument("--output", "-o", required=True)
    reference_compile_materials.set_defaults(
        handler=cmd_training_reference_compile_materials
    )

    reference_validate = reference_commands.add_parser(
        "validate",
        help="statically validate Golden References without invoking Unreal or models",
    )
    reference_validate.add_argument("--references", required=True)
    reference_validate.add_argument("--suite", required=True)
    reference_validate.add_argument("--path", required=True, help="compiled .uproject file")
    reference_validate.add_argument(
        "--assets",
        required=True,
        help="same frozen complete AssetCard catalog",
    )
    reference_validate.add_argument("--output", "-o")
    reference_validate.set_defaults(handler=cmd_training_reference_validate)

    reference_plan = reference_commands.add_parser(
        "plan",
        help="freeze validated references into a non-executing campaign",
    )
    reference_plan.add_argument("--references", required=True)
    reference_plan.add_argument("--suite", required=True)
    reference_plan.add_argument("--campaign-dir", required=True)
    reference_plan.add_argument("--campaign-id", required=True)
    reference_plan.add_argument("--path", required=True, help="compiled .uproject file")
    reference_plan.add_argument("--assets", required=True)
    reference_plan.add_argument("--vision-model")
    reference_plan.add_argument("--max-attempts", type=int, choices=range(1, 4), default=2)
    reference_plan.add_argument("--output", "-o")
    reference_plan.set_defaults(handler=cmd_training_reference_plan)

    reference_run = reference_commands.add_parser(
        "run",
        help="render or resume a Golden Reference campaign without a planner model",
    )
    reference_run.add_argument("--references", required=True)
    reference_run.add_argument("--suite", required=True)
    reference_run.add_argument("--campaign-dir", required=True)
    reference_run.add_argument("--path", required=True, help="compiled .uproject file")
    reference_run.add_argument("--engine-root", required=True)
    reference_run.add_argument("--assets", required=True)
    reference_run.add_argument(
        "--retry-failed",
        action="store_true",
        help="retry non-eligible references up to the planned attempt limit",
    )
    reference_run.add_argument("--output", "-o")
    reference_run.set_defaults(handler=cmd_training_reference_run)

    training_campaign = subparsers.add_parser(
        "training-campaign",
        help="plan, run, resume, inspect, or baseline a workflow corpus campaign",
    )
    campaign_commands = training_campaign.add_subparsers(
        dest="training_campaign_command",
        required=True,
    )
    campaign_plan = campaign_commands.add_parser(
        "plan",
        help="freeze a campaign suite and create a non-executing run manifest",
    )
    campaign_plan.add_argument("--suite", required=True)
    campaign_plan.add_argument("--campaign-dir", required=True)
    campaign_plan.add_argument("--campaign-id", required=True)
    campaign_plan.add_argument("--path", required=True, help="compiled .uproject file")
    campaign_plan.add_argument("--assets", required=True, help="complete AssetCard catalog")
    campaign_plan.add_argument("--planner-model")
    campaign_plan.add_argument("--vision-model")
    campaign_plan.add_argument("--max-cases", type=int)
    campaign_plan.add_argument(
        "--case-id",
        action="append",
        help="select one exact suite case; repeat for an ordered subset",
    )
    campaign_plan.add_argument("--max-attempts", type=int, choices=range(1, 4), default=2)
    campaign_plan.add_argument("--output", help="optional plan summary JSON")
    campaign_plan.set_defaults(handler=cmd_training_campaign_plan)

    campaign_run = campaign_commands.add_parser(
        "run",
        help="execute or resume isolated end-to-end campaign cases",
    )
    campaign_run.add_argument("--campaign-dir", required=True)
    campaign_run.add_argument("--path", required=True, help="compiled .uproject file")
    campaign_run.add_argument("--engine-root", required=True)
    campaign_run.add_argument("--assets", required=True, help="same frozen AssetCard catalog")
    campaign_run.add_argument(
        "--retry-failed",
        action="store_true",
        help="retry non-eligible cases up to the planned per-case attempt limit",
    )
    campaign_run.add_argument("--output", help="optional run summary JSON")
    campaign_run.set_defaults(handler=cmd_training_campaign_run)

    campaign_status = campaign_commands.add_parser(
        "status",
        help="summarize a campaign without invoking models or Unreal",
    )
    campaign_status.add_argument("manifest")
    campaign_status.add_argument("--output", "-o")
    campaign_status.set_defaults(handler=cmd_training_campaign_status)

    campaign_cancel = campaign_commands.add_parser(
        "cancel",
        help="recover active evidence and mark a stopped campaign as resumable cancelled",
    )
    campaign_cancel.add_argument("manifest")
    campaign_cancel.add_argument(
        "--reason",
        default="campaign cancelled by operator",
        help="bounded cancellation reason saved with the campaign",
    )
    campaign_cancel.add_argument("--output", "-o")
    campaign_cancel.set_defaults(handler=cmd_training_campaign_cancel)

    campaign_baseline = campaign_commands.add_parser(
        "baseline",
        help="freeze aggregate base-model metrics from a completed campaign",
    )
    campaign_baseline.add_argument("manifest")
    campaign_baseline.add_argument("--report-id", required=True)
    campaign_baseline.add_argument("--output", "-o", required=True)
    campaign_baseline.set_defaults(handler=cmd_training_campaign_baseline)

    run = subparsers.add_parser(
        "run",
        help="run bounded retrieval, planning, Unreal preview, evaluation, and correction",
    )
    run.add_argument("path", help="path to a compiled .uproject file")
    run.add_argument(
        "--engine-root",
        required=True,
        help="Unreal installation root containing the Engine directory",
    )
    prompt_source = run.add_mutually_exclusive_group(required=True)
    prompt_source.add_argument("--prompt", help="natural-language render request")
    prompt_source.add_argument(
        "--prompt-file",
        help="UTF-8 text file containing the render request (recommended for editor panels)",
    )
    run.add_argument(
        "--assets",
        required=True,
        help="validated complete AssetCard JSON catalog",
    )
    run.add_argument(
        "--workflow-dir",
        required=True,
        help="new or empty directory for all immutable workflow evidence",
    )
    run.add_argument(
        "--workflow-id",
        required=True,
        help="stable lowercase workflow identifier",
    )
    run.add_argument("--planner-model", help="override the configured planner/correction model")
    run.add_argument("--vision-model", help="override the configured vision model")
    run.add_argument(
        "--retrieve-assets",
        type=int,
        default=8,
        metavar="N",
        help="retrieve N static-mesh candidates (default: 8)",
    )
    run.add_argument(
        "--retrieve-materials",
        type=int,
        default=5,
        metavar="N",
        help="retrieve N material candidates (default: 5)",
    )
    run.add_argument(
        "--max-iterations",
        type=int,
        choices=range(1, 6),
        default=2,
        help="maximum preview/evaluation attempts from 1 to 5 (default: 2)",
    )
    run.add_argument(
        "--view-axis",
        choices=VIEW_AXES,
        default="auto-product",
        help="deterministic product framing direction (default: auto-product)",
    )
    run.add_argument(
        "--margin",
        type=float,
        default=0.02,
        help="fractional framing margin on each image edge (default: 0.02)",
    )
    run.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="per-Unreal-preview timeout in seconds (default: 600)",
    )
    run.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="stop before Unreal when semantic preflight needs review",
    )
    run.add_argument(
        "--no-studio-calibration",
        action="store_true",
        help="disable the deterministic fixed-exposure first-preview baseline",
    )
    run.set_defaults(handler=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_console()
    args = build_parser().parse_args(argv)
    return args.handler(args)
