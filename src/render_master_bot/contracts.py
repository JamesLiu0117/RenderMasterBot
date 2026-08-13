"""Versioned contracts shared by the AI core and engine adapters.

These models are deliberately strict. They are the durable hand-off boundary
between probabilistic model output and deterministic renderer code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, JsonValue, model_validator

from render_master_bot.models import (
    Identifier,
    PositiveFiniteFloat,
    RenderSpec,
    StrictModel,
    UnitFloat,
    Vector3,
)


ShortText = Annotated[str, Field(min_length=1, max_length=240)]
LongText = Annotated[str, Field(min_length=1, max_length=4000)]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def _relative_artifact_path(value: str) -> str:
    """Require portable run-relative paths without using regex lookarounds."""

    parts = value.split("/")
    if "\\" in value or value.startswith("/") or (len(value) > 1 and value[1] == ":"):
        raise ValueError("artifact path must be relative and use forward slashes")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("artifact path cannot contain empty, dot, or parent segments")
    return value


RelativeArtifactPath = Annotated[
    str,
    Field(min_length=1, max_length=500),
    AfterValidator(_relative_artifact_path),
]


class SourceReference(StrictModel):
    """Traceable source used to create a knowledge or asset record."""

    source_id: Identifier
    source_type: Literal[
        "paper",
        "documentation",
        "standard",
        "repository",
        "book",
        "production_note",
        "dataset",
        "generated",
        "other",
    ]
    title: ShortText
    uri: Annotated[str, Field(max_length=1000)] | None = None
    doi: Annotated[str, Field(max_length=200)] | None = None
    license: Annotated[str, Field(max_length=200)] | None = None
    content_sha256: Sha256 | None = None


class EngineMapping(StrictModel):
    """How a general graphics technique maps onto an engine control."""

    engine: Literal["unreal", "blender", "generic"]
    component: ShortText
    property_path: ShortText | None = None
    guidance: LongText


class TechniqueCard(StrictModel):
    """Curated, cited graphics knowledge suitable for retrieval."""

    schema_version: Literal["0.1"] = "0.1"
    technique_id: Identifier
    name: ShortText
    summary: LongText
    problem_types: list[ShortText] = Field(min_length=1, max_length=32)
    assumptions: list[ShortText] = Field(default_factory=list, max_length=32)
    inputs: list[ShortText] = Field(default_factory=list, max_length=32)
    outputs: list[ShortText] = Field(default_factory=list, max_length=32)
    benefits: list[ShortText] = Field(default_factory=list, max_length=32)
    failure_modes: list[ShortText] = Field(default_factory=list, max_length=32)
    engine_mappings: list[EngineMapping] = Field(default_factory=list, max_length=32)
    sources: list[SourceReference] = Field(min_length=1, max_length=32)
    tags: list[Identifier] = Field(default_factory=list, max_length=64)


class Dimensions3(StrictModel):
    x: PositiveFiniteFloat
    y: PositiveFiniteFloat
    z: PositiveFiniteFloat


class AssetCard(StrictModel):
    """Searchable description of one renderer-resolvable asset."""

    schema_version: Literal["0.1"] = "0.1"
    asset_id: Identifier
    engine: Literal["unreal", "blender", "generic"] = "unreal"
    engine_path: Annotated[str, Field(min_length=1, max_length=500)]
    display_name: ShortText
    asset_type: Literal[
        "static_mesh",
        "skeletal_mesh",
        "material",
        "texture",
        "light",
        "camera",
        "animation",
        "blueprint",
        "level",
        "other",
    ]
    description: LongText | None = None
    tags: list[Identifier] = Field(default_factory=list, max_length=64)
    dimensions_cm: Dimensions3 | None = None
    pivot_offset_cm: Vector3 = Field(default_factory=Vector3)
    material_slots: list[ShortText] = Field(default_factory=list, max_length=64)
    license: Annotated[str, Field(max_length=200)] | None = None
    sources: list[SourceReference] = Field(default_factory=list, max_length=16)


class PatchOperation(StrictModel):
    """A bounded JSON Patch subset; metadata and identity fields are immutable."""

    op: Literal["add", "replace", "remove"]
    path: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            pattern=r"^/(objects|camera|lights|render|notes)(/.*)?$",
        ),
    ]
    value: JsonValue | None = None

    @model_validator(mode="after")
    def value_matches_operation(self) -> "PatchOperation":
        supplied = "value" in self.model_fields_set
        if self.op == "remove" and supplied:
            raise ValueError("remove operations must omit value")
        if self.op != "remove" and not supplied:
            raise ValueError("add and replace operations must supply value")
        if any(token in self.path for token in ("..", "__")):
            raise ValueError("patch path contains a forbidden token")
        return self


class ModelIdentity(StrictModel):
    provider: Literal["ollama", "local", "external", "human"]
    model: ShortText
    revision: Annotated[str, Field(max_length=240)] | None = None


class RenderSpecPatch(StrictModel):
    """Auditable proposal to modify an existing RenderSpec."""

    schema_version: Literal["0.1"] = "0.1"
    base_spec_sha256: Sha256
    rationale: LongText
    proposed_by: ModelIdentity
    operations: list[PatchOperation] = Field(min_length=1, max_length=64)


class EvaluationIssue(StrictModel):
    issue_id: Identifier
    category: Literal[
        "asset",
        "camera",
        "composition",
        "geometry",
        "lighting",
        "material",
        "render_quality",
        "performance",
        "other",
    ]
    severity: Literal["info", "warning", "error", "blocking"]
    confidence: UnitFloat
    message: LongText
    object_ids: list[Identifier] = Field(default_factory=list, max_length=64)
    evidence_paths: list[RelativeArtifactPath] = Field(default_factory=list, max_length=32)


class EvaluationReport(StrictModel):
    """Structured output from visual and deterministic evaluators."""

    schema_version: Literal["0.1"] = "0.1"
    render_spec_sha256: Sha256
    evaluator: ModelIdentity
    evaluation_stage: Literal["preflight", "preview", "final", "benchmark"] = "preview"
    verdict: Literal["pass", "needs_review", "fail"]
    summary: LongText
    preview_paths: list[RelativeArtifactPath] = Field(default_factory=list, max_length=32)
    issues: list[EvaluationIssue] = Field(default_factory=list, max_length=128)
    suggested_patch: RenderSpecPatch | None = None

    @model_validator(mode="after")
    def verdict_matches_issues(self) -> "EvaluationReport":
        if self.evaluation_stage in {"preview", "final"} and not self.preview_paths:
            raise ValueError("preview and final evaluations require preview_paths")
        severe = any(issue.severity in {"error", "blocking"} for issue in self.issues)
        if self.verdict == "pass" and severe:
            raise ValueError("pass verdict cannot contain error or blocking issues")
        if self.verdict == "fail" and not severe:
            raise ValueError("fail verdict requires an error or blocking issue")
        if (
            self.suggested_patch is not None
            and self.suggested_patch.base_spec_sha256 != self.render_spec_sha256
        ):
            raise ValueError("suggested patch must target the evaluated RenderSpec")
        return self


class CapabilityManifest(StrictModel):
    """Observed capabilities of a concrete renderer project."""

    schema_version: Literal["0.1"] = "0.1"
    engine: Literal["unreal", "blender", "mock"]
    engine_version: ShortText
    project_name: ShortText
    coordinate_system: Literal["unreal_z_up_cm", "blender_z_up_m", "mock"]
    python_available: bool
    movie_render_queue_available: bool = False
    movie_render_graph_available: bool = False
    enabled_plugins: list[ShortText] = Field(default_factory=list, max_length=256)
    supported_asset_types: list[ShortText] = Field(default_factory=list, max_length=64)
    supported_render_passes: list[ShortText] = Field(default_factory=list, max_length=64)
    captured_at: datetime


class ArtifactRecord(StrictModel):
    role: Identifier
    path: RelativeArtifactPath
    sha256: Sha256 | None = None


class RunTiming(StrictModel):
    stage: Identifier
    duration_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]


class RunManifest(StrictModel):
    """Reproducibility record for one planning, rendering, or evaluation run."""

    schema_version: Literal["0.1"] = "0.1"
    run_id: Identifier
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    started_at: datetime
    finished_at: datetime | None = None
    planner: ModelIdentity | None = None
    evaluator: ModelIdentity | None = None
    render_spec_sha256: Sha256 | None = None
    capability_manifest_sha256: Sha256 | None = None
    input_artifacts: list[ArtifactRecord] = Field(default_factory=list, max_length=256)
    output_artifacts: list[ArtifactRecord] = Field(default_factory=list, max_length=256)
    timings: list[RunTiming] = Field(default_factory=list, max_length=64)
    errors: list[LongText] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> "RunManifest":
        terminal = self.status in {"succeeded", "failed", "cancelled"}
        if terminal and self.finished_at is None:
            raise ValueError("terminal runs require finished_at")
        if not terminal and self.finished_at is not None:
            raise ValueError("non-terminal runs cannot have finished_at")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot be earlier than started_at")
        if self.status == "failed" and not self.errors:
            raise ValueError("failed runs require at least one error")
        return self


CONTRACT_MODELS = {
    "render-spec": RenderSpec,
    "technique-card": TechniqueCard,
    "asset-card": AssetCard,
    "render-spec-patch": RenderSpecPatch,
    "evaluation-report": EvaluationReport,
    "capability-manifest": CapabilityManifest,
    "run-manifest": RunManifest,
}
