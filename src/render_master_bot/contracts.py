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
    FiniteFloat,
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


class MaterialSlotContext(StrictModel):
    """One observed material slot on a selected Unreal mesh component."""

    slot_index: Annotated[int, Field(ge=0, le=63)]
    slot_name: ShortText
    current_material_path: Annotated[str, Field(min_length=1, max_length=500)] | None = None


class UnrealSelectionContext(StrictModel):
    """Read-only Editor evidence captured before an assistant action is proposed."""

    schema_version: Literal["0.1"] = "0.1"
    project_name: ShortText
    level_path: Annotated[str, Field(min_length=1, max_length=500)]
    actor_name: ShortText
    actor_path: Annotated[str, Field(min_length=1, max_length=500)]
    component_name: ShortText
    mesh_path: Annotated[str, Field(min_length=1, max_length=500)]
    material_slots: list[MaterialSlotContext] = Field(min_length=1, max_length=64)
    target_slot_index: Annotated[int, Field(ge=0, le=63)] | None = None

    @model_validator(mode="after")
    def material_slot_indices_are_unique(self) -> "UnrealSelectionContext":
        indices = [slot.slot_index for slot in self.material_slots]
        if len(indices) != len(set(indices)):
            raise ValueError("selection context material slot indices must be unique")
        if self.target_slot_index is not None and self.target_slot_index not in indices:
            raise ValueError("target material slot is not present in the selection context")
        return self


class MaterialCandidate(StrictModel):
    """Catalog-verified material returned by semantic retrieval."""

    rank: Annotated[int, Field(ge=1, le=100)]
    asset_id: Identifier
    display_name: ShortText
    engine_path: Annotated[str, Field(min_length=1, max_length=500)]
    similarity: Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]


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


class AssistantMaterialProposal(StrictModel):
    """Auditable, approval-gated material change for one selected component slot."""

    schema_version: Literal["0.1"] = "0.1"
    proposal_id: Identifier
    status: Literal["proposed", "unresolved"]
    request: LongText
    target: UnrealSelectionContext
    proposed_by: ModelIdentity
    selected_slot: MaterialSlotContext | None = None
    selected_material: MaterialCandidate | None = None
    alternatives: list[MaterialCandidate] = Field(default_factory=list, max_length=5)
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: Literal[True] = True
    auto_save: Literal[False] = False
    undo_supported: Literal[True] = True

    @model_validator(mode="after")
    def status_matches_material_action(self) -> "AssistantMaterialProposal":
        if self.status == "proposed":
            if self.selected_slot is None or self.selected_material is None:
                raise ValueError("proposed material actions require a slot and material")
            if self.missing_capabilities:
                raise ValueError("proposed material actions cannot report missing capabilities")
            slot_indices = {slot.slot_index for slot in self.target.material_slots}
            if self.selected_slot.slot_index not in slot_indices:
                raise ValueError("selected material slot is not present in the target context")
            if (
                self.target.target_slot_index is not None
                and self.selected_slot.slot_index != self.target.target_slot_index
            ):
                raise ValueError("selected material slot does not match the explicit target slot")
        elif (
            self.selected_slot is not None
            or self.selected_material is not None
            or not self.missing_capabilities
        ):
            raise ValueError(
                "unresolved material actions require missing capabilities and no selection"
            )
        return self


class ActorTransformSnapshot(StrictModel):
    """Finite world-space transform observed directly from one Unreal Actor."""

    location_cm: Vector3
    rotation_deg: Vector3
    scale: Vector3


class UnrealActorTransformContext(StrictModel):
    """Read-only Actor identity and Transform evidence captured by the Editor."""

    schema_version: Literal["0.1"] = "0.1"
    project_name: ShortText
    level_path: Annotated[str, Field(min_length=1, max_length=500)]
    actor_name: ShortText
    actor_path: Annotated[str, Field(min_length=1, max_length=1000)]
    actor_class: ShortText
    actor_guid: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    root_component_name: ShortText | None = None
    root_mobility: Literal["static", "stationary", "movable", "none"]
    is_editable: bool
    is_locked: bool
    transform: ActorTransformSnapshot


class TransformAxisEdit(StrictModel):
    """Model-facing per-axis edit; omitted axes are always preserved."""

    operation: Literal["preserve", "set", "add", "multiply"] = "preserve"
    x: FiniteFloat | None = None
    y: FiniteFloat | None = None
    z: FiniteFloat | None = None

    @model_validator(mode="after")
    def operation_matches_axis_values(self) -> "TransformAxisEdit":
        values = (self.x, self.y, self.z)
        if self.operation == "preserve" and any(value is not None for value in values):
            raise ValueError("preserve edits cannot contain axis values")
        if self.operation != "preserve" and all(value is None for value in values):
            raise ValueError("transform edits require at least one axis value")
        return self


class TransformEditIntent(StrictModel):
    """Bounded interpretation generated by the local planning model."""

    schema_version: Literal["0.1"] = "0.1"
    outcome: Literal["proposed", "unresolved"]
    coordinate_space: Literal["world", "local"] = "world"
    location: TransformAxisEdit = Field(default_factory=TransformAxisEdit)
    rotation: TransformAxisEdit = Field(default_factory=TransformAxisEdit)
    scale: TransformAxisEdit = Field(default_factory=TransformAxisEdit)
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def outcome_matches_edits(self) -> "TransformEditIntent":
        edits = (self.location, self.rotation, self.scale)
        has_edit = any(edit.operation != "preserve" for edit in edits)
        if self.outcome == "proposed":
            if not has_edit:
                raise ValueError("proposed transform intents require at least one edit")
            if self.missing_capabilities:
                raise ValueError("proposed transform intents cannot report missing capabilities")
        elif has_edit or not self.missing_capabilities:
            raise ValueError(
                "unresolved transform intents require missing capabilities and no edits"
            )
        return self


