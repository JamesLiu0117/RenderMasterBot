"""Reproducible product-baseline identities and frozen task-suite contracts."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, model_validator

from render_master_bot import __version__
from render_master_bot.contracts import (
    AssistantConversationMessage,
    TrainingDatasetManifest,
    UnrealConversationContext,
)
from render_master_bot.models import Identifier, StrictModel
from render_master_bot.ollama import OllamaClient, OllamaError
from render_master_bot.serialization import canonical_sha256
from render_master_bot.settings import Settings


ShortText = Annotated[str, Field(min_length=1, max_length=240)]
LongText = Annotated[str, Field(min_length=1, max_length=4000)]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
GitObjectId = Annotated[str, Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")]
BoundedPath = Annotated[str, Field(min_length=1, max_length=1000)]


def _validate_embedded_sha256(
    value: Any,
    *,
    field_name: str,
    label: str,
) -> Any:
    if not isinstance(value, dict):
        return value
    supplied = value.get(field_name)
    payload = {key: item for key, item in value.items() if key != field_name}
    if supplied != canonical_sha256(payload):
        raise ValueError(f"{label} {field_name} is invalid")
    return value


class ProductBaselineError(RuntimeError):
    """Raised when a baseline input cannot be validated or identified."""


class ProductBenchmarkExpectation(StrictModel):
    outcome: Literal["respond", "clarify", "route"]
    intent: Identifier
    required_context: list[Identifier] = Field(default_factory=list, max_length=10)
    acceptance_checks: list[ShortText] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def required_context_is_unique(self) -> "ProductBenchmarkExpectation":
        if len(self.required_context) != len(set(self.required_context)):
            raise ValueError("benchmark required_context values must be unique")
        return self


class ProductBenchmarkCase(StrictModel):
    case_id: Identifier
    category: Literal[
        "capability_qa",
        "project_context",
        "clarification",
        "scene_preview",
        "bounded_edit",
        "performance_analysis",
        "graphics_knowledge",
        "unsupported_request",
    ]
    language: Literal["en", "zh"]
    evaluation_stage: Literal[
        "router_only",
        "tool_result",
        "approval_loop",
        "complete_user_task",
    ]
    request: LongText
    history: list[AssistantConversationMessage] = Field(default_factory=list, max_length=24)
    context_profile_id: Identifier
    expectation: ProductBenchmarkExpectation


class ProductBenchmarkSuite(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    suite_id: Identifier
    purpose: LongText
    status: Literal["development_regression", "blind_holdout"]
    context_profiles: dict[Identifier, UnrealConversationContext] = Field(
        min_length=1,
        max_length=32,
    )
    cases: list[ProductBenchmarkCase] = Field(min_length=1, max_length=200)
    suite_sha256: Sha256

    @model_validator(mode="before")
    @classmethod
    def suite_hash_is_valid(cls, value: Any) -> Any:
        return _validate_embedded_sha256(
            value,
            field_name="suite_sha256",
            label="product benchmark",
        )

    @model_validator(mode="after")
    def suite_is_frozen_and_unique(self) -> "ProductBenchmarkSuite":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("product benchmark case IDs must be unique")
        referenced_profiles = {case.context_profile_id for case in self.cases}
        available_profiles = set(self.context_profiles)
        missing_profiles = referenced_profiles - available_profiles
        if missing_profiles:
            raise ValueError(
                "benchmark cases reference missing context profiles: "
                + ", ".join(sorted(missing_profiles))
            )
        unused_profiles = available_profiles - referenced_profiles
        if unused_profiles:
            raise ValueError(
                "benchmark context profiles are unused: "
                + ", ".join(sorted(unused_profiles))
            )
        return self


class CapabilityEvidenceEntry(StrictModel):
    capability_id: Identifier
    implementation_status: Literal["planned", "partial", "implemented"]
    highest_evidence_level: Literal[
        "none",
        "unit_or_fixture",
        "live_model_fixture",
        "live_engine_component",
        "complete_user_task",
    ]
    assessment: LongText
    evidence_paths: list[BoundedPath] = Field(default_factory=list, max_length=32)
    acceptance_gaps: list[ShortText] = Field(default_factory=list, max_length=32)


class CapabilityEvidenceLedger(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    ledger_id: Identifier
    assessed_at_utc: datetime
    entries: list[CapabilityEvidenceEntry] = Field(min_length=1, max_length=128)
    ledger_sha256: Sha256

    @model_validator(mode="before")
    @classmethod
    def ledger_hash_is_valid(cls, value: Any) -> Any:
        return _validate_embedded_sha256(
            value,
            field_name="ledger_sha256",
            label="capability evidence ledger",
        )

    @model_validator(mode="after")
    def ledger_is_frozen_and_unique(self) -> "CapabilityEvidenceLedger":
        capability_ids = [entry.capability_id for entry in self.entries]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("capability evidence IDs must be unique")
        return self


class FileIdentity(StrictModel):
    path: BoundedPath
    size_bytes: Annotated[int, Field(ge=0)]
    sha256: Sha256


class SourceIdentity(StrictModel):
    root: BoundedPath
    git_head: GitObjectId
    git_dirty: bool
    source_file_count: Annotated[int, Field(ge=1)]
    source_tree_sha256: Sha256


class OllamaModelIdentity(StrictModel):
    role: Literal["planner", "vision", "embedding"]
    configured_name: ShortText
    installed: bool
    digest: Sha256 | None = None
    size_bytes: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def installation_details_are_consistent(self) -> "OllamaModelIdentity":
        if self.installed != (self.digest is not None and self.size_bytes is not None):
            raise ValueError("installed models require digest and size")
        return self


class UnrealEngineIdentity(StrictModel):
    root: BoundedPath
    build_version: FileIdentity
    major: Annotated[int, Field(ge=0)]
    minor: Annotated[int, Field(ge=0)]
    patch: Annotated[int, Field(ge=0)]
    changelist: Annotated[int, Field(ge=0)]
    branch_name: ShortText


class RuntimeSettingsIdentity(StrictModel):
    ollama_base_url: BoundedPath
    planner_model: ShortText
    vision_model: ShortText
    embedding_model: ShortText
    num_ctx: Annotated[int, Field(gt=0)]
    vision_num_ctx: Annotated[int, Field(gt=0)]
    data_dir: BoundedPath
    chroma_dir: BoundedPath
    asset_collection: Identifier


class ProductRuntimeBaseline(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    baseline_id: Identifier
    captured_at_utc: datetime
    evidence_scope: Literal["live_configuration"] = "live_configuration"
    app_version: ShortText
    python_version: ShortText
    platform: ShortText
    source: SourceIdentity
    settings: RuntimeSettingsIdentity
    models: list[OllamaModelIdentity] = Field(min_length=3, max_length=3)
    unreal_engine: UnrealEngineIdentity
    unreal_project: FileIdentity
    asset_catalog: FileIdentity
    asset_card_count: Annotated[int, Field(ge=0)]
    training_dataset_manifest: FileIdentity
    training_dataset_id: Identifier
    training_dataset_sha256: Sha256
    product_benchmark_suite: FileIdentity
    product_benchmark_suite_id: Identifier
    product_benchmark_suite_sha256: Sha256
    capability_evidence_ledger: FileIdentity
    capability_evidence_ledger_id: Identifier
    capability_evidence_ledger_sha256: Sha256
    baseline_sha256: Sha256

    @model_validator(mode="before")
    @classmethod
    def baseline_hash_is_valid(cls, value: Any) -> Any:
        return _validate_embedded_sha256(
            value,
            field_name="baseline_sha256",
            label="product runtime baseline",
        )

    @model_validator(mode="after")
    def baseline_is_complete_and_frozen(self) -> "ProductRuntimeBaseline":
        roles = [model.role for model in self.models]
        if roles != ["planner", "vision", "embedding"]:
            raise ValueError("baseline models must be ordered planner, vision, embedding")
        return self


def _resolve_file(path: str | Path, *, role: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ProductBaselineError(f"{role} does not exist: {resolved}")
    return resolved


def _file_identity(path: str | Path, *, role: str) -> FileIdentity:
    resolved = _resolve_file(path, role=role)
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return FileIdentity(
        path=str(resolved),
        size_bytes=resolved.stat().st_size,
        sha256=digest.hexdigest(),
    )


def _load_contract(path: str | Path, model, *, role: str):
    resolved = _resolve_file(path, role=role)
    try:
        return model.model_validate_json(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, ValidationError) as exc:
        raise ProductBaselineError(f"invalid {role} {resolved}: {exc}") from exc


def load_product_benchmark_suite(path: str | Path) -> ProductBenchmarkSuite:
    return _load_contract(path, ProductBenchmarkSuite, role="product benchmark suite")


def load_capability_evidence_ledger(path: str | Path) -> CapabilityEvidenceLedger:
    return _load_contract(path, CapabilityEvidenceLedger, role="capability evidence ledger")


def _git(source_root: Path, *args: str, text: bool = True):
    try:
        return subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={source_root.as_posix()}",
                "-C",
                str(source_root),
                *args,
            ],
            check=True,
            capture_output=True,
            text=text,
            encoding="utf-8" if text else None,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProductBaselineError(
            f"could not identify Git source at {source_root}: {exc}"
        ) from exc


def _source_identity(source_root: str | Path) -> SourceIdentity:
    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        raise ProductBaselineError(f"source root does not exist: {root}")
    head = _git(root, "rev-parse", "HEAD").strip().lower()
    status = _git(root, "status", "--porcelain=v1")
    listed = _git(
        root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        text=False,
    )
    relative_paths = sorted(
        path.decode("utf-8") for path in listed.split(b"\0") if path
    )
    if not relative_paths:
        raise ProductBaselineError("Git source identity contains no files")
    digest = hashlib.sha256()
    for relative in relative_paths:
        file_path = root / relative
        if not file_path.is_file():
            raise ProductBaselineError(f"source file disappeared during capture: {relative}")
        digest.update(relative.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(file_path.read_bytes()).digest())
    return SourceIdentity(
        root=str(root),
        git_head=head,
        git_dirty=bool(status.strip()),
        source_file_count=len(relative_paths),
        source_tree_sha256=digest.hexdigest(),
    )


def _engine_identity(engine_root: str | Path) -> UnrealEngineIdentity:
    root = Path(engine_root).expanduser().resolve()
    build_path = root / "Engine" / "Build" / "Build.version"
    build_identity = _file_identity(build_path, role="Unreal Build.version")
    try:
        value = json.loads(build_path.read_text(encoding="utf-8-sig"))
        return UnrealEngineIdentity(
            root=str(root),
            build_version=build_identity,
            major=value["MajorVersion"],
            minor=value["MinorVersion"],
            patch=value["PatchVersion"],
            changelist=value["Changelist"],
            branch_name=value["BranchName"],
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ProductBaselineError(f"invalid Unreal Build.version {build_path}: {exc}") from exc


def _asset_count(path: Path) -> int:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ProductBaselineError(f"invalid asset catalog {path}: {exc}") from exc
    if not isinstance(value, list):
        raise ProductBaselineError("asset catalog must be a JSON array")
    return len(value)


def capture_product_runtime_baseline(
    *,
    baseline_id: str,
    source_root: str | Path,
    unreal_project_path: str | Path,
    engine_root: str | Path,
    asset_catalog_path: str | Path,
    training_dataset_manifest_path: str | Path,
    product_benchmark_suite_path: str | Path,
    capability_evidence_ledger_path: str | Path,
    settings: Settings,
    client: OllamaClient,
    captured_at_utc: datetime | None = None,
) -> ProductRuntimeBaseline:
    suite = load_product_benchmark_suite(product_benchmark_suite_path)
    ledger = load_capability_evidence_ledger(capability_evidence_ledger_path)
    dataset = _load_contract(
        training_dataset_manifest_path,
        TrainingDatasetManifest,
        role="training dataset manifest",
    )
    asset_path = _resolve_file(asset_catalog_path, role="asset catalog")
    try:
        installed = {record.name: record for record in client.list_model_details()}
    except OllamaError as exc:
        raise ProductBaselineError(str(exc)) from exc

    models = []
    for role, name in (
        ("planner", settings.planner_model),
        ("vision", settings.vision_model),
        ("embedding", settings.embedding_model),
    ):
        record = installed.get(name)
        models.append(OllamaModelIdentity(
            role=role,
            configured_name=name,
            installed=record is not None,
            digest=record.digest if record else None,
            size_bytes=record.size_bytes if record else None,
        ))
    missing = [model.configured_name for model in models if not model.installed]
    if missing:
        raise ProductBaselineError(
            "configured Ollama models are missing: " + ", ".join(missing)
        )

    suite_file = _file_identity(product_benchmark_suite_path, role="product benchmark suite")
    ledger_file = _file_identity(
        capability_evidence_ledger_path,
        role="capability evidence ledger",
    )
    captured = captured_at_utc or datetime.now(timezone.utc)
    payload = {
        "schema_version": "0.1",
        "baseline_id": baseline_id,
        "captured_at_utc": captured.isoformat(),
        "evidence_scope": "live_configuration",
        "app_version": __version__,
        "python_version": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "source": _source_identity(source_root).model_dump(mode="json"),
        "settings": RuntimeSettingsIdentity(
            ollama_base_url=settings.ollama_base_url,
            planner_model=settings.planner_model,
            vision_model=settings.vision_model,
            embedding_model=settings.embedding_model,
            num_ctx=settings.num_ctx,
            vision_num_ctx=settings.vision_num_ctx,
            data_dir=str(settings.data_dir.expanduser().resolve()),
            chroma_dir=str(settings.chroma_dir.expanduser().resolve()),
            asset_collection=settings.asset_collection,
        ).model_dump(mode="json"),
        "models": [model.model_dump(mode="json") for model in models],
        "unreal_engine": _engine_identity(engine_root).model_dump(mode="json"),
        "unreal_project": _file_identity(
            unreal_project_path,
            role="Unreal project",
        ).model_dump(mode="json"),
        "asset_catalog": _file_identity(
            asset_path,
            role="asset catalog",
        ).model_dump(mode="json"),
        "asset_card_count": _asset_count(asset_path),
        "training_dataset_manifest": _file_identity(
            training_dataset_manifest_path,
            role="training dataset manifest",
        ).model_dump(mode="json"),
        "training_dataset_id": dataset.dataset_id,
        "training_dataset_sha256": dataset.dataset_sha256,
        "product_benchmark_suite": suite_file.model_dump(mode="json"),
        "product_benchmark_suite_id": suite.suite_id,
        "product_benchmark_suite_sha256": suite.suite_sha256,
        "capability_evidence_ledger": ledger_file.model_dump(mode="json"),
        "capability_evidence_ledger_id": ledger.ledger_id,
        "capability_evidence_ledger_sha256": ledger.ledger_sha256,
    }
    payload["baseline_sha256"] = canonical_sha256(payload)
    return ProductRuntimeBaseline.model_validate(payload)
