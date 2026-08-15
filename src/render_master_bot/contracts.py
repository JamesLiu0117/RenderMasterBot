"""Versioned contracts shared by the AI core and engine adapters.

These models are deliberately strict. They are the durable hand-off boundary
between probabilistic model output and deterministic renderer code.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, JsonValue, model_validator

from render_master_bot.models import (
    Identifier,
    NonNegativeFiniteFloat,
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


class CorrectionDecision(StrictModel):
    """Auditable result of deciding whether an evaluation can be repaired safely."""

    schema_version: Literal["0.1"] = "0.1"
    render_spec_sha256: Sha256
    evaluation_report_sha256: Sha256
    planner: ModelIdentity
    outcome: Literal["patch", "unresolved"]
    rationale: LongText
    patch: RenderSpecPatch | None = None
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def outcome_matches_payload(self) -> "CorrectionDecision":
        if self.outcome == "patch":
            if self.patch is None:
                raise ValueError("patch outcomes require a RenderSpecPatch")
            if self.patch.base_spec_sha256 != self.render_spec_sha256:
                raise ValueError("correction patch must target the decision RenderSpec")
        else:
            if self.patch is not None:
                raise ValueError("unresolved outcomes cannot contain a patch")
            if not self.missing_capabilities:
                raise ValueError("unresolved outcomes require missing_capabilities")
        return self


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


class ImageMetricExpectation(StrictModel):
    """Accepted range for one deterministic image statistic."""

    metric: Literal[
        "mean_luminance",
        "luminance_stddev",
        "p05_luminance",
        "p95_luminance",
        "dark_pixel_fraction",
        "clipped_pixel_fraction",
        "foreground_fraction",
        "center_luminance",
        "border_luminance",
    ]
    minimum: UnitFloat | None = None
    maximum: UnitFloat | None = None

    @model_validator(mode="after")
    def range_is_bounded(self) -> "ImageMetricExpectation":
        if self.minimum is None and self.maximum is None:
            raise ValueError("image metric expectations require a minimum or maximum")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("image metric expectation minimum cannot exceed maximum")
        return self


class VisualBenchmarkExpectation(StrictModel):
    """Human-owned ground truth for one visual evaluator benchmark case."""

    accepted_verdicts: list[Literal["pass", "needs_review", "fail"]] = Field(
        min_length=1,
        max_length=3,
    )
    required_issue_categories: list[
        Literal[
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
    ] = Field(default_factory=list, max_length=9)
    forbidden_issue_categories: list[
        Literal[
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
    ] = Field(default_factory=list, max_length=9)
    image_metrics: list[ImageMetricExpectation] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def labels_are_unambiguous(self) -> "VisualBenchmarkExpectation":
        if len(self.accepted_verdicts) != len(set(self.accepted_verdicts)):
            raise ValueError("accepted benchmark verdicts must be unique")
        required = set(self.required_issue_categories)
        forbidden = set(self.forbidden_issue_categories)
        if len(required) != len(self.required_issue_categories):
            raise ValueError("required benchmark issue categories must be unique")
        if len(forbidden) != len(self.forbidden_issue_categories):
            raise ValueError("forbidden benchmark issue categories must be unique")
        if overlap := sorted(required & forbidden):
            raise ValueError(
                "benchmark issue categories cannot be both required and forbidden: "
                + ", ".join(overlap)
            )
        metrics = [item.metric for item in self.image_metrics]
        if len(metrics) != len(set(metrics)):
            raise ValueError("benchmark image metric expectations must be unique")
        return self


class VisualBenchmarkCase(StrictModel):
    case_id: Identifier
    description: ShortText
    run_directory: RelativeArtifactPath
    expectation: VisualBenchmarkExpectation


class VisualBenchmarkSuite(StrictModel):
    """Portable collection of labeled, completed Unreal preview runs."""

    schema_version: Literal["0.1"] = "0.1"
    suite_id: Identifier
    description: LongText
    repetitions: Annotated[int, Field(ge=1, le=5)] = 1
    cases: list[VisualBenchmarkCase] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def cases_are_unique(self) -> "VisualBenchmarkSuite":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("visual benchmark case IDs must be unique")
        run_directories = [case.run_directory.casefold() for case in self.cases]
        if len(run_directories) != len(set(run_directories)):
            raise ValueError("visual benchmark run directories must be unique")
        return self


class ImageStatistics(StrictModel):
    """Deterministic pixel evidence extracted from one verified preview PNG."""

    sha256: Sha256
    width_px: Annotated[int, Field(gt=0, le=16384)]
    height_px: Annotated[int, Field(gt=0, le=16384)]
    sampled_pixels: Annotated[int, Field(gt=0)]
    mean_luminance: UnitFloat
    luminance_stddev: UnitFloat
    p05_luminance: UnitFloat
    p95_luminance: UnitFloat
    dark_pixel_fraction: UnitFloat
    clipped_pixel_fraction: UnitFloat
    foreground_fraction: UnitFloat
    center_luminance: UnitFloat
    border_luminance: UnitFloat
    blank_like: bool
    underexposed_like: bool
    overexposed_like: bool


class VisualBenchmarkObservation(StrictModel):
    repetition: Annotated[int, Field(ge=1, le=5)]
    status: Literal["valid", "invalid"]
    duration_seconds: NonNegativeFiniteFloat
    report: EvaluationReport | None = None
    error: LongText | None = None
    verdict_matched: bool | None = None
    required_categories_matched: bool | None = None
    forbidden_categories_absent: bool | None = None

    @model_validator(mode="after")
    def status_matches_payload(self) -> "VisualBenchmarkObservation":
        checks = (
            self.verdict_matched,
            self.required_categories_matched,
            self.forbidden_categories_absent,
        )
        if self.status == "valid":
            if (
                self.report is None
                or self.error is not None
                or any(value is None for value in checks)
            ):
                raise ValueError("valid benchmark observations require a report and match results")
        elif (
            self.report is not None
            or self.error is None
            or any(value is not None for value in checks)
        ):
            raise ValueError("invalid benchmark observations require only an error")
        return self


class VisualBenchmarkCaseResult(StrictModel):
    case_id: Identifier
    run_directory: RelativeArtifactPath
    image_statistics: ImageStatistics
    image_expectation_failures: list[LongText] = Field(default_factory=list, max_length=32)
    observations: list[VisualBenchmarkObservation] = Field(min_length=1, max_length=5)
    verdict_stable: bool
    contradictions: list[LongText] = Field(default_factory=list, max_length=32)
    passed: bool

    @model_validator(mode="after")
    def passed_matches_evidence(self) -> "VisualBenchmarkCaseResult":
        observations_passed = all(
            observation.status == "valid"
            and observation.verdict_matched
            and observation.required_categories_matched
            and observation.forbidden_categories_absent
            for observation in self.observations
        )
        expected = (
            not self.image_expectation_failures
            and self.verdict_stable
            and observations_passed
        )
        if self.passed != expected:
            raise ValueError("benchmark case passed flag does not match its evidence")
        return self


class VisualBenchmarkReport(StrictModel):
    """Auditable accuracy, stability, and contradiction summary for one model."""

    schema_version: Literal["0.1"] = "0.1"
    suite_id: Identifier
    suite_sha256: Sha256
    evaluator: ModelIdentity
    completed_at: datetime
    case_count: Annotated[int, Field(gt=0, le=64)]
    passed_case_count: Annotated[int, Field(ge=0, le=64)]
    observation_count: Annotated[int, Field(gt=0, le=320)]
    valid_observation_count: Annotated[int, Field(ge=0, le=320)]
    case_accuracy: UnitFloat
    verdict_stability: UnitFloat
    contradiction_count: Annotated[int, Field(ge=0)]
    total_duration_seconds: NonNegativeFiniteFloat
    cases: list[VisualBenchmarkCaseResult] = Field(min_length=1, max_length=64)
    passed: bool

    @model_validator(mode="after")
    def summary_matches_cases(self) -> "VisualBenchmarkReport":
        if self.case_count != len(self.cases):
            raise ValueError("benchmark case_count does not match cases")
        if self.passed_case_count != sum(case.passed for case in self.cases):
            raise ValueError("benchmark passed_case_count does not match cases")
        observed = sum(len(case.observations) for case in self.cases)
        if self.observation_count != observed:
            raise ValueError("benchmark observation_count does not match cases")
        valid = sum(
            observation.status == "valid"
            for case in self.cases
            for observation in case.observations
        )
        if self.valid_observation_count != valid:
            raise ValueError("benchmark valid_observation_count does not match cases")
        contradictions = sum(len(case.contradictions) for case in self.cases)
        if self.contradiction_count != contradictions:
            raise ValueError("benchmark contradiction_count does not match cases")
        expected_accuracy = self.passed_case_count / self.case_count
        if not math.isclose(
            self.case_accuracy,
            expected_accuracy,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError("benchmark case_accuracy does not match cases")
        stable_cases = sum(case.verdict_stable for case in self.cases)
        expected_stability = stable_cases / self.case_count
        if not math.isclose(
            self.verdict_stability,
            expected_stability,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError("benchmark verdict_stability does not match cases")
        expected_duration = sum(
            observation.duration_seconds
            for case in self.cases
            for observation in case.observations
        )
        if not math.isclose(
            self.total_duration_seconds,
            expected_duration,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise ValueError("benchmark total_duration_seconds does not match observations")
        if self.passed != (self.passed_case_count == self.case_count):
            raise ValueError("benchmark passed flag does not match cases")
        return self


class CapabilityEvidence(StrictModel):
    """Auditable evidence supporting one capability assertion."""

    capability: Identifier
    status: Literal["confirmed", "inferred", "not_detected", "conflict"]
    source: Literal[
        "uproject",
        "project_log",
        "engine_build",
        "plugin_descriptor",
        "filesystem",
        "solution",
        "user_override",
    ]
    detail: LongText


class CapabilityManifest(StrictModel):
    """Observed capabilities of a concrete renderer project."""

    schema_version: Literal["0.1"] = "0.1"
    engine: Literal["unreal", "blender", "mock"]
    engine_version: ShortText
    engine_association: ShortText | None = None
    project_name: ShortText
    project_descriptor_sha256: Sha256 | None = None
    coordinate_system: Literal["unreal_z_up_cm", "blender_z_up_m", "mock"]
    probe_mode: Literal["static", "editor_runtime"] = "static"
    python_available: bool
    movie_render_queue_available: bool = False
    movie_render_graph_available: bool = False
    project_modules: list[ShortText] = Field(default_factory=list, max_length=128)
    enabled_plugins: list[ShortText] = Field(default_factory=list, max_length=256)
    supported_asset_types: list[ShortText] = Field(default_factory=list, max_length=64)
    supported_render_passes: list[ShortText] = Field(default_factory=list, max_length=64)
    evidence: list[CapabilityEvidence] = Field(default_factory=list, max_length=128)
    warnings: list[LongText] = Field(default_factory=list, max_length=64)
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
    "correction-decision": CorrectionDecision,
    "evaluation-report": EvaluationReport,
    "visual-benchmark-suite": VisualBenchmarkSuite,
    "visual-benchmark-report": VisualBenchmarkReport,
    "capability-manifest": CapabilityManifest,
    "run-manifest": RunManifest,
}