class TransformChange(StrictModel):
    """Host-computed before/after evidence for one changed Transform channel."""

    channel: Literal["location", "rotation", "scale"]
    operation: Literal["set", "add", "multiply"]
    axes: list[Literal["x", "y", "z"]] = Field(min_length=1, max_length=3)
    before: Vector3
    after: Vector3

    @model_validator(mode="after")
    def changed_axes_are_unique(self) -> "TransformChange":
        if len(self.axes) != len(set(self.axes)):
            raise ValueError("transform change axes must be unique")
        return self


class AssistantTransformProposal(StrictModel):
    """Auditable, approval-gated Transform change for one selected Actor."""

    schema_version: Literal["0.1"] = "0.1"
    proposal_id: Identifier
    status: Literal["proposed", "unresolved"]
    request: LongText
    target: UnrealActorTransformContext
    proposed_by: ModelIdentity
    coordinate_space: Literal["world", "local"] = "world"
    before: ActorTransformSnapshot
    after: ActorTransformSnapshot | None = None
    changes: list[TransformChange] = Field(default_factory=list, max_length=3)
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: Literal[True] = True
    auto_save: Literal[False] = False
    undo_supported: Literal[True] = True

    @model_validator(mode="after")
    def status_matches_transform_action(self) -> "AssistantTransformProposal":
        if self.before != self.target.transform:
            raise ValueError("transform proposal before value must match target evidence")
        if self.status == "proposed":
            if self.after is None or not self.changes:
                raise ValueError("proposed transform actions require after values and changes")
            if self.missing_capabilities:
                raise ValueError("proposed transform actions cannot report missing capabilities")
            before_channels = {
                "location": self.before.location_cm,
                "rotation": self.before.rotation_deg,
                "scale": self.before.scale,
            }
            after_channels = {
                "location": self.after.location_cm,
                "rotation": self.after.rotation_deg,
                "scale": self.after.scale,
            }
            allowed_operations = {
                "location": {"set", "add"},
                "rotation": {"set", "add"},
                "scale": {"set", "multiply"},
            }
            seen_channels: set[str] = set()
            for change in self.changes:
                if change.channel in seen_channels:
                    raise ValueError("transform proposal cannot repeat a changed channel")
                seen_channels.add(change.channel)
                if change.operation not in allowed_operations[change.channel]:
                    raise ValueError(
                        f"unsupported {change.channel} operation in transform proposal"
                    )
                if change.before != before_channels[change.channel]:
                    raise ValueError(
                        f"{change.channel} change before value must match proposal before"
                    )
                if change.after != after_channels[change.channel]:
                    raise ValueError(
                        f"{change.channel} change after value must match proposal after"
                    )
                actual_axes = {
                    axis
                    for axis in ("x", "y", "z")
                    if getattr(change.before, axis) != getattr(change.after, axis)
                }
                if set(change.axes) != actual_axes:
                    raise ValueError(
                        f"{change.channel} change axes must match observable differences"
                    )
            expected_channels = {
                channel
                for channel in ("location", "rotation", "scale")
                if before_channels[channel] != after_channels[channel]
            }
            if seen_channels != expected_channels:
                raise ValueError(
                    "transform proposal changes must cover every changed channel exactly once"
                )
        elif self.after is not None or self.changes or not self.missing_capabilities:
            raise ValueError(
                "unresolved transform actions require missing capabilities and no changes"
            )
        return self


class UnrealTransformSelectionContext(StrictModel):
    """Read-only Transform evidence for an ordered Unreal Actor selection."""

    schema_version: Literal["0.1"] = "0.1"
    project_name: ShortText
    level_path: Annotated[str, Field(min_length=1, max_length=500)]
    actors: list[UnrealActorTransformContext] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def actors_share_selection_scope(self) -> "UnrealTransformSelectionContext":
        paths = [actor.actor_path for actor in self.actors]
        if len(paths) != len(set(paths)):
            raise ValueError("Transform selection cannot repeat an Actor path")
        guids = [actor.actor_guid for actor in self.actors if actor.actor_guid is not None]
        if len(guids) != len(set(guids)):
            raise ValueError("Transform selection cannot repeat an Actor GUID")
        if any(actor.project_name != self.project_name for actor in self.actors):
            raise ValueError("all selected Actors must belong to the captured project")
        if any(actor.level_path != self.level_path for actor in self.actors):
            raise ValueError("all selected Actors must belong to the captured level")
        return self


