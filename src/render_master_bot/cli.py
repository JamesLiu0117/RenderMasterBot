"""Command-line interface for inspecting and exercising the planning boundary."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError

from render_master_bot.asset_index import (
    AssetIndexError,
    load_asset_card_catalog,
    open_persistent_asset_index,
)
from render_master_bot.contracts import CONTRACT_MODELS
from render_master_bot.models import RenderSpec
from render_master_bot.ollama import OllamaClient, OllamaError
from render_master_bot.planner import PlanningError, ScenePlanner
from render_master_bot.preflight import run_preflight
from render_master_bot.schemas import contract_model, contract_schema
from render_master_bot.settings import Settings
from render_master_bot.unreal_assets import UnrealAssetScanError, run_unreal_asset_scan
from render_master_bot.unreal_probe import UnrealProbeError, probe_unreal_project


def _settings() -> Settings:
    return Settings.from_env()


def _client(settings: Settings | None = None) -> OllamaClient:
    settings = settings or _settings()
    return OllamaClient(settings.ollama_base_url, num_ctx=settings.num_ctx)


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
            hits = index.search(args.query, limit=args.limit)
    except (AssetIndexError, OllamaError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _write_json([asdict(hit) for hit in hits], args.output)
    print(
        f"ASSET SEARCH: results={len(hits)} query={args.query!r}",
        file=sys.stderr if args.output is None else sys.stdout,
    )
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    settings = _settings()
    model = args.model or settings.planner_model
    planner = ScenePlanner(_client(settings))
    asset_ids = list(dict.fromkeys(args.asset))
    asset_context = [f"{asset_id}: explicitly allowed by CLI" for asset_id in asset_ids]
    if args.retrieve_assets:
        try:
            with _asset_index(settings) as index:
                hits = index.search(
                    args.prompt,
                    limit=args.retrieve_assets,
                )
        except (AssetIndexError, OllamaError) as exc:
            print(f"ERROR: asset retrieval failed: {exc}", file=sys.stderr)
            return 1
        for hit in hits:
            if hit.asset_id not in asset_ids:
                asset_ids.append(hit.asset_id)
                asset_context.append(hit.planner_context())
        print(
            "Retrieved asset IDs: "
            + (", ".join(hit.asset_id for hit in hits) if hits else "none"),
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
                _write_json(
                    _response_metrics(exc.response, status="invalid", error=str(exc)),
                    args.metrics_output,
                )
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
    asset_search.add_argument("--output", "-o", help="write ranked retrieval results")
    asset_search.set_defaults(handler=cmd_asset_search)

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
    plan.add_argument("--output", "-o")
    plan.add_argument("--raw-output", help="save the model's unvalidated response")
    plan.add_argument("--metrics-output", help="write timing and token metrics as JSON")
    plan.set_defaults(handler=cmd_plan)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_console()
    args = build_parser().parse_args(argv)
    return args.handler(args)
