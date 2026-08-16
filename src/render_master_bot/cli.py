"""Command-line interface for inspecting and exercising the planning boundary."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
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
from render_master_bot.schemas import contract_model, contract_schema
from render_master_bot.serialization import canonical_sha256
from render_master_bot.settings import Settings
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