class TransformActorAction(StrictModel):
    """Host-computed Transform evidence for one Actor in a batch proposal."""

    target: UnrealActorTransformContext
    before: ActorTransformSnapshot
    after: ActorTransformSnapshot
    changes: list[TransformChange] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def evidence_is_complete(self) -> "TransformActorAction":
        if self.before != self.target.transform:
            raise ValueError("batch Transform before value must match target evidence")
        before_channels = {
            "location": self.before.location_cm,
            "rotation": self.before.rotation_deg,
            "scale": self.before.scale,
        }
        after_channels = {
            "location": self.after.location_cm,
            "rotation": self.after.rotation_deg,
            "scale": self.after.scale,
        }
        allowed_operations = {
            "location": {"set", "add"},
            "rotation": {"set", "add"},
            "scale": {"set", "multiply"},
        }
        seen_channels: set[str] = set()
        for change in self.changes:
            if change.channel in seen_channels:
                raise ValueError("batch Transform action cannot repeat a changed channel")
            seen_channels.add(change.channel)
            if change.operation not in allowed_operations[change.channel]:
                raise ValueError(
                    f"unsupported {change.channel} operation in batch Transform action"
                )
            if change.before != before_channels[change.channel]:
                raise ValueError(
                    f"batch {change.channel} before value must match Actor evidence"
                )
            if change.after != after_channels[change.channel]:
                raise ValueError(
                    f"batch {change.channel} after value must match Actor evidence"
                )
            actual_axes = {
                axis
                for axis in ("x", "y", "z")
                if getattr(change.before, axis) != getattr(change.after, axis)
            }
            if set(change.axes) != actual_axes:
                raise ValueError(
                    f"batch {change.channel} axes must match observable world differences"
                )
        expected_channels = {
            channel
            for channel in ("location", "rotation", "scale")
            if before_channels[channel] != after_channels[channel]
        }
        if seen_channels != expected_channels:
            raise ValueError(
                "batch Transform changes must cover every changed channel exactly once"
            )
        return self


class AssistantTransformBatchProposal(StrictModel):
    """Approval-gated Transform change for one ordered Actor selection."""

    schema_version: Literal["0.1"] = "0.1"
    proposal_id: Identifier
    status: Literal["proposed", "unresolved"]
    request: LongText
    selection: UnrealTransformSelectionContext
    proposed_by: ModelIdentity
    coordinate_space: Literal["world", "local"] = "world"
    actions: list[TransformActorAction] = Field(default_factory=list, max_length=32)
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: Literal[True] = True
    auto_save: Literal[False] = False
    undo_supported: Literal[True] = True

    @model_validator(mode="after")
    def status_matches_batch_action(self) -> "AssistantTransformBatchProposal":
        if self.status == "proposed":
            if not self.actions:
                raise ValueError("proposed batch Transform actions require Actor actions")
            if self.missing_capabilities:
                raise ValueError(
                    "proposed batch Transform actions cannot report missing capabilities"
                )
            if len(self.actions) != len(self.selection.actors):
                raise ValueError(
                    "batch Transform actions must cover every selected Actor exactly once"
                )
            for selected, action in zip(self.selection.actors, self.actions, strict=True):
                if action.target != selected:
                    raise ValueError(
                        "batch Transform action targets must preserve selection order and evidence"
                    )
                if self.coordinate_space == "local":
                    for change in action.changes:
                        if change.channel in {"location", "rotation"} and change.operation != "add":
                            raise ValueError(
                                "local-space location and rotation support only add operations"
                            )
            if not any(action.changes for action in self.actions):
                raise ValueError(
                    "proposed batch Transform action must change at least one selected Actor"
                )
        elif self.actions or not self.missing_capabilities:
            raise ValueError(
                "unresolved batch Transform actions require missing capabilities and no actions"
            )
        return self


class LightColorRGB(StrictModel):
    """Linear RGB light color captured from or applied to Unreal."""

    r: UnitFloat
    g: UnitFloat
    b: UnitFloat


class EditorLightSnapshot(StrictModel):
    """Editable light state observed directly from one Unreal light Actor."""

    rotation_deg: Vector3
    intensity: FiniteFloat
    intensity_unit: Literal[
        "lux", "lumens", "candelas", "unitless", "ev", "nits"
    ]
    color_rgb: LightColorRGB
    use_temperature: bool
    temperature_kelvin: Annotated[
        float, Field(ge=1000.0, le=20000.0, allow_inf_nan=False)
    ]
    cast_shadows: bool
    attenuation_radius_cm: PositiveFiniteFloat | None = None
    inner_cone_deg: Annotated[
        float, Field(ge=0.0, le=89.0, allow_inf_nan=False)
    ] | None = None
    outer_cone_deg: Annotated[
        float, Field(gt=0.0, le=89.0, allow_inf_nan=False)
    ] | None = None

    @model_validator(mode="after")
    def cone_angles_are_ordered(self) -> "EditorLightSnapshot":
        if (
            self.inner_cone_deg is not None
            and self.outer_cone_deg is not None
            and self.inner_cone_deg > self.outer_cone_deg
        ):
            raise ValueError("inner light cone cannot exceed outer light cone")
        return self


class UnrealLightContext(StrictModel):
    """Read-only light identity and property evidence captured by the Editor."""

    schema_version: Literal["0.1"] = "0.1"
    project_name: ShortText
    level_path: Annotated[str, Field(min_length=1, max_length=500)]
    actor_name: ShortText
    actor_path: Annotated[str, Field(min_length=1, max_length=1000)]
    actor_class: ShortText
    actor_guid: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    component_name: ShortText
    light_kind: Literal["directional", "point", "spot", "rect"]
    component_mobility: Literal["static", "stationary", "movable"]
    is_editable: bool
    is_locked: bool
    light: EditorLightSnapshot

    @model_validator(mode="after")
    def fields_match_light_kind(self) -> "UnrealLightContext":
        local = self.light_kind in {"point", "spot", "rect"}
        if self.light_kind == "directional" and self.light.intensity_unit != "lux":
            raise ValueError("directional Editor lights must report lux")
        if local and self.light.intensity_unit == "lux":
            raise ValueError("local Editor lights cannot report lux")
        if local != (self.light.attenuation_radius_cm is not None):
            raise ValueError("only local Editor lights require attenuation radius")
        has_cones = (
            self.light.inner_cone_deg is not None
            and self.light.outer_cone_deg is not None
        )
        if (self.light_kind == "spot") != has_cones:
            raise ValueError("only spot lights require inner and outer cone angles")
        if self.light.intensity_unit != "ev" and self.light.intensity < 0:
            raise ValueError("non-EV light intensity cannot be negative")
        return self


