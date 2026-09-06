"""Deterministic, auditable training-data export from workflow evidence."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import TypeAdapter, ValidationError

from render_master_bot.asset_index import AssetSearchHit
from render_master_bot.contracts import (
    AssetCard,
    ArtifactRecord,
    RenderWorkflowManifest,
    ShortText,
    TrainingArtifactReference,
    TrainingCampaignRun,
    TrainingDatasetAuditReport,
    TrainingDatasetCampaignSource,
    TrainingDatasetManifest,
    TrainingDatasetSplit,
    TrainingExample,
    TrainingMessage,
    TrainingReadinessThresholds,
    TrainingSourceSkip,
)
from render_master_bot.models import Identifier, RenderSpec
from render_master_bot.planner import build_scene_planner_messages
from render_master_bot.serialization import canonical_json_bytes, canonical_sha256


BUILDER_VERSION = "0.2"
DEFAULT_DATASET_ID = "renderbot_stage0_v1"
DEFAULT_SPLIT_SEED = "renderbot-stage0-v1"
MANIFEST_FILENAME = "dataset_manifest.json"
AUDIT_FILENAME = "audit_report.json"
SPLIT_NAMES = ("train", "validation", "test")
REQUIRED_PLANNER_ARTIFACT_ROLES = (
    "retrieval_results",
    "retrieved_asset_catalog",
    "planner_raw",
    "initial_render_spec",
)
_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)
_SHORT_TEXT_ADAPTER = TypeAdapter(ShortText)


class TrainingDataError(RuntimeError):
    """Raised when a dataset cannot be built or audited safely."""


class _SourceRejected(RuntimeError):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class TrainingDatasetBuildResult:
    manifest_path: Path
    audit_path: Path
    manifest: TrainingDatasetManifest
    audit: TrainingDatasetAuditReport


@dataclass(frozen=True)
class _CampaignEvidence:
    reference: TrainingDatasetCampaignSource
    snapshot: bytes
    allowed: dict[str, str]
    groups: dict[str, str]


def _campaign_membership(
    run: TrainingCampaignRun, source_run_path: str,
) -> tuple[dict[str, str], dict[str, str]]:
    if run.status != "completed":
        raise TrainingDataError("campaign eligibility requires a completed campaign run")
    allowed: dict[str, str] = {}
    groups: dict[str, str] = {}
    for result in run.case_results:
        if not result.training_eligible:
            continue
        attempt = result.attempts[-1]
        if not attempt.workflow_manifest_path or not attempt.workflow_manifest_sha256:
            raise TrainingDataError(f"eligible campaign case {result.case_id} has no manifest")
        relative = str(PurePosixPath(source_run_path).parent / attempt.workflow_manifest_path)
        if relative in allowed:
            raise TrainingDataError(f"campaign eligibility repeats workflow manifest {relative}")
        allowed[relative] = attempt.workflow_manifest_sha256
        # A rerun of one suite case must stay in the same partition even when
        # its campaign ID, workflow ID, or final model response changes.
        groups[relative] = "case_" + canonical_sha256({
            "project_descriptor_sha256": run.project_descriptor_sha256,
            "asset_catalog_sha256": run.asset_catalog_sha256,
            "case_id": result.case_id,
        })[:32]
    return allowed, groups


def _load_campaign_sources(
    paths: list[str | Path], source_root: Path,
) -> list[_CampaignEvidence]:
    if not paths or len(paths) > 256:
        raise TrainingDataError("merge requires between 1 and 256 campaign manifests")
    resolved = [Path(path).expanduser().resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise TrainingDataError("campaign manifest paths must be unique")
    evidence: list[_CampaignEvidence] = []
    identities: set[tuple[str, str, str]] = set()
    campaign_ids: set[str] = set()
    all_members: set[str] = set()
    for index, path in enumerate(sorted(resolved, key=lambda p: str(p).casefold())):
        relative = _portable_relative(path, source_root)
        if any(path.parent == other.parent or path.parent.is_relative_to(other.parent)
               or other.parent.is_relative_to(path.parent) for other in resolved if other != path):
            raise TrainingDataError("campaign directories must be distinct and non-overlapping")
        try:
            snapshot = path.read_bytes()
            run = TrainingCampaignRun.model_validate_json(snapshot)
        except (OSError, ValueError) as exc:
            raise TrainingDataError(f"invalid campaign eligibility run {path}: {exc}") from exc
        if run.campaign_id in campaign_ids:
            raise TrainingDataError(f"duplicate campaign identity: {run.campaign_id}")
        campaign_ids.add(run.campaign_id)
        identities.add((run.project_name, run.project_descriptor_sha256, run.asset_catalog_sha256))
        if len(identities) > 1:
            raise TrainingDataError("campaign project or asset catalog identities do not match")
        allowed, groups = _campaign_membership(run, relative)
        for member in allowed:
            member_path = (source_root / member).resolve()
            _portable_relative(member_path, path.parent)
            if not member_path.is_file():
                raise TrainingDataError(f"campaign eligible workflow manifest is missing: {member}")
        if all_members.intersection(allowed):
            raise TrainingDataError("campaigns repeat an eligible workflow manifest")
        all_members.update(allowed)
        evidence.append(_CampaignEvidence(
            reference=TrainingDatasetCampaignSource(
                source_run_path=relative,
                snapshot_path=f"provenance/campaigns/{index + 1:03d}.json",
                sha256=hashlib.sha256(snapshot).hexdigest(),
            ),
            snapshot=snapshot, allowed=allowed, groups=groups,
        ))
    return evidence


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _portable_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise TrainingDataError(f"path escapes the declared root: {path}") from exc


def _bounded_detail(value: object) -> str:
    text = str(value).strip() or "unspecified source rejection"
    return text[:4000]


def discover_workflow_manifests(source_root: str | Path) -> list[Path]:
    """Find workflow manifests in a stable, case-insensitive path order."""

    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        raise TrainingDataError(f"training source root is not a directory: {root}")
    return sorted(
        root.rglob("workflow_manifest.json"),
        key=lambda path: _portable_relative(path, root).casefold(),
    )


def _load_campaign_eligibility(
    *,
    campaign_run_path: str | Path,
    source_root: Path,
) -> tuple[str, str, dict[str, str]]:
    path = Path(campaign_run_path).expanduser().resolve()
    try:
        relative_run = _portable_relative(path, source_root)
        run = TrainingCampaignRun.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        raise TrainingDataError(f"invalid campaign eligibility run {path}: {exc}") from exc
    if run.status != "completed":
        raise TrainingDataError("campaign eligibility requires a completed campaign run")
    campaign_root = path.parent
    allowed: dict[str, str] = {}
    for result in run.case_results:
        if not result.training_eligible:
            continue
        attempt = result.attempts[-1]
        if (
            attempt.workflow_manifest_path is None
            or attempt.workflow_manifest_sha256 is None
        ):
            raise TrainingDataError(
                f"eligible campaign case {result.case_id} has no workflow manifest"
            )
        manifest_path = (campaign_root / attempt.workflow_manifest_path).resolve()
        _portable_relative(manifest_path, campaign_root)
        relative_manifest = _portable_relative(manifest_path, source_root)
        if relative_manifest in allowed:
            raise TrainingDataError(
                f"campaign eligibility repeats workflow manifest {relative_manifest}"
            )
        allowed[relative_manifest] = attempt.workflow_manifest_sha256
    return relative_run, canonical_sha256(run), allowed


def _artifact_for_role(
    manifest: RenderWorkflowManifest,
    role: str,
) -> ArtifactRecord:
    matches = [artifact for artifact in manifest.output_artifacts if artifact.role == role]
    if len(matches) != 1 or matches[0].sha256 is None:
        raise _SourceRejected(
            "missing_artifact",
            f"workflow requires exactly one hash-pinned {role!r} output artifact",
        )
    return matches[0]


def _verified_artifact_path(
    *,
    workflow_root: Path,
    artifact: ArtifactRecord,
) -> Path:
    path = (workflow_root / artifact.path).resolve()
    try:
        path.relative_to(workflow_root.resolve())
    except ValueError as exc:
        raise _SourceRejected(
            "missing_artifact",
            f"artifact {artifact.role!r} escapes its workflow directory",
        ) from exc
    if not path.is_file():
        raise _SourceRejected(
            "missing_artifact",
            f"artifact {artifact.role!r} does not exist at {artifact.path}",
        )
    actual_sha256 = _sha256_file(path)
    if actual_sha256 != artifact.sha256:
        raise _SourceRejected(
            "hash_mismatch",
            f"artifact {artifact.role!r} hash does not match workflow_manifest.json",
        )
    return path


def _load_retrieval_hits(path: Path) -> list[AssetSearchHit]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, list) or not value:
            raise ValueError("retrieval results must be a non-empty JSON array")
        hits = [AssetSearchHit(**item) for item in value]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise _SourceRejected(
            "invalid_artifact",
            f"invalid retrieval results: {_bounded_detail(exc)}",
        ) from exc
    if len({hit.asset_id for hit in hits}) != len(hits):
        raise _SourceRejected("invalid_artifact", "retrieval result asset IDs are not unique")
    return hits


def _load_retrieved_cards(path: Path) -> list[AssetCard]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, list) or not value:
            raise ValueError("retrieved asset catalog must be a non-empty JSON array")
        cards = [AssetCard.model_validate(item) for item in value]
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise _SourceRejected(
            "invalid_artifact",
            f"invalid retrieved asset catalog: {_bounded_detail(exc)}",
        ) from exc
    if len({card.asset_id for card in cards}) != len(cards):
        raise _SourceRejected("invalid_artifact", "retrieved AssetCard IDs are not unique")
    return cards


def _load_render_spec(path: Path, *, role: str) -> RenderSpec:
    try:
        return RenderSpec.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        raise _SourceRejected(
            "invalid_artifact",
            f"artifact {role!r} is not a valid RenderSpec: {_bounded_detail(exc)}",
        ) from exc


def _build_planner_example(
    *,
    source_root: Path,
    manifest_path: Path,
    manifest_sha256: str,
    manifest: RenderWorkflowManifest,
    prefer_final_spec: bool = False,
    source_group_id: str | None = None,
) -> TrainingExample:
    workflow_root = manifest_path.parent
    artifacts = {
        role: _artifact_for_role(manifest, role)
        for role in REQUIRED_PLANNER_ARTIFACT_ROLES
    }
    artifact_paths = {
        role: _verified_artifact_path(
            workflow_root=workflow_root,
            artifact=artifact,
        )
        for role, artifact in artifacts.items()
    }
    hits = _load_retrieval_hits(artifact_paths["retrieval_results"])
    cards = _load_retrieved_cards(artifact_paths["retrieved_asset_catalog"])
    hit_ids = [hit.asset_id for hit in hits]
    card_ids = [card.asset_id for card in cards]
    if hit_ids != manifest.retrieved_asset_ids or card_ids != hit_ids:
        raise _SourceRejected(
            "invalid_artifact",
            "retrieval results, retrieved AssetCards, and manifest asset IDs disagree",
        )
    raw_spec = _load_render_spec(artifact_paths["planner_raw"], role="planner_raw")
    initial_spec = _load_render_spec(
        artifact_paths["initial_render_spec"],
        role="initial_render_spec",
    )
    if canonical_sha256(raw_spec) != canonical_sha256(initial_spec):
        raise _SourceRejected(
            "invalid_artifact",
            "planner_raw and initial_render_spec do not contain the same RenderSpec",
        )

    training_spec = initial_spec
    training_artifact_roles = list(REQUIRED_PLANNER_ARTIFACT_ROLES)
    reference_artifacts = [
        artifact
        for artifact in manifest.input_artifacts
        if artifact.role == "golden_reference_case"
    ]
    if reference_artifacts:
        if len(reference_artifacts) != 1 or reference_artifacts[0].sha256 is None:
            raise _SourceRejected(
                "missing_artifact",
                "Golden Reference workflows require one hash-pinned reference case",
            )
        artifacts["golden_reference_case"] = reference_artifacts[0]
        artifact_paths["golden_reference_case"] = _verified_artifact_path(
            workflow_root=workflow_root,
            artifact=reference_artifacts[0],
        )
        training_artifact_roles.append("golden_reference_case")
    if prefer_final_spec:
        final_artifact = _artifact_for_role(manifest, "final_render_spec")
        final_path = _verified_artifact_path(
            workflow_root=workflow_root,
            artifact=final_artifact,
        )
        training_spec = _load_render_spec(final_path, role="final_render_spec")
        artifacts["final_render_spec"] = final_artifact
        artifact_paths["final_render_spec"] = final_path
        training_artifact_roles.append("final_render_spec")
    # Notes are non-executable planner commentary. They can contradict the
    # structured fields, so positive SFT targets intentionally omit them.
    training_spec = training_spec.model_copy(update={"notes": []})

    messages = [
        TrainingMessage.model_validate(message)
        for message in build_scene_planner_messages(
            prompt=manifest.prompt,
            asset_ids=hit_ids,
            asset_context=[hit.planner_context() for hit in hits],
        )
    ]
    messages.append(
        TrainingMessage(
            role="assistant",
            content=canonical_json_bytes(training_spec).decode("utf-8"),
        )
    )
    content_identity = canonical_sha256(
        {
            "task_type": "render_spec_planning",
            "messages": [message.model_dump(mode="json") for message in messages],
        }
    )
    payload = {
        "schema_version": "0.1",
        "example_id": f"plan_{content_identity[:32]}",
        "task_type": "render_spec_planning",
        "source_group_id": source_group_id or manifest.workflow_id,
        "source_manifest_path": _portable_relative(manifest_path, source_root),
        "source_manifest_sha256": manifest_sha256,
        "source_artifacts": [
            TrainingArtifactReference(
                role=role,
                path=_portable_relative(artifact_paths[role], source_root),
                sha256=artifacts[role].sha256,
            ).model_dump(mode="json")
            for role in training_artifact_roles
        ],
        "messages": [message.model_dump(mode="json") for message in messages],
        "response_contract": "render-spec",
        "response_sha256": canonical_sha256(training_spec),
    }
    payload["example_sha256"] = canonical_sha256(payload)
    return TrainingExample.model_validate(payload)


def _split_group_assignments(
    examples: list[TrainingExample],
    *,
    seed: str,
    validation_fraction: float,
    test_fraction: float,
) -> dict[str, str]:
    groups = sorted(
        {example.source_group_id for example in examples},
        key=lambda group: (hashlib.sha256(f"{seed}:{group}".encode()).hexdigest(), group),
    )
    if len(groups) < 3:
        return {group: "train" for group in groups}

    validation_count = max(1, round(len(groups) * validation_fraction))
    test_count = max(1, round(len(groups) * test_fraction))
    while validation_count + test_count >= len(groups):
        if validation_count >= test_count and validation_count > 1:
            validation_count -= 1
        elif test_count > 1:
            test_count -= 1
        else:
            break
    assignments = {group: "train" for group in groups}
    for group in groups[:test_count]:
        assignments[group] = "test"
    for group in groups[test_count : test_count + validation_count]:
        assignments[group] = "validation"
    return assignments


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _readiness_reasons(
    *,
    integrity_status: str,
    split_counts: dict[str, int],
    thresholds: TrainingReadinessThresholds,
) -> list[str]:
    reasons: list[str] = []
    if integrity_status != "passed":
        reasons.append("dataset integrity audit failed")
    for name, required in (
        ("train", thresholds.qlora_smoke_train),
        ("validation", thresholds.qlora_smoke_validation),
        ("test", thresholds.qlora_smoke_test),
    ):
        actual = split_counts.get(name, 0)
        if actual < required:
            reasons.append(f"QLoRA smoke gate needs {required} {name} examples; found {actual}")
    for name, required in (
        ("train", thresholds.product_train),
        ("validation", thresholds.product_validation),
        ("test", thresholds.product_test),
    ):
        actual = split_counts.get(name, 0)
        if actual < required:
            reasons.append(f"product gate needs {required} {name} examples; found {actual}")
    return reasons


def audit_training_dataset(
    manifest_path: str | Path,
    *,
    source_root: str | Path | None = None,
) -> TrainingDatasetAuditReport:
    """Verify split bytes, example contracts, grouping, membership, and readiness."""

    path = Path(manifest_path).expanduser().resolve()
    if not path.is_file():
        raise TrainingDataError(f"training dataset manifest does not exist: {path}")
    try:
        manifest = TrainingDatasetManifest.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise TrainingDataError(f"invalid training dataset manifest {path}: {exc}") from exc

    root = path.parent
    issues: list[str] = []
    file_hash_error_count = 0
    manifest_membership_error_count = 0
    invalid_example_count = 0
    valid_example_count = 0
    split_example_ids: dict[str, list[str]] = {}
    split_group_ids: dict[str, set[str]] = {}
    actual_split_counts: dict[str, int] = {}
    all_examples: list[TrainingExample] = []
    source = Path(source_root).expanduser().resolve() if source_root is not None else None
    campaign_allowed: dict[str, str] = {}
    campaign_groups: dict[str, str] = {}
    campaign_ids: set[str] = set()
    campaign_identities: set[tuple[str, str, str]] = set()
    if source is not None and manifest.campaign_run_path is not None:
        try:
            _, run_hash, campaign_allowed = _load_campaign_eligibility(
                campaign_run_path=source / manifest.campaign_run_path, source_root=source,
            )
            if run_hash != manifest.campaign_run_sha256:
                raise TrainingDataError("original campaign hash no longer matches the dataset")
        except (OSError, ValueError, TrainingDataError) as exc:
            file_hash_error_count += 1
            issues.append(f"legacy campaign: {_bounded_detail(exc)}")
    for reference in manifest.campaign_sources or []:
        try:
            snapshot_path = (root / reference.snapshot_path).resolve()
            _portable_relative(snapshot_path, root)
            snapshot = snapshot_path.read_bytes()
            if hashlib.sha256(snapshot).hexdigest() != reference.sha256:
                raise TrainingDataError("snapshot SHA-256 mismatch")
            run = TrainingCampaignRun.model_validate_json(snapshot)
            if run.campaign_id in campaign_ids:
                raise TrainingDataError("duplicate campaign identity")
            campaign_ids.add(run.campaign_id)
            campaign_identities.add((
                run.project_name, run.project_descriptor_sha256, run.asset_catalog_sha256,
            ))
            if len(campaign_identities) > 1:
                raise TrainingDataError("campaign project or asset catalog identities disagree")
            allowed, groups = _campaign_membership(run, reference.source_run_path)
            if set(campaign_allowed).intersection(allowed):
                raise TrainingDataError("campaigns repeat an eligible workflow manifest")
            campaign_allowed.update(allowed)
            campaign_groups.update(groups)
            if source is not None:
                original = (source / reference.source_run_path).resolve()
                _portable_relative(original, source)
                if _sha256_file(original) != reference.sha256:
                    raise TrainingDataError("original campaign no longer matches its snapshot")
        except (OSError, ValueError, TrainingDataError) as exc:
            file_hash_error_count += 1
            issues.append(f"campaign {reference.source_run_path}: {_bounded_detail(exc)}")

    for split in manifest.splits:
        split_path = (root / split.jsonl_path).resolve()
        try:
            split_path.relative_to(root.resolve())
        except ValueError:
            file_hash_error_count += 1
            issues.append(f"{split.name} JSONL path escapes the dataset directory")
            split_example_ids[split.name] = []
            split_group_ids[split.name] = set()
            actual_split_counts[split.name] = 0
            continue
        if not split_path.is_file():
            file_hash_error_count += 1
            issues.append(f"{split.name} JSONL file is missing")
            split_example_ids[split.name] = []
            split_group_ids[split.name] = set()
            actual_split_counts[split.name] = 0
            continue
        if _sha256_file(split_path) != split.sha256:
            file_hash_error_count += 1
            issues.append(f"{split.name} JSONL SHA-256 does not match the manifest")

        examples: list[TrainingExample] = []
        try:
            lines = split_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            file_hash_error_count += 1
            issues.append(f"cannot read {split.name} JSONL: {_bounded_detail(exc)}")
            lines = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                invalid_example_count += 1
                issues.append(f"{split.name} line {line_number} is empty")
                continue
            try:
                examples.append(TrainingExample.model_validate_json(line))
                valid_example_count += 1
            except ValidationError as exc:
                invalid_example_count += 1
                issues.append(
                    f"{split.name} line {line_number} is invalid: {_bounded_detail(exc)}"
                )
        ids = [example.example_id for example in examples]
        all_examples.extend(examples)
        groups = {example.source_group_id for example in examples}
        task_counts = dict(Counter(example.task_type for example in examples))
        split_example_ids[split.name] = ids
        split_group_ids[split.name] = groups
        actual_split_counts[split.name] = len(examples)
        if ids != split.example_ids:
            manifest_membership_error_count += 1
            issues.append(f"{split.name} example IDs do not match the manifest")
        if sorted(groups) != sorted(split.source_group_ids):
            manifest_membership_error_count += 1
            issues.append(f"{split.name} source groups do not match the manifest")
        if task_counts != split.task_counts:
            manifest_membership_error_count += 1
            issues.append(f"{split.name} task counts do not match the manifest")
        if len(examples) != split.example_count:
            manifest_membership_error_count += 1
            issues.append(f"{split.name} example count does not match the manifest")

    accepted = {item.path: item.sha256 for item in manifest.accepted_source_manifests}
    if len(accepted) != len(manifest.accepted_source_manifests):
        manifest_membership_error_count += 1
        issues.append("accepted source manifests contain duplicate paths")
    example_sources = {example.source_manifest_path for example in all_examples}
    if example_sources != set(accepted):
        manifest_membership_error_count += 1
        issues.append("example source manifests do not match accepted source membership")
    for example in all_examples:
        relative = example.source_manifest_path
        if accepted.get(relative) != example.source_manifest_sha256:
            manifest_membership_error_count += 1
            issues.append(f"{example.example_id} source hash disagrees with accepted membership")
        content_id = "plan_" + canonical_sha256({
            "task_type": example.task_type,
            "messages": [message.model_dump(mode="json") for message in example.messages],
        })[:32]
        if example.example_id != content_id:
            invalid_example_count += 1
            issues.append(f"{example.example_id} does not match its content identity")
        if manifest.campaign_sources or (source is not None and manifest.campaign_run_path):
            if campaign_allowed.get(relative) != example.source_manifest_sha256:
                manifest_membership_error_count += 1
                issues.append(f"{relative} is not hash-approved by the campaign snapshots")
            if manifest.campaign_sources and campaign_groups.get(relative) != example.source_group_id:
                manifest_membership_error_count += 1
                issues.append(f"{relative} does not use its campaign case group")
        if source is not None:
            try:
                source_manifest = (source / relative).resolve()
                _portable_relative(source_manifest, source)
                if _sha256_file(source_manifest) != example.source_manifest_sha256:
                    raise TrainingDataError("original workflow manifest hash mismatch")
                workflow = RenderWorkflowManifest.model_validate_json(source_manifest.read_bytes())
                for artifact in workflow.input_artifacts + workflow.output_artifacts:
                    _verified_artifact_path(workflow_root=source_manifest.parent, artifact=artifact)
                rebuilt = _build_planner_example(
                    source_root=source, manifest_path=source_manifest,
                    manifest_sha256=example.source_manifest_sha256, manifest=workflow,
                    prefer_final_spec=bool(manifest.campaign_sources or manifest.campaign_run_path),
                    source_group_id=campaign_groups.get(relative),
                )
                if rebuilt != example:
                    raise TrainingDataError("example cannot be reproduced from original evidence")
            except (OSError, ValueError, TrainingDataError, _SourceRejected) as exc:
                file_hash_error_count += 1
                issues.append(f"source {relative}: {_bounded_detail(exc)}")

    all_ids = [
        example_id
        for split_name in SPLIT_NAMES
        for example_id in split_example_ids.get(split_name, [])
    ]
    duplicate_example_count = sum(
        count - 1 for count in Counter(all_ids).values() if count > 1
    )
    if duplicate_example_count:
        issues.append(f"found {duplicate_example_count} duplicate example IDs")
    cross_split_example_ids: set[str] = set()
    cross_split_group_ids: set[str] = set()
    for index, left_name in enumerate(SPLIT_NAMES):
        for right_name in SPLIT_NAMES[index + 1 :]:
            cross_split_example_ids.update(
                set(split_example_ids.get(left_name, []))
                & set(split_example_ids.get(right_name, []))
            )
            cross_split_group_ids.update(
                split_group_ids.get(left_name, set())
                & split_group_ids.get(right_name, set())
            )
    if cross_split_example_ids:
        issues.append("example IDs overlap across dataset splits")
    if cross_split_group_ids:
        issues.append("source workflow groups overlap across dataset splits")
    if valid_example_count != manifest.total_example_count:
        manifest_membership_error_count += 1
        issues.append("valid example total does not match the dataset manifest")

    integrity_error_count = (
        invalid_example_count
        + file_hash_error_count
        + manifest_membership_error_count
        + duplicate_example_count
        + len(cross_split_example_ids)
        + len(cross_split_group_ids)
    )
    integrity_status = "passed" if integrity_error_count == 0 else "failed"
    thresholds = TrainingReadinessThresholds()
    qlora_smoke_ready = integrity_status == "passed" and (
        actual_split_counts.get("train", 0) >= thresholds.qlora_smoke_train
        and actual_split_counts.get("validation", 0)
        >= thresholds.qlora_smoke_validation
        and actual_split_counts.get("test", 0) >= thresholds.qlora_smoke_test
    )
    product_finetune_ready = integrity_status == "passed" and (
        actual_split_counts.get("train", 0) >= thresholds.product_train
        and actual_split_counts.get("validation", 0) >= thresholds.product_validation
        and actual_split_counts.get("test", 0) >= thresholds.product_test
    )
    return TrainingDatasetAuditReport(
        dataset_id=manifest.dataset_id,
        manifest_sha256=_sha256_file(path),
        dataset_sha256=manifest.dataset_sha256,
        source_evidence_checked=source is not None,
        campaign_snapshot_count=len(manifest.campaign_sources or []),
        integrity_status=integrity_status,
        split_counts={name: actual_split_counts.get(name, 0) for name in SPLIT_NAMES},
        valid_example_count=valid_example_count,
        invalid_example_count=invalid_example_count,
        file_hash_error_count=file_hash_error_count,
        manifest_membership_error_count=manifest_membership_error_count,
        duplicate_example_count=duplicate_example_count,
        cross_split_example_overlap_count=len(cross_split_example_ids),
        cross_split_group_overlap_count=len(cross_split_group_ids),
        thresholds=thresholds,
        qlora_smoke_ready=qlora_smoke_ready,
        product_finetune_ready=product_finetune_ready,
        readiness_reasons=_readiness_reasons(
            integrity_status=integrity_status,
            split_counts=actual_split_counts,
            thresholds=thresholds,
        ),
        issues=issues[:256],
    )


def build_training_dataset(
    *,
    source_root: str | Path,
    output_directory: str | Path,
    dataset_id: str = DEFAULT_DATASET_ID,
    split_seed: str = DEFAULT_SPLIT_SEED,
    validation_group_fraction: float = 0.1,
    test_group_fraction: float = 0.1,
    campaign_run_path: str | Path | None = None,
    campaign_run_paths: list[str | Path] | None = None,
) -> TrainingDatasetBuildResult:
    """Build immutable JSONL splits and immediately audit the result."""

    source = Path(source_root).expanduser().resolve()
    output = Path(output_directory).expanduser().resolve()
    try:
        dataset_id = _IDENTIFIER_ADAPTER.validate_python(dataset_id)
        split_seed = _SHORT_TEXT_ADAPTER.validate_python(split_seed)
    except ValidationError as exc:
        raise TrainingDataError(f"invalid training dataset identity: {exc}") from exc
    if output == source:
        raise TrainingDataError("training output directory cannot equal the source root")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise TrainingDataError(f"training output directory is not empty: {output}")
    if not 0 <= validation_group_fraction < 0.5:
        raise TrainingDataError("validation group fraction must be at least 0 and below 0.5")
    if not 0 <= test_group_fraction < 0.5:
        raise TrainingDataError("test group fraction must be at least 0 and below 0.5")
    if validation_group_fraction + test_group_fraction >= 1:
        raise TrainingDataError("validation and test fractions must leave room for training")

    if campaign_run_path is not None and campaign_run_paths is not None:
        raise TrainingDataError("choose legacy campaign_run_path or campaign_run_paths, not both")
    campaign_evidence = (
        _load_campaign_sources(campaign_run_paths, source)
        if campaign_run_paths is not None else []
    )
    campaign_groups: dict[str, str] = {}
    campaign_run_relative: str | None = None
    campaign_run_sha256: str | None = None
    campaign_allowed: dict[str, str] | None = None
    if campaign_run_path is not None:
        (
            campaign_run_relative,
            campaign_run_sha256,
            campaign_allowed,
        ) = _load_campaign_eligibility(
            campaign_run_path=campaign_run_path,
            source_root=source,
        )

    if campaign_evidence:
        campaign_allowed = {}
        manifest_paths = []
        for item in campaign_evidence:
            campaign_allowed.update(item.allowed)
            campaign_groups.update(item.groups)
            manifest_paths.extend(discover_workflow_manifests(
                (source / item.reference.source_run_path).parent,
            ))
        manifest_paths.sort(key=lambda p: _portable_relative(p, source).casefold())
    else:
        manifest_paths = discover_workflow_manifests(source)
    if campaign_allowed is not None:
        discovered = {_portable_relative(path, source) for path in manifest_paths}
        missing_eligible = sorted(set(campaign_allowed) - discovered)
        if missing_eligible:
            raise TrainingDataError(
                "campaign eligible workflow manifests are missing: "
                + ", ".join(missing_eligible)
            )
    examples: list[TrainingExample] = []
    accepted_manifests: list[TrainingArtifactReference] = []
    skipped_sources: list[TrainingSourceSkip] = []
    seen_content_ids: set[str] = set()
    for manifest_path in manifest_paths:
        relative_manifest = _portable_relative(manifest_path, source)
        manifest_sha256 = _sha256_file(manifest_path)
        if campaign_allowed is not None:
            expected_sha256 = campaign_allowed.get(relative_manifest)
            if expected_sha256 is None:
                skipped_sources.append(
                    TrainingSourceSkip(
                        manifest_path=relative_manifest,
                        reason="campaign_not_eligible",
                        detail="workflow was not approved by the supplied campaign run",
                    )
                )
                continue
            if manifest_sha256 != expected_sha256:
                skipped_sources.append(
                    TrainingSourceSkip(
                        manifest_path=relative_manifest,
                        reason="hash_mismatch",
                        detail="workflow manifest no longer matches campaign eligibility",
                    )
                )
                continue
        try:
            manifest = RenderWorkflowManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError) as exc:
            skipped_sources.append(
                TrainingSourceSkip(
                    manifest_path=relative_manifest,
                    reason="invalid_manifest",
                    detail=_bounded_detail(exc),
                )
            )
            continue
        if manifest.status != "succeeded":
            skipped_sources.append(
                TrainingSourceSkip(
                    manifest_path=relative_manifest,
                    reason="nonpassing_workflow",
                    detail=(
                        f"workflow status is {manifest.status!r} with stop reason "
                        f"{manifest.stop_reason!r}; positive SFT requires 'succeeded'"
                    ),
                )
            )
            continue
        try:
            example = _build_planner_example(
                source_root=source,
                manifest_path=manifest_path,
                manifest_sha256=manifest_sha256,
                manifest=manifest,
                prefer_final_spec=campaign_allowed is not None,
                source_group_id=campaign_groups.get(relative_manifest),
            )
        except _SourceRejected as exc:
            skipped_sources.append(
                TrainingSourceSkip(
                    manifest_path=relative_manifest,
                    reason=exc.reason,
                    detail=_bounded_detail(exc.detail),
                )
            )
            continue
        if example.example_id in seen_content_ids:
            skipped_sources.append(
                TrainingSourceSkip(
                    manifest_path=relative_manifest,
                    reason="duplicate_example",
                    detail=f"example content duplicates {example.example_id}",
                )
            )
            continue
        seen_content_ids.add(example.example_id)
        examples.append(example)
        accepted_manifests.append(
            TrainingArtifactReference(
                role="workflow_manifest",
                path=relative_manifest,
                sha256=manifest_sha256,
            )
        )

    assignments = _split_group_assignments(
        examples,
        seed=split_seed,
        validation_fraction=validation_group_fraction,
        test_fraction=test_group_fraction,
    )
    split_examples = {
        name: sorted(
            [
                example
                for example in examples
                if assignments[example.source_group_id] == name
            ],
            key=lambda example: example.example_id,
        )
        for name in SPLIT_NAMES
    }

    output.mkdir(parents=True, exist_ok=True)
    for item in campaign_evidence:
        snapshot_path = output / item.reference.snapshot_path
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_bytes(item.snapshot)
    splits: list[TrainingDatasetSplit] = []
    for name in SPLIT_NAMES:
        relative_path = f"splits/{name}.jsonl"
        split_path = output / relative_path
        split_path.parent.mkdir(parents=True, exist_ok=True)
        split_path.write_bytes(
            b"".join(canonical_json_bytes(example) + b"\n" for example in split_examples[name])
        )
        task_counts = dict(Counter(example.task_type for example in split_examples[name]))
        splits.append(
            TrainingDatasetSplit(
                name=name,
                jsonl_path=relative_path,
                sha256=_sha256_file(split_path),
                example_count=len(split_examples[name]),
                example_ids=[example.example_id for example in split_examples[name]],
                source_group_ids=sorted(
                    {example.source_group_id for example in split_examples[name]}
                ),
                task_counts=task_counts,
            )
        )

    manifest_payload = {
        "schema_version": "0.1",
        "builder_version": "0.3" if campaign_evidence else BUILDER_VERSION,
        "dataset_id": dataset_id,
        "split_strategy": "group_sha256_v1",
        "split_group_key": "source_group_id",
        "split_seed": split_seed,
        "validation_group_fraction": validation_group_fraction,
        "test_group_fraction": test_group_fraction,
        "discovered_source_manifest_count": len(manifest_paths),
        "accepted_source_manifests": [
            item.model_dump(mode="json") for item in accepted_manifests
        ],
        "skipped_sources": [item.model_dump(mode="json") for item in skipped_sources],
        "total_example_count": len(examples),
        "task_counts": dict(Counter(example.task_type for example in examples)),
        "splits": [split.model_dump(mode="json") for split in splits],
    }
    if campaign_run_relative is not None and campaign_run_sha256 is not None:
        manifest_payload["campaign_run_path"] = campaign_run_relative
        manifest_payload["campaign_run_sha256"] = campaign_run_sha256
    if campaign_evidence:
        manifest_payload["campaign_sources"] = [
            item.reference.model_dump(mode="json") for item in campaign_evidence
        ]
    manifest_payload["dataset_sha256"] = canonical_sha256(manifest_payload)
    manifest = TrainingDatasetManifest.model_validate(manifest_payload)
    manifest_path = output / MANIFEST_FILENAME
    _write_json(manifest_path, manifest.model_dump(mode="json"))
    audit = audit_training_dataset(
        manifest_path, source_root=source if campaign_evidence else None,
    )
    audit_path = output / AUDIT_FILENAME
    _write_json(audit_path, audit.model_dump(mode="json"))
    return TrainingDatasetBuildResult(
        manifest_path=manifest_path,
        audit_path=audit_path,
        manifest=manifest,
        audit=audit,
    )