class LightScalarEdit(StrictModel):
    """Model-facing bounded scalar operation for one light property."""

    operation: Literal["preserve", "set", "add", "multiply"] = "preserve"
    value: FiniteFloat | None = None

    @model_validator(mode="after")
    def operation_matches_value(self) -> "LightScalarEdit":
        if self.operation == "preserve" and self.value is not None:
            raise ValueError("preserve light edits cannot contain a value")
        if self.operation != "preserve" and self.value is None:
            raise ValueError("non-preserve light edits require a value")
        return self


class LightEditIntent(StrictModel):
    """Restricted model interpretation of one selected-light request."""

    schema_version: Literal["0.1"] = "0.1"
    outcome: Literal["proposed", "unresolved"]
    intensity: LightScalarEdit = Field(default_factory=LightScalarEdit)
    color_rgb: LightColorRGB | None = None
    use_temperature: bool | None = None
    temperature_kelvin: Annotated[
        float, Field(ge=1000.0, le=20000.0, allow_inf_nan=False)
    ] | None = None
    cast_shadows: bool | None = None
    attenuation_radius_cm: LightScalarEdit = Field(default_factory=LightScalarEdit)
    inner_cone_deg: LightScalarEdit = Field(default_factory=LightScalarEdit)
    outer_cone_deg: LightScalarEdit = Field(default_factory=LightScalarEdit)
    rotation: TransformAxisEdit = Field(default_factory=TransformAxisEdit)
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def outcome_matches_light_edits(self) -> "LightEditIntent":
        has_edit = any((
            self.intensity.operation != "preserve",
            self.color_rgb is not None,
            self.use_temperature is not None,
            self.temperature_kelvin is not None,
            self.cast_shadows is not None,
            self.attenuation_radius_cm.operation != "preserve",
            self.inner_cone_deg.operation != "preserve",
            self.outer_cone_deg.operation != "preserve",
            self.rotation.operation != "preserve",
        ))
        if self.outcome == "proposed":
            if not has_edit:
                raise ValueError("proposed light intents require at least one edit")
            if self.missing_capabilities:
                raise ValueError("proposed light intents cannot report missing capabilities")
        elif has_edit or not self.missing_capabilities:
            raise ValueError(
                "unresolved light intents require missing capabilities and no edits"
            )
        return self


class LightPropertyChange(StrictModel):
    """Host-owned operation record for one changed light property."""

    property: Literal[
        "rotation",
        "intensity",
        "color_rgb",
        "use_temperature",
        "temperature_kelvin",
        "cast_shadows",
        "attenuation_radius_cm",
        "inner_cone_deg",
        "outer_cone_deg",
    ]
    operation: Literal["set", "add", "multiply"]


class AssistantLightProposal(StrictModel):
    """Auditable, approval-gated property change for one selected Unreal light."""

    schema_version: Literal["0.1"] = "0.1"
    proposal_id: Identifier
    status: Literal["proposed", "unresolved"]
    request: LongText
    target: UnrealLightContext
    proposed_by: ModelIdentity
    before: EditorLightSnapshot
    after: EditorLightSnapshot | None = None
    changes: list[LightPropertyChange] = Field(default_factory=list, max_length=9)
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: Literal[True] = True
    auto_save: Literal[False] = False
    undo_supported: Literal[True] = True

    @model_validator(mode="after")
    def status_matches_light_action(self) -> "AssistantLightProposal":
        if self.before != self.target.light:
            raise ValueError("light proposal before value must match target evidence")
        if self.status == "proposed":
            if self.after is None or not self.changes:
                raise ValueError("proposed light actions require after values and changes")
            if self.missing_capabilities:
                raise ValueError("proposed light actions cannot report missing capabilities")
            properties = [change.property for change in self.changes]
            if len(properties) != len(set(properties)):
                raise ValueError("light proposal cannot repeat a changed property")
            allowed_operations = {
                "rotation": {"set", "add"},
                "intensity": {"set", "add", "multiply"},
                "color_rgb": {"set"},
                "use_temperature": {"set"},
                "temperature_kelvin": {"set"},
                "cast_shadows": {"set"},
                "attenuation_radius_cm": {"set", "add", "multiply"},
                "inner_cone_deg": {"set", "add"},
                "outer_cone_deg": {"set", "add"},
            }
            for change in self.changes:
                if change.operation not in allowed_operations[change.property]:
                    raise ValueError(
                        f"unsupported {change.property} operation in light proposal"
                    )
            expected = {
                name
                for name in (
                    "rotation",
                    "intensity",
                    "color_rgb",
                    "use_temperature",
                    "temperature_kelvin",
                    "cast_shadows",
                    "attenuation_radius_cm",
                    "inner_cone_deg",
                    "outer_cone_deg",
                )
                if getattr(self.before, "rotation_deg" if name == "rotation" else name)
                != getattr(self.after, "rotation_deg" if name == "rotation" else name)
            }
            if set(properties) != expected:
                raise ValueError(
                    "light proposal changes must cover every changed property exactly once"
                )
            if self.target.light_kind == "point" and "rotation" in expected:
                raise ValueError("point-light rotation is not an executable visual edit")
            if self.target.light_kind == "directional" and any(
                name in expected
                for name in ("attenuation_radius_cm", "inner_cone_deg", "outer_cone_deg")
            ):
                raise ValueError("directional lights cannot contain local-light edits")
            if self.target.light_kind != "spot" and any(
                name in expected for name in ("inner_cone_deg", "outer_cone_deg")
            ):
                raise ValueError("only spot lights can contain cone edits")
        elif self.after is not None or self.changes or not self.missing_capabilities:
            raise ValueError(
                "unresolved light actions require missing capabilities and no changes"
            )
        return self


class UnrealLightSelectionContext(StrictModel):
    """Read-only evidence for an ordered Unreal light selection."""

    schema_version: Literal["0.1"] = "0.1"
    project_name: ShortText
    level_path: Annotated[str, Field(min_length=1, max_length=500)]
    lights: list[UnrealLightContext] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def lights_share_selection_scope(self) -> "UnrealLightSelectionContext":
        paths = [light.actor_path for light in self.lights]
        if len(paths) != len(set(paths)):
            raise ValueError("light selection cannot repeat an Actor path")
        guids = [light.actor_guid for light in self.lights if light.actor_guid is not None]
        if len(guids) != len(set(guids)):
            raise ValueError("light selection cannot repeat an Actor GUID")
        if any(light.project_name != self.project_name for light in self.lights):
            raise ValueError("all selected lights must belong to the captured project")
        if any(light.level_path != self.level_path for light in self.lights):
            raise ValueError("all selected lights must belong to the captured level")
        return self


class LightActorAction(StrictModel):
    """Host-computed property evidence for one light in a batch proposal."""

    target: UnrealLightContext
    before: EditorLightSnapshot
    after: EditorLightSnapshot
    changes: list[LightPropertyChange] = Field(default_factory=list, max_length=9)

    @model_validator(mode="after")
    def evidence_is_complete(self) -> "LightActorAction":
        if self.before != self.target.light:
            raise ValueError("batch light before value must match target evidence")
        properties = [change.property for change in self.changes]
        if len(properties) != len(set(properties)):
            raise ValueError("batch light action cannot repeat a changed property")
        allowed_operations = {
            "rotation": {"set", "add"},
            "intensity": {"set", "add", "multiply"},
            "color_rgb": {"set"},
            "use_temperature": {"set"},
            "temperature_kelvin": {"set"},
            "cast_shadows": {"set"},
            "attenuation_radius_cm": {"set", "add", "multiply"},
            "inner_cone_deg": {"set", "add"},
            "outer_cone_deg": {"set", "add"},
        }
        for change in self.changes:
            if change.operation not in allowed_operations[change.property]:
                raise ValueError(
                    f"unsupported {change.property} operation in batch light action"
                )
        expected = {
            name
            for name in (
                "rotation",
                "intensity",
                "color_rgb",
                "use_temperature",
                "temperature_kelvin",
                "cast_shadows",
                "attenuation_radius_cm",
                "inner_cone_deg",
                "outer_cone_deg",
            )
            if getattr(self.before, "rotation_deg" if name == "rotation" else name)
            != getattr(self.after, "rotation_deg" if name == "rotation" else name)
        }
        if set(properties) != expected:
            raise ValueError(
                "batch light changes must cover every changed property exactly once"
            )
        if self.target.light_kind == "point" and "rotation" in expected:
            raise ValueError("point-light rotation is not an executable visual edit")
        if self.target.light_kind == "directional" and any(
            name in expected
            for name in ("attenuation_radius_cm", "inner_cone_deg", "outer_cone_deg")
        ):
            raise ValueError("directional lights cannot contain local-light edits")
        if self.target.light_kind != "spot" and any(
            name in expected for name in ("inner_cone_deg", "outer_cone_deg")
        ):
            raise ValueError("only spot lights can contain cone edits")
        return self


class AssistantLightBatchProposal(StrictModel):
    """Approval-gated uniform property change for one ordered light selection."""

    schema_version: Literal["0.1"] = "0.1"
    proposal_id: Identifier
    status: Literal["proposed", "unresolved"]
    request: LongText
    selection: UnrealLightSelectionContext
    proposed_by: ModelIdentity
    actions: list[LightActorAction] = Field(default_factory=list, max_length=16)
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: Literal[True] = True
    auto_save: Literal[False] = False
    undo_supported: Literal[True] = True

    @model_validator(mode="after")
    def status_matches_batch_action(self) -> "AssistantLightBatchProposal":
        if self.status == "proposed":
            if not self.actions:
                raise ValueError("proposed batch light actions require light actions")
            if self.missing_capabilities:
                raise ValueError(
                    "proposed batch light actions cannot report missing capabilities"
                )
            if len(self.actions) != len(self.selection.lights):
                raise ValueError(
                    "batch light actions must cover every selected light exactly once"
                )
            for selected, action in zip(self.selection.lights, self.actions, strict=True):
                if action.target != selected:
                    raise ValueError(
                        "batch light action targets must preserve selection order and evidence"
                    )
            if not any(action.changes for action in self.actions):
                raise ValueError(
                    "proposed batch light action must change at least one selected light"
                )
        elif self.actions or not self.missing_capabilities:
            raise ValueError(
                "unresolved batch light actions require missing capabilities and no actions"
            )
        return self


class EditorCameraSnapshot(StrictModel):
    """Editable camera state observed directly from one Unreal camera Actor."""

    location_cm: Vector3
    rotation_deg: Vector3
    field_of_view_deg: Annotated[
        float, Field(ge=5.0, le=170.0, allow_inf_nan=False)
    ] | None = None
    focal_length_mm: PositiveFiniteFloat | None = None
    aperture_fstop: Annotated[
        float, Field(ge=0.1, le=64.0, allow_inf_nan=False)
    ]
    focus_mode: Literal["project_default", "manual", "tracking", "disabled"]
    focus_distance_cm: NonNegativeFiniteFloat
    exposure_compensation_enabled: bool
    exposure_compensation_ev: Annotated[
        float, Field(ge=-15.0, le=15.0, allow_inf_nan=False)
    ]
    post_process_blend_weight: UnitFloat


class UnrealCameraContext(StrictModel):
    """Read-only camera identity, lens limits, and property evidence from Unreal."""

    schema_version: Literal["0.1"] = "0.1"
    project_name: ShortText
    level_path: Annotated[str, Field(min_length=1, max_length=500)]
    actor_name: ShortText
    actor_path: Annotated[str, Field(min_length=1, max_length=1000)]
    actor_class: ShortText
    actor_guid: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    component_name: ShortText
    camera_kind: Literal["camera", "cine_camera"]
    component_mobility: Literal["static", "stationary", "movable"]
    projection_mode: Literal["perspective"] = "perspective"
    is_editable: bool
    is_locked: bool
    min_focal_length_mm: PositiveFiniteFloat | None = None
    max_focal_length_mm: PositiveFiniteFloat | None = None
    min_aperture_fstop: Annotated[
        float, Field(ge=0.1, le=64.0, allow_inf_nan=False)
    ]
    max_aperture_fstop: Annotated[
        float, Field(ge=0.1, le=64.0, allow_inf_nan=False)
    ]
    minimum_focus_distance_cm: NonNegativeFiniteFloat
    camera: EditorCameraSnapshot

    @model_validator(mode="after")
    def fields_match_camera_kind(self) -> "UnrealCameraContext":
        cine = self.camera_kind == "cine_camera"
        has_focal_bounds = (
            self.min_focal_length_mm is not None
            and self.max_focal_length_mm is not None
        )
        if cine != has_focal_bounds:
            raise ValueError("only Cine Cameras require focal-length bounds")
        if cine != (self.camera.focal_length_mm is not None):
            raise ValueError("only Cine Cameras expose focal length")
        if cine == (self.camera.field_of_view_deg is not None):
            raise ValueError("standard Cameras expose FOV; Cine Cameras expose focal length")
        if has_focal_bounds and self.min_focal_length_mm > self.max_focal_length_mm:
            raise ValueError("camera focal-length bounds are reversed")
        if self.min_aperture_fstop > self.max_aperture_fstop:
            raise ValueError("camera aperture bounds are reversed")
        if (
            self.camera.focal_length_mm is not None
            and not self.min_focal_length_mm
            <= self.camera.focal_length_mm
            <= self.max_focal_length_mm
        ):
            raise ValueError("camera focal length is outside captured lens bounds")
        if not self.min_aperture_fstop <= self.camera.aperture_fstop <= self.max_aperture_fstop:
            raise ValueError("camera aperture is outside captured lens bounds")
        if self.camera.focus_distance_cm < self.minimum_focus_distance_cm:
            raise ValueError("camera focus distance is below the captured lens minimum")
        if not cine and self.camera.focus_mode not in {"project_default", "manual"}:
            raise ValueError("standard Cameras support only project-default or manual focus")
        return self


class CameraScalarEdit(StrictModel):
    """Restricted scalar operation used by the camera intent model."""

    operation: Literal["preserve", "set", "add", "multiply"] = "preserve"
    value: FiniteFloat | None = None

    @model_validator(mode="after")
    def operation_matches_value(self) -> "CameraScalarEdit":
        if self.operation == "preserve" and self.value is not None:
            raise ValueError("preserve camera edits cannot contain a value")
        if self.operation != "preserve" and self.value is None:
            raise ValueError("non-preserve camera edits require a value")
        return self


class CameraEditIntent(StrictModel):
    """Restricted model interpretation of one selected-camera request."""

    schema_version: Literal["0.1"] = "0.1"
    outcome: Literal["proposed", "unresolved"]
    location: TransformAxisEdit = Field(default_factory=TransformAxisEdit)
    rotation: TransformAxisEdit = Field(default_factory=TransformAxisEdit)
    field_of_view_deg: CameraScalarEdit = Field(default_factory=CameraScalarEdit)
    focal_length_mm: CameraScalarEdit = Field(default_factory=CameraScalarEdit)
    aperture_fstop: CameraScalarEdit = Field(default_factory=CameraScalarEdit)
    focus_mode: Literal["preserve", "manual", "disabled"] = "preserve"
    focus_distance_cm: CameraScalarEdit = Field(default_factory=CameraScalarEdit)
    exposure_compensation_enabled: bool | None = None
    exposure_compensation_ev: CameraScalarEdit = Field(default_factory=CameraScalarEdit)
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def outcome_matches_camera_edits(self) -> "CameraEditIntent":
        has_edit = any((
            self.location.operation != "preserve",
            self.rotation.operation != "preserve",
            self.field_of_view_deg.operation != "preserve",
            self.focal_length_mm.operation != "preserve",
            self.aperture_fstop.operation != "preserve",
            self.focus_mode != "preserve",
            self.focus_distance_cm.operation != "preserve",
            self.exposure_compensation_enabled is not None,
            self.exposure_compensation_ev.operation != "preserve",
        ))
        if self.outcome == "proposed":
            if not has_edit:
                raise ValueError("proposed camera intents require at least one edit")
            if self.missing_capabilities:
                raise ValueError("proposed camera intents cannot report missing capabilities")
        elif has_edit or not self.missing_capabilities:
            raise ValueError(
                "unresolved camera intents require missing capabilities and no edits"
            )
        return self


class CameraPropertyChange(StrictModel):
    """Host-owned operation record for one changed camera property."""

    property: Literal[
        "location",
        "rotation",
        "field_of_view_deg",
        "focal_length_mm",
        "aperture_fstop",
        "focus_mode",
        "focus_distance_cm",
        "exposure_compensation_enabled",
        "exposure_compensation_ev",
    ]
    operation: Literal["set", "add", "multiply"]


class AssistantCameraProposal(StrictModel):
    """Auditable, approval-gated property change for one selected Unreal camera."""

    schema_version: Literal["0.1"] = "0.1"
    proposal_id: Identifier
    status: Literal["proposed", "unresolved"]
    request: LongText
    target: UnrealCameraContext
    proposed_by: ModelIdentity
    before: EditorCameraSnapshot
    after: EditorCameraSnapshot | None = None
    changes: list[CameraPropertyChange] = Field(default_factory=list, max_length=9)
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: Literal[True] = True
    auto_save: Literal[False] = False
    undo_supported: Literal[True] = True

    @model_validator(mode="after")
    def status_matches_camera_action(self) -> "AssistantCameraProposal":
        if self.before != self.target.camera:
            raise ValueError("camera proposal before value must match target evidence")
        if self.status == "proposed":
            if self.after is None or not self.changes:
                raise ValueError("proposed camera actions require after values and changes")
            if self.missing_capabilities:
                raise ValueError("proposed camera actions cannot report missing capabilities")
            if self.after.post_process_blend_weight != self.before.post_process_blend_weight:
                raise ValueError("camera actions cannot change Post Process blend weight")
            properties = [change.property for change in self.changes]
            if len(properties) != len(set(properties)):
                raise ValueError("camera proposal cannot repeat a changed property")
            allowed_operations = {
                "location": {"set", "add"},
                "rotation": {"set", "add"},
                "field_of_view_deg": {"set", "add", "multiply"},
                "focal_length_mm": {"set", "add", "multiply"},
                "aperture_fstop": {"set", "add", "multiply"},
                "focus_mode": {"set"},
                "focus_distance_cm": {"set", "add", "multiply"},
                "exposure_compensation_enabled": {"set"},
                "exposure_compensation_ev": {"set", "add"},
            }
            for change in self.changes:
                if change.operation not in allowed_operations[change.property]:
                    raise ValueError(
                        f"unsupported {change.property} operation in camera proposal"
                    )
            expected = {
                name
                for name in (
                    "location",
                    "rotation",
                    "field_of_view_deg",
                    "focal_length_mm",
                    "aperture_fstop",
                    "focus_mode",
                    "focus_distance_cm",
                    "exposure_compensation_enabled",
                    "exposure_compensation_ev",
                )
                if getattr(self.before, f"{name}_cm" if name == "location" else f"{name}_deg" if name == "rotation" else name)
                != getattr(self.after, f"{name}_cm" if name == "location" else f"{name}_deg" if name == "rotation" else name)
            }
            if set(properties) != expected:
                raise ValueError(
                    "camera proposal changes must cover every changed property exactly once"
                )
            if self.target.camera_kind == "camera" and "focal_length_mm" in expected:
                raise ValueError("standard Cameras cannot contain focal-length edits")
            if self.target.camera_kind == "cine_camera" and "field_of_view_deg" in expected:
                raise ValueError("Cine Cameras cannot contain direct FOV edits")
        elif self.after is not None or self.changes or not self.missing_capabilities:
            raise ValueError(
                "unresolved camera actions require missing capabilities and no changes"
            )
        return self


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


class ImageComparison(StrictModel):
    """Deterministic quality comparison between consecutive preview PNGs."""

    schema_version: Literal["0.1"] = "0.1"
    baseline_sha256: Sha256
    candidate_sha256: Sha256
    outcome: Literal["improved", "regressed", "inconclusive"]
    reasons: list[LongText] = Field(min_length=1, max_length=16)
    mean_luminance_delta: FiniteFloat
    center_luminance_delta: FiniteFloat
    foreground_fraction_delta: FiniteFloat


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


class WorkflowIteration(StrictModel):
    """Auditable evidence from one preview/evaluate/correct workflow iteration."""

    iteration: Annotated[int, Field(ge=1, le=5)]
    run_directory: RelativeArtifactPath
    render_spec_sha256: Sha256
    preflight_report_sha256: Sha256
    preflight_verdict: Literal["pass", "needs_review", "fail"]
    preview_manifest_sha256: Sha256
    evaluation_report_sha256: Sha256
    evaluation_verdict: Literal["pass", "needs_review", "fail"]
    image_statistics_sha256: Sha256 | None = None
    image_comparison_sha256: Sha256 | None = None
    correction_outcome: Literal["patch", "unresolved"] | None = None
    correction_decision_sha256: Sha256 | None = None
    corrected_render_spec_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def correction_fields_are_consistent(self) -> "WorkflowIteration":
        if self.image_comparison_sha256 is not None and self.image_statistics_sha256 is None:
            raise ValueError("image comparisons require image statistics")
        if self.evaluation_verdict == "pass" and self.correction_outcome is not None:
            raise ValueError("passing workflow iterations cannot contain a correction")
        if self.correction_outcome is None:
            if (
                self.correction_decision_sha256 is not None
                or self.corrected_render_spec_sha256 is not None
            ):
                raise ValueError("workflow correction hashes require a correction outcome")
        elif self.correction_decision_sha256 is None:
            raise ValueError("workflow correction outcomes require a decision hash")
        elif self.correction_outcome == "patch":
            if self.corrected_render_spec_sha256 is None:
                raise ValueError("workflow patch outcomes require a corrected RenderSpec hash")
        elif self.corrected_render_spec_sha256 is not None:
            raise ValueError("unresolved workflow outcomes cannot have a corrected RenderSpec")
        return self


class RenderWorkflowManifest(StrictModel):
    """Top-level lifecycle and stop record for one bounded RenderMasterBot run."""

    schema_version: Literal["0.1"] = "0.1"
    workflow_id: Identifier
    status: Literal["running", "succeeded", "stopped", "failed"]
    stage: Literal[
        "initializing",
        "retrieval",
        "planning",
        "preflight",
        "rendering",
        "evaluation",
        "correction",
        "complete",
    ]
    stop_reason: Literal[
        "evaluator_passed",
        "preflight_rejected",
        "correction_unresolved",
        "max_iterations_reached",
        "correction_cycle_detected",
        "image_quality_regressed",
        "stage_failed",
    ] | None = None
    prompt: LongText
    project_name: ShortText
    project_descriptor_sha256: Sha256
    planner: ModelIdentity
    evaluator: ModelIdentity
    max_iterations: Annotated[int, Field(ge=1, le=5)]
    retrieved_asset_ids: list[Identifier] = Field(default_factory=list, max_length=128)
    started_at: datetime
    finished_at: datetime | None = None
    input_artifacts: list[ArtifactRecord] = Field(default_factory=list, max_length=64)
    output_artifacts: list[ArtifactRecord] = Field(default_factory=list, max_length=512)
    iterations: list[WorkflowIteration] = Field(default_factory=list, max_length=5)
    errors: list[LongText] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> "RenderWorkflowManifest":
        terminal = self.status in {"succeeded", "stopped", "failed"}
        if terminal != (self.finished_at is not None):
            raise ValueError(
                "terminal workflows require finished_at and running workflows forbid it"
            )
        if terminal != (self.stage == "complete"):
            raise ValueError("terminal workflows require complete stage")
        if terminal != (self.stop_reason is not None):
            raise ValueError("terminal workflows require a stop reason")
        if self.status == "running" and self.errors:
            raise ValueError("running workflows cannot contain terminal errors")
        if self.status == "failed":
            if self.stop_reason != "stage_failed" or not self.errors:
                raise ValueError("failed workflows require stage_failed and at least one error")
        elif self.errors:
            raise ValueError("only failed workflows can contain errors")
        if self.status == "succeeded":
            if self.stop_reason != "evaluator_passed":
                raise ValueError("succeeded workflows require evaluator_passed")
            if not self.iterations or self.iterations[-1].evaluation_verdict != "pass":
                raise ValueError("succeeded workflows require a final passing evaluation")
        if self.status == "stopped" and self.stop_reason not in {
            "preflight_rejected",
            "correction_unresolved",
            "max_iterations_reached",
            "correction_cycle_detected",
            "image_quality_regressed",
        }:
            raise ValueError("stopped workflow has an invalid stop reason")
        numbers = [iteration.iteration for iteration in self.iterations]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("workflow iterations must be contiguous and one-based")
        if len(numbers) > self.max_iterations:
            raise ValueError("workflow iterations exceed max_iterations")
        if len(self.retrieved_asset_ids) != len(set(self.retrieved_asset_ids)):
            raise ValueError("workflow retrieved asset IDs must be unique")
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
    "unreal-selection-context": UnrealSelectionContext,
    "assistant-material-proposal": AssistantMaterialProposal,
    "unreal-actor-transform-context": UnrealActorTransformContext,
    "assistant-transform-proposal": AssistantTransformProposal,
    "unreal-transform-selection-context": UnrealTransformSelectionContext,
    "assistant-transform-batch-proposal": AssistantTransformBatchProposal,
    "unreal-light-context": UnrealLightContext,
    "assistant-light-proposal": AssistantLightProposal,
    "unreal-light-selection-context": UnrealLightSelectionContext,
    "assistant-light-batch-proposal": AssistantLightBatchProposal,
    "unreal-camera-context": UnrealCameraContext,
    "assistant-camera-proposal": AssistantCameraProposal,
    "render-spec-patch": RenderSpecPatch,
    "correction-decision": CorrectionDecision,
    "evaluation-report": EvaluationReport,
    "visual-benchmark-suite": VisualBenchmarkSuite,
    "visual-benchmark-report": VisualBenchmarkReport,
    "render-workflow-manifest": RenderWorkflowManifest,
    "capability-manifest": CapabilityManifest,
    "run-manifest": RunManifest,
}
