"""Versioned contracts shared by the AI core and engine adapters.

These models are deliberately strict. They are the durable hand-off boundary
between probabilistic model output and deterministic renderer code.
"""

from __future__ import annotations

import hashlib
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


class LightingRigBounds(StrictModel):
    """World-space bounds frozen for the lighting subject."""

    center_cm: Vector3
    extent_cm: Vector3
    sphere_radius_cm: PositiveFiniteFloat

    @model_validator(mode="after")
    def extents_match_radius(self) -> "LightingRigBounds":
        extents = (self.extent_cm.x, self.extent_cm.y, self.extent_cm.z)
        if any(value < 0.0 for value in extents):
            raise ValueError("lighting-rig bounds extents cannot be negative")
        if max(extents) <= 0.0:
            raise ValueError("lighting-rig subject must have observable bounds")
        if self.sphere_radius_cm + 1e-6 < max(extents):
            raise ValueError("lighting-rig sphere radius cannot be smaller than an extent")
        return self


class LightingRigSubjectContext(StrictModel):
    """Read-only subject identity, Transform, and bounds captured by Unreal."""

    actor_name: ShortText
    actor_path: Annotated[str, Field(min_length=1, max_length=1000)]
    actor_class: ShortText
    actor_guid: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    root_component_name: ShortText | None = None
    root_mobility: Literal["static", "stationary", "movable", "none"]
    is_editable: bool
    is_locked: bool
    transform: ActorTransformSnapshot
    bounds: LightingRigBounds


class LightingRigCameraContext(StrictModel):
    """Read-only camera identity and viewpoint used to orient a lighting rig."""

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
    location_cm: Vector3
    rotation_deg: Vector3


class LightingRigLightContext(StrictModel):
    """One local light plus the world location omitted by the property-only context."""

    target: UnrealLightContext
    location_cm: Vector3


class UnrealLightingRigContext(StrictModel):
    """Frozen subject, camera, and exactly three local lights for a role-aware rig."""

    schema_version: Literal["0.1"] = "0.1"
    project_name: ShortText
    level_path: Annotated[str, Field(min_length=1, max_length=500)]
    subject: LightingRigSubjectContext
    camera: LightingRigCameraContext
    lights: list[LightingRigLightContext] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def rig_evidence_is_compatible(self) -> "UnrealLightingRigContext":
        if not self.subject.is_editable or self.subject.is_locked:
            raise ValueError("lighting-rig subject must be editable and unlocked")
        if not self.camera.is_editable or self.camera.is_locked:
            raise ValueError("lighting-rig camera must be editable and unlocked")
        paths = [self.subject.actor_path, self.camera.actor_path]
        paths.extend(light.target.actor_path for light in self.lights)
        if len(paths) != len(set(paths)):
            raise ValueError("lighting-rig Actor paths must be unique")
        guids = [
            value
            for value in (
                self.subject.actor_guid,
                self.camera.actor_guid,
                *(light.target.actor_guid for light in self.lights),
            )
            if value is not None
        ]
        if len(guids) != len(set(guids)):
            raise ValueError("lighting-rig Actor GUIDs must be unique")
        component_keys = [
            (light.target.actor_path, light.target.component_name)
            for light in self.lights
        ]
        if len(component_keys) != len(set(component_keys)):
            raise ValueError("lighting-rig light components must be unique")
        for light in self.lights:
            target = light.target
            if target.project_name != self.project_name or target.level_path != self.level_path:
                raise ValueError("all lighting-rig lights must share the captured project and level")
            if target.light_kind == "directional":
                raise ValueError("role-aware lighting rigs require local lights")
            if target.component_mobility != "movable":
                raise ValueError("role-aware lighting-rig lights must be Movable")
            if not target.is_editable or target.is_locked:
                raise ValueError("role-aware lighting-rig lights must be editable and unlocked")
        units = {light.target.light.intensity_unit for light in self.lights}
        if len(units) != 1 or "ev" in units:
            raise ValueError("lighting-rig lights require one shared non-EV intensity unit")
        if max(light.target.light.intensity for light in self.lights) <= 0.0:
            raise ValueError("lighting-rig lights require a positive captured intensity scale")
        return self


class LightingRigRoleAssignment(StrictModel):
    """Model-selected role for one exact light path."""

    actor_path: Annotated[str, Field(min_length=1, max_length=1000)]
    role: Literal["key", "fill", "rim"]


class LightingRigIntent(StrictModel):
    """Restricted model-facing interpretation of one three-point-lighting request."""

    schema_version: Literal["0.1"] = "0.1"
    outcome: Literal["proposed", "unresolved"]
    assignments: list[LightingRigRoleAssignment] = Field(default_factory=list, max_length=3)
    contrast: Literal["soft", "balanced", "dramatic"] = "balanced"
    palette: Literal["preserve", "neutral", "warm_cool", "cool_warm"] = "preserve"
    key_side: Literal["camera_left", "camera_right"] = "camera_left"
    spacing: Literal["tight", "standard", "wide"] = "standard"
    brightness: Literal["dim", "balanced", "bright"] = "balanced"
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def outcome_matches_role_plan(self) -> "LightingRigIntent":
        if self.outcome == "proposed":
            if len(self.assignments) != 3:
                raise ValueError("proposed lighting rigs require exactly three assignments")
            if self.missing_capabilities:
                raise ValueError("proposed lighting rigs cannot report missing capabilities")
            paths = [assignment.actor_path for assignment in self.assignments]
            roles = [assignment.role for assignment in self.assignments]
            if len(paths) != len(set(paths)):
                raise ValueError("lighting-rig assignments cannot repeat a light path")
            if set(roles) != {"key", "fill", "rim"}:
                raise ValueError("lighting-rig assignments require key, fill, and rim exactly once")
        elif self.assignments or not self.missing_capabilities:
            raise ValueError(
                "unresolved lighting rigs require missing capabilities and no assignments"
            )
        return self


class LightingRigLightSnapshot(StrictModel):
    """Complete host-owned location and property evidence for one rig light."""

    location_cm: Vector3
    light: EditorLightSnapshot


class LightingRigPropertyChange(StrictModel):
    """One deterministic property changed by the role-aware rig compiler."""

    property: Literal[
        "location",
        "rotation",
        "intensity",
        "use_temperature",
        "temperature_kelvin",
        "attenuation_radius_cm",
        "inner_cone_deg",
        "outer_cone_deg",
    ]
    operation: Literal["set"] = "set"


class LightingRigLightAction(StrictModel):
    """Role and complete Before/After evidence for one selected rig light."""

    role: Literal["key", "fill", "rim"]
    target: LightingRigLightContext
    before: LightingRigLightSnapshot
    after: LightingRigLightSnapshot
    changes: list[LightingRigPropertyChange] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def action_evidence_is_complete(self) -> "LightingRigLightAction":
        expected_before = LightingRigLightSnapshot(
            location_cm=self.target.location_cm,
            light=self.target.target.light,
        )
        if self.before != expected_before:
            raise ValueError("lighting-rig Before values must match target evidence")
        properties = [change.property for change in self.changes]
        if len(properties) != len(set(properties)):
            raise ValueError("lighting-rig action cannot repeat a changed property")
        before_light = self.before.light
        after_light = self.after.light
        expected = set()
        if self.before.location_cm != self.after.location_cm:
            expected.add("location")
        for name in (
            "rotation",
            "intensity",
            "use_temperature",
            "temperature_kelvin",
            "attenuation_radius_cm",
            "inner_cone_deg",
            "outer_cone_deg",
        ):
            attribute = "rotation_deg" if name == "rotation" else name
            if getattr(before_light, attribute) != getattr(after_light, attribute):
                expected.add(name)
        if set(properties) != expected:
            raise ValueError(
                "lighting-rig changes must cover every changed property exactly once"
            )
        if before_light.intensity_unit != after_light.intensity_unit:
            raise ValueError("lighting-rig actions cannot change intensity units")
        if before_light.color_rgb != after_light.color_rgb:
            raise ValueError("lighting-rig actions cannot change light color tint")
        if before_light.cast_shadows != after_light.cast_shadows:
            raise ValueError("lighting-rig actions cannot change shadow casting")
        return self


class AssistantLightingRigProposal(StrictModel):
    """Approval-gated Key/Fill/Rim proposal for one frozen lighting-rig context."""

    schema_version: Literal["0.1"] = "0.1"
    proposal_id: Identifier
    status: Literal["proposed", "unresolved"]
    request: LongText
    context: UnrealLightingRigContext
    proposed_by: ModelIdentity
    contrast: Literal["soft", "balanced", "dramatic"]
    palette: Literal["preserve", "neutral", "warm_cool", "cool_warm"]
    key_side: Literal["camera_left", "camera_right"]
    spacing: Literal["tight", "standard", "wide"]
    brightness: Literal["dim", "balanced", "bright"]
    actions: list[LightingRigLightAction] = Field(default_factory=list, max_length=3)
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: Literal[True] = True
    auto_save: Literal[False] = False
    undo_supported: Literal[True] = True

    @model_validator(mode="after")
    def proposal_matches_frozen_rig(self) -> "AssistantLightingRigProposal":
        if self.status == "proposed":
            if len(self.actions) != 3 or self.missing_capabilities:
                raise ValueError("proposed lighting rigs require three actions and no gaps")
            roles = [action.role for action in self.actions]
            if set(roles) != {"key", "fill", "rim"}:
                raise ValueError("lighting-rig proposal requires key, fill, and rim exactly once")
            for selected, action in zip(self.context.lights, self.actions, strict=True):
                if action.target != selected:
                    raise ValueError(
                        "lighting-rig actions must preserve selection order and evidence"
                    )
            if not any(action.changes for action in self.actions):
                raise ValueError("proposed lighting rig must change at least one light")
        elif self.actions or not self.missing_capabilities:
            raise ValueError(
                "unresolved lighting rigs require missing capabilities and no actions"
            )
        return self


class UnrealLightingRigReviewContext(StrictModel):
    """Applied rig evidence frozen before one camera-view visual review."""

    schema_version: Literal["0.1"] = "0.1"
    source_request: LongText
    rig: UnrealLightingRigContext
    assignments: list[LightingRigRoleAssignment] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def assignments_match_current_rig(self) -> "UnrealLightingRigReviewContext":
        paths = [assignment.actor_path for assignment in self.assignments]
        roles = [assignment.role for assignment in self.assignments]
        expected_paths = [light.target.actor_path for light in self.rig.lights]
        if paths != expected_paths:
            raise ValueError(
                "lighting-rig review assignments must preserve current light order"
            )
        if set(roles) != {"key", "fill", "rim"}:
            raise ValueError(
                "lighting-rig review requires key, fill, and rim exactly once"
            )
        return self


class LightingRigReviewIntent(StrictModel):
    """Vision-owned categorical diagnosis for one applied three-point rig."""

    schema_version: Literal["0.1"] = "0.1"
    outcome: Literal["pass", "proposed", "unresolved"]
    exposure: Literal["too_dark", "balanced", "too_bright"]
    fill_balance: Literal["too_weak", "balanced", "too_strong"]
    rim_separation: Literal["too_weak", "balanced", "too_strong"]
    confidence: UnitFloat
    summary: LongText
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(max_length=16)

    @model_validator(mode="after")
    def outcome_matches_diagnosis(self) -> "LightingRigReviewIntent":
        adjustments = (
            self.exposure != "balanced"
            or self.fill_balance != "balanced"
            or self.rim_separation != "balanced"
        )
        if self.outcome == "pass":
            if adjustments or self.missing_capabilities:
                raise ValueError(
                    "passing lighting-rig reviews require balanced diagnostics and no gap"
                )
        elif self.outcome == "proposed":
            if not adjustments or self.missing_capabilities:
                raise ValueError(
                    "proposed lighting-rig reviews require a diagnosis and no gap"
                )
        elif adjustments or not self.missing_capabilities:
            raise ValueError(
                "unresolved lighting-rig reviews require balanced diagnostics and a gap"
            )
        return self


class LightingRigPreviewEvidence(StrictModel):
    """Host-owned deterministic evidence for the exact reviewed Editor PNG."""

    sha256: Sha256
    width_px: Annotated[int, Field(gt=0, le=32768)]
    height_px: Annotated[int, Field(gt=0, le=32768)]
    sampled_pixels: Annotated[int, Field(gt=0, le=1_000_000)]
    mean_luminance: UnitFloat
    luminance_stddev: UnitFloat
    dark_pixel_fraction: UnitFloat
    clipped_pixel_fraction: UnitFloat
    foreground_fraction: UnitFloat
    center_luminance: UnitFloat
    border_luminance: UnitFloat
    blank_like: bool
    underexposed_like: bool
    overexposed_like: bool


class AssistantLightingRigReviewProposal(StrictModel):
    """Approval-gated intensity correction derived from one verified camera PNG."""

    schema_version: Literal["0.1"] = "0.1"
    proposal_id: Identifier
    status: Literal["pass", "proposed", "unresolved"]
    request: LongText
    context: UnrealLightingRigReviewContext
    proposed_by: ModelIdentity
    preview: LightingRigPreviewEvidence
    exposure: Literal["too_dark", "balanced", "too_bright"]
    fill_balance: Literal["too_weak", "balanced", "too_strong"]
    rim_separation: Literal["too_weak", "balanced", "too_strong"]
    confidence: UnitFloat
    summary: LongText
    actions: list[LightingRigLightAction] = Field(default_factory=list, max_length=3)
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: Literal[True] = True
    auto_save: Literal[False] = False
    undo_supported: Literal[True] = True

    @model_validator(mode="after")
    def review_matches_frozen_rig(self) -> "AssistantLightingRigReviewProposal":
        adjustments = (
            self.exposure != "balanced"
            or self.fill_balance != "balanced"
            or self.rim_separation != "balanced"
        )
        if self.status == "pass":
            if adjustments or self.actions or self.missing_capabilities:
                raise ValueError(
                    "passing lighting-rig reviews require no actions or capability gap"
                )
            if (
                self.preview.blank_like
                or self.preview.underexposed_like
                or self.preview.overexposed_like
            ):
                raise ValueError(
                    "deterministically invalid previews cannot pass lighting-rig review"
                )
            return self
        if self.status == "unresolved":
            if adjustments or self.actions or not self.missing_capabilities:
                raise ValueError(
                    "unresolved lighting-rig reviews require no action and a gap"
                )
            return self
        if not adjustments or len(self.actions) != 3 or self.missing_capabilities:
            raise ValueError(
                "proposed lighting-rig reviews require three actions and no gap"
            )
        if self.preview.blank_like:
            raise ValueError("blank-like previews cannot produce a lighting correction")
        if self.preview.underexposed_like and self.exposure != "too_dark":
            raise ValueError("underexposed-like previews require a brighter diagnosis")
        if self.preview.overexposed_like and self.exposure != "too_bright":
            raise ValueError("overexposed-like previews require a darker diagnosis")

        for selected, assignment, action in zip(
            self.context.rig.lights,
            self.context.assignments,
            self.actions,
            strict=True,
        ):
            if action.target != selected or action.role != assignment.role:
                raise ValueError(
                    "lighting-rig review actions must preserve ordered role evidence"
                )
            if any(change.property != "intensity" for change in action.changes):
                raise ValueError(
                    "lighting-rig visual review can change only light intensity"
                )
            before = action.before.model_dump(mode="json")
            after = action.after.model_dump(mode="json")
            after["light"]["intensity"] = before["light"]["intensity"]
            if before != after:
                raise ValueError(
                    "lighting-rig visual review must preserve every non-intensity value"
                )
        if not any(action.changes for action in self.actions):
            raise ValueError("proposed lighting-rig review must change at least one light")
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


class UnrealCameraSelectionContext(StrictModel):
    """Read-only camera evidence for one ordered Unreal Editor selection."""

    schema_version: Literal["0.1"] = "0.1"
    project_name: ShortText
    level_path: Annotated[str, Field(min_length=1, max_length=500)]
    cameras: list[UnrealCameraContext] = Field(min_length=2, max_length=16)

    @model_validator(mode="after")
    def cameras_share_selection_scope(self) -> "UnrealCameraSelectionContext":
        paths = [camera.actor_path for camera in self.cameras]
        if len(paths) != len(set(paths)):
            raise ValueError("camera selection cannot repeat an Actor path")
        guids = [
            camera.actor_guid
            for camera in self.cameras
            if camera.actor_guid is not None
        ]
        if len(guids) != len(set(guids)):
            raise ValueError("camera selection cannot repeat an Actor GUID")
        if any(camera.project_name != self.project_name for camera in self.cameras):
            raise ValueError("all selected cameras must belong to the captured project")
        if any(camera.level_path != self.level_path for camera in self.cameras):
            raise ValueError("all selected cameras must belong to the captured level")
        return self


class CameraActorAction(StrictModel):
    """Host-computed Before/After evidence for one camera in a batch action."""

    target: UnrealCameraContext
    before: EditorCameraSnapshot
    after: EditorCameraSnapshot
    changes: list[CameraPropertyChange] = Field(default_factory=list, max_length=9)

    @model_validator(mode="after")
    def evidence_is_complete(self) -> "CameraActorAction":
        if self.before != self.target.camera:
            raise ValueError("batch camera before value must match target evidence")
        if self.after.post_process_blend_weight != self.before.post_process_blend_weight:
            raise ValueError("batch camera actions cannot change Post Process blend weight")

        properties = [change.property for change in self.changes]
        if len(properties) != len(set(properties)):
            raise ValueError("batch camera action cannot repeat a changed property")
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
                    f"unsupported {change.property} operation in batch camera action"
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
            if getattr(
                self.before,
                f"{name}_cm"
                if name == "location"
                else f"{name}_deg"
                if name == "rotation"
                else name,
            )
            != getattr(
                self.after,
                f"{name}_cm"
                if name == "location"
                else f"{name}_deg"
                if name == "rotation"
                else name,
            )
        }
        if set(properties) != expected:
            raise ValueError(
                "batch camera changes must cover every changed property exactly once"
            )
        if self.target.camera_kind == "camera" and "focal_length_mm" in expected:
            raise ValueError("standard Cameras cannot contain focal-length edits")
        if self.target.camera_kind == "cine_camera" and "field_of_view_deg" in expected:
            raise ValueError("Cine Cameras cannot contain direct FOV edits")
        return self


class AssistantCameraBatchProposal(StrictModel):
    """Approval-gated coordinated edit for one ordered camera selection."""

    schema_version: Literal["0.1"] = "0.1"
    proposal_id: Identifier
    status: Literal["proposed", "unresolved"]
    request: LongText
    selection: UnrealCameraSelectionContext
    proposed_by: ModelIdentity
    actions: list[CameraActorAction] = Field(default_factory=list, max_length=16)
    rationale: LongText
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: Literal[True] = True
    auto_save: Literal[False] = False
    undo_supported: Literal[True] = True

    @model_validator(mode="after")
    def status_matches_batch_action(self) -> "AssistantCameraBatchProposal":
        if self.status == "proposed":
            if len(self.actions) != len(self.selection.cameras):
                raise ValueError(
                    "batch camera actions must cover every selected camera exactly once"
                )
            if self.missing_capabilities:
                raise ValueError(
                    "proposed batch camera actions cannot report missing capabilities"
                )
            for selected, action in zip(
                self.selection.cameras, self.actions, strict=True
            ):
                if action.target != selected:
                    raise ValueError(
                        "batch camera targets must preserve selection order and evidence"
                    )
            if not any(action.changes for action in self.actions):
                raise ValueError(
                    "proposed batch camera action must change at least one selected camera"
                )
        elif self.actions or not self.missing_capabilities:
            raise ValueError(
                "unresolved batch camera actions require missing capabilities and no actions"
            )
        return self


PerformanceEvidenceField = Literal[
    "lod0_triangles",
    "lod_count",
    "material_slot_count",
    "nanite_enabled",
    "collision_mode",
    "component_mobility",
    "component_tick_enabled",
    "cast_shadow",
    "max_draw_distance_cm",
    "bounds_radius_cm",
]


class StaticMeshPerformanceSnapshot(StrictModel):
    """Mutable component-level performance settings captured from Unreal."""

    cast_shadow: bool
    max_draw_distance_cm: Annotated[
        float, Field(ge=0, le=1_000_000, allow_inf_nan=False)
    ]


class UnrealStaticMeshPerformanceContext(StrictModel):
    """Read-only evidence for one selected Unreal StaticMeshActor."""

    schema_version: Literal["0.1"] = "0.1"
    project_name: ShortText
    level_path: Annotated[str, Field(min_length=1, max_length=500)]
    actor_name: ShortText
    actor_path: Annotated[str, Field(min_length=1, max_length=1000)]
    actor_class: Literal["StaticMeshActor"]
    actor_guid: Annotated[str, Field(pattern=r"^[A-Fa-f0-9]{32}$")] | None = None
    component_name: ShortText
    component_mobility: Literal["static", "stationary", "movable"]
    is_editable: bool
    is_locked: bool
    mesh_path: Annotated[str, Field(min_length=1, max_length=1000)]
    lod_count: Annotated[int, Field(ge=1, le=64)]
    lod0_triangles: Annotated[int, Field(ge=0, le=2_000_000_000)]
    material_slot_count: Annotated[int, Field(ge=0, le=512)]
    nanite_enabled: bool
    collision_mode: Literal[
        "no_collision", "query_only", "physics_only", "query_and_physics"
    ]
    component_tick_enabled: bool
    bounds_radius_cm: Annotated[
        float, Field(gt=0, le=10_000_000, allow_inf_nan=False)
    ]
    performance: StaticMeshPerformanceSnapshot


class UnrealPerformanceSelectionContext(StrictModel):
    """Complete ordered performance evidence for selected StaticMeshActors."""

    schema_version: Literal["0.1"] = "0.1"
    project_name: ShortText
    level_path: Annotated[str, Field(min_length=1, max_length=500)]
    actors: list[UnrealStaticMeshPerformanceContext] = Field(
        min_length=1, max_length=32
    )

    @model_validator(mode="after")
    def actors_share_selection_scope(self) -> "UnrealPerformanceSelectionContext":
        paths = [actor.actor_path for actor in self.actors]
        if len(paths) != len(set(paths)):
            raise ValueError("performance selection cannot repeat an Actor path")
        guids = [
            actor.actor_guid for actor in self.actors if actor.actor_guid is not None
        ]
        if len(guids) != len(set(guids)):
            raise ValueError("performance selection cannot repeat an Actor GUID")
        if any(actor.project_name != self.project_name for actor in self.actors):
            raise ValueError(
                "all selected performance Actors must belong to the captured project"
            )
        if any(actor.level_path != self.level_path for actor in self.actors):
            raise ValueError(
                "all selected performance Actors must belong to the captured level"
            )
        return self


class PerformanceFindingIntent(StrictModel):
    """Model-authored diagnosis constrained to captured evidence fields."""

    actor_path: Annotated[str, Field(min_length=1, max_length=1000)]
    severity: Literal["info", "warning", "critical"]
    category: Literal[
        "geometry", "materials", "nanite", "collision", "tick", "shadows", "culling"
    ]
    evidence_fields: list[PerformanceEvidenceField] = Field(
        min_length=1, max_length=10
    )
    recommendation: LongText

    @model_validator(mode="after")
    def evidence_fields_are_unique(self) -> "PerformanceFindingIntent":
        if len(self.evidence_fields) != len(set(self.evidence_fields)):
            raise ValueError("performance finding cannot repeat an evidence field")
        return self


class PerformanceActorEditIntent(StrictModel):
    """Optional safe component-level changes requested for one captured Actor."""

    actor_path: Annotated[str, Field(min_length=1, max_length=1000)]
    cast_shadow: bool | None = None
    max_draw_distance_cm: Annotated[
        float, Field(ge=0, le=1_000_000, allow_inf_nan=False)
    ] | None = None
    rationale: LongText

    @model_validator(mode="after")
    def includes_one_edit(self) -> "PerformanceActorEditIntent":
        if self.cast_shadow is None and self.max_draw_distance_cm is None:
            raise ValueError("performance action intent must include at least one edit")
        if self.max_draw_distance_cm is not None and 0 < self.max_draw_distance_cm < 500:
            raise ValueError("non-zero max draw distance must be at least 500 cm")
        return self


class PerformanceReviewIntent(StrictModel):
    """Structured model interpretation before exact actions are host-computed."""

    schema_version: Literal["0.1"] = "0.1"
    outcome: Literal["proposed", "review_only", "unresolved"]
    summary: LongText
    findings: list[PerformanceFindingIntent] = Field(default_factory=list, max_length=64)
    actions: list[PerformanceActorEditIntent] = Field(default_factory=list, max_length=32)
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def outcome_is_consistent(self) -> "PerformanceReviewIntent":
        action_paths = [action.actor_path for action in self.actions]
        if len(action_paths) != len(set(action_paths)):
            raise ValueError("performance intent cannot repeat an action Actor path")
        if self.outcome == "proposed":
            if not self.actions or self.missing_capabilities:
                raise ValueError(
                    "proposed performance intent requires actions and no missing capabilities"
                )
        elif self.outcome == "review_only":
            if self.actions or self.missing_capabilities:
                raise ValueError(
                    "review-only performance intent cannot contain actions or capability gaps"
                )
        elif self.findings or self.actions or not self.missing_capabilities:
            raise ValueError(
                "unresolved performance intent requires only missing capabilities"
            )
        return self


class PerformancePropertyChange(StrictModel):
    property: Literal["cast_shadow", "max_draw_distance_cm"]
    before: bool | float
    after: bool | float

    @model_validator(mode="after")
    def value_types_match_property(self) -> "PerformancePropertyChange":
        if self.property == "cast_shadow":
            if type(self.before) is not bool or type(self.after) is not bool:
                raise ValueError("cast_shadow changes require boolean values")
        else:
            if type(self.before) is bool or type(self.after) is bool:
                raise ValueError("max_draw_distance changes require numeric values")
            if not (0 <= float(self.after) <= 1_000_000):
                raise ValueError(
                    "max_draw_distance after value is outside the supported boundary"
                )
            if 0 < float(self.after) < 500:
                raise ValueError("non-zero max draw distance must be at least 500 cm")
        if self.before == self.after:
            raise ValueError("performance change must alter the captured value")
        return self


class PerformanceActorAction(StrictModel):
    """Host-computed Before/After evidence for one selected StaticMeshActor."""

    target: UnrealStaticMeshPerformanceContext
    before: StaticMeshPerformanceSnapshot
    after: StaticMeshPerformanceSnapshot
    changes: list[PerformancePropertyChange] = Field(default_factory=list, max_length=2)
    rationale: LongText

    @model_validator(mode="after")
    def evidence_is_complete(self) -> "PerformanceActorAction":
        if self.before != self.target.performance:
            raise ValueError("performance action Before must match target evidence")
        properties = [change.property for change in self.changes]
        if len(properties) != len(set(properties)):
            raise ValueError("performance action cannot repeat a changed property")
        expected = {
            name
            for name in ("cast_shadow", "max_draw_distance_cm")
            if getattr(self.before, name) != getattr(self.after, name)
        }
        if set(properties) != expected:
            raise ValueError(
                "performance changes must cover every changed property exactly once"
            )
        return self


class AssistantPerformanceProposal(StrictModel):
    """Evidence-backed review with optional approval-gated component changes."""

    schema_version: Literal["0.1"] = "0.1"
    proposal_id: Identifier
    status: Literal["proposed", "review_only", "unresolved"]
    request: LongText
    selection: UnrealPerformanceSelectionContext
    proposed_by: ModelIdentity
    summary: LongText
    findings: list[PerformanceFindingIntent] = Field(default_factory=list, max_length=64)
    actions: list[PerformanceActorAction] = Field(default_factory=list, max_length=32)
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: bool
    auto_save: Literal[False] = False
    undo_supported: Literal[True] = True

    @model_validator(mode="after")
    def status_matches_review(self) -> "AssistantPerformanceProposal":
        selected_paths = {actor.actor_path for actor in self.selection.actors}
        if any(finding.actor_path not in selected_paths for finding in self.findings):
            raise ValueError("performance findings must reference selected Actors")
        if self.status == "proposed":
            if len(self.actions) != len(self.selection.actors):
                raise ValueError(
                    "performance actions must cover every selected Actor exactly once"
                )
            if self.missing_capabilities or not self.modifies_editor_scene:
                raise ValueError(
                    "proposed performance actions must modify the scene without capability gaps"
                )
            for selected, action in zip(
                self.selection.actors, self.actions, strict=True
            ):
                if action.target != selected:
                    raise ValueError(
                        "performance actions must preserve selection order and evidence"
                    )
            if not any(action.changes for action in self.actions):
                raise ValueError(
                    "proposed performance action must contain an observable change"
                )
        elif self.status == "review_only":
            if (
                self.actions
                or self.missing_capabilities
                or self.modifies_editor_scene
            ):
                raise ValueError(
                    "review-only performance proposals cannot contain actions or capability gaps"
                )
        elif (
            self.findings
            or self.actions
            or not self.missing_capabilities
            or self.modifies_editor_scene
        ):
            raise ValueError(
                "unresolved performance proposals contain only missing capabilities"
            )
        return self


RuntimeTimingComponent = Literal[
    "frame_time",
    "game_thread",
    "render_thread",
    "rhi_thread",
    "gpu",
]
RuntimeBottleneck = Literal[
    "game_thread",
    "render_thread",
    "rhi_thread",
    "gpu",
    "mixed",
    "inconclusive",
]
RuntimePerformanceEvidenceField = Literal[
    "target_frame_ms",
    "frame_budget_miss_count",
    "frame_budget_miss_fraction",
    "largest_measured_component",
    "frame_time.mean_ms",
    "frame_time.p50_ms",
    "frame_time.p95_ms",
    "frame_time.max_ms",
    "game_thread.mean_ms",
    "game_thread.p50_ms",
    "game_thread.p95_ms",
    "game_thread.max_ms",
    "render_thread.mean_ms",
    "render_thread.p50_ms",
    "render_thread.p95_ms",
    "render_thread.max_ms",
    "rhi_thread.mean_ms",
    "rhi_thread.p50_ms",
    "rhi_thread.p95_ms",
    "rhi_thread.max_ms",
    "gpu.mean_ms",
    "gpu.p50_ms",
    "gpu.p95_ms",
    "gpu.max_ms",
    "process_working_set_mb",
    "process_peak_working_set_mb",
    "texture_streaming_memory_mb",
    "texture_non_streaming_memory_mb",
    "texture_pool_mb",
    "dedicated_video_memory_mb",
    "viewport_width_px",
    "viewport_height_px",
]


class RuntimeFrameSample(StrictModel):
    """One consecutive PIE/SIE frame sampled by the Unreal Editor host."""

    frame_index: Annotated[int, Field(ge=0, le=599)]
    frame_time_ms: NonNegativeFiniteFloat
    game_thread_ms: NonNegativeFiniteFloat
    render_thread_ms: NonNegativeFiniteFloat
    rhi_thread_ms: NonNegativeFiniteFloat | None = None
    gpu_ms: NonNegativeFiniteFloat | None = None


class RuntimeTimingSummary(StrictModel):
    """Host-computed nearest-rank timing distribution for one metric."""

    available: bool
    sample_count: Annotated[int, Field(ge=0, le=600)]
    mean_ms: NonNegativeFiniteFloat | None = None
    p50_ms: NonNegativeFiniteFloat | None = None
    p95_ms: NonNegativeFiniteFloat | None = None
    max_ms: NonNegativeFiniteFloat | None = None

    @model_validator(mode="after")
    def availability_matches_values(self) -> "RuntimeTimingSummary":
        values = (self.mean_ms, self.p50_ms, self.p95_ms, self.max_ms)
        if self.available:
            if self.sample_count < 1 or any(value is None for value in values):
                raise ValueError("available timing summaries require samples and all values")
            assert self.mean_ms is not None
            assert self.p50_ms is not None
            assert self.p95_ms is not None
            assert self.max_ms is not None
            if not (
                self.p50_ms <= self.p95_ms <= self.max_ms
                and self.mean_ms <= self.max_ms
            ):
                raise ValueError("timing summary percentiles must be ordered")
        elif self.sample_count != 0 or any(value is not None for value in values):
            raise ValueError("unavailable timing summaries cannot contain samples or values")
        return self


def _runtime_summary(values: list[float]) -> tuple[float, float, float, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    count = len(ordered)
    p50_index = max(0, math.ceil(0.50 * count) - 1)
    p95_index = max(0, math.ceil(0.95 * count) - 1)
    return (
        sum(ordered) / count,
        ordered[p50_index],
        ordered[p95_index],
        ordered[-1],
    )


class UnrealRuntimePerformanceCapture(StrictModel):
    """Recomputable runtime timing and memory evidence captured during PIE/SIE."""

    schema_version: Literal["0.1"] = "0.1"
    capture_id: Identifier
    captured_at_utc: datetime
    project_name: ShortText
    world_path: Annotated[str, Field(min_length=1, max_length=500)]
    capture_mode: Literal["pie", "simulate"]
    target_fps: Annotated[float, Field(ge=1.0, le=240.0)]
    target_frame_ms: PositiveFiniteFloat
    warmup_frames: Annotated[int, Field(ge=0, le=300)]
    sample_count: Annotated[int, Field(ge=30, le=600)]
    viewport_width_px: Annotated[int, Field(ge=1, le=16384)]
    viewport_height_px: Annotated[int, Field(ge=1, le=16384)]
    gpu_name: ShortText
    samples: list[RuntimeFrameSample] = Field(min_length=30, max_length=600)
    frame_time: RuntimeTimingSummary
    game_thread: RuntimeTimingSummary
    render_thread: RuntimeTimingSummary
    rhi_thread: RuntimeTimingSummary
    gpu: RuntimeTimingSummary
    frame_budget_miss_count: Annotated[int, Field(ge=0, le=600)]
    frame_budget_miss_fraction: UnitFloat
    largest_measured_component: Literal[
        "game_thread", "render_thread", "rhi_thread", "gpu"
    ]
    process_working_set_mb: NonNegativeFiniteFloat
    process_peak_working_set_mb: NonNegativeFiniteFloat
    texture_streaming_memory_mb: NonNegativeFiniteFloat
    texture_non_streaming_memory_mb: NonNegativeFiniteFloat
    texture_pool_mb: NonNegativeFiniteFloat | None = None
    dedicated_video_memory_mb: NonNegativeFiniteFloat | None = None
    measurement_notes: list[ShortText] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def summaries_match_raw_samples(self) -> "UnrealRuntimePerformanceCapture":
        if self.sample_count != len(self.samples):
            raise ValueError("runtime sample_count must match the raw sample list")
        if [sample.frame_index for sample in self.samples] != list(
            range(self.sample_count)
        ):
            raise ValueError("runtime frame indices must be consecutive and ordered")
        expected_frame_ms = 1000.0 / self.target_fps
        if not math.isclose(self.target_frame_ms, expected_frame_ms, abs_tol=0.01):
            raise ValueError("target_frame_ms must equal 1000 / target_fps")

        sources: dict[str, list[float]] = {
            "frame_time": [sample.frame_time_ms for sample in self.samples],
            "game_thread": [sample.game_thread_ms for sample in self.samples],
            "render_thread": [sample.render_thread_ms for sample in self.samples],
            "rhi_thread": [
                sample.rhi_thread_ms
                for sample in self.samples
                if sample.rhi_thread_ms is not None
            ],
            "gpu": [sample.gpu_ms for sample in self.samples if sample.gpu_ms is not None],
        }
        for name, values in sources.items():
            summary = getattr(self, name)
            expected = _runtime_summary(values)
            if expected is None:
                if summary.available:
                    raise ValueError(f"{name} summary is available without raw samples")
                continue
            if not summary.available or summary.sample_count != len(values):
                raise ValueError(f"{name} summary availability or count is incorrect")
            observed = (
                summary.mean_ms,
                summary.p50_ms,
                summary.p95_ms,
                summary.max_ms,
            )
            if any(
                actual is None
                or not math.isclose(actual, wanted, abs_tol=0.001)
                for actual, wanted in zip(observed, expected, strict=True)
            ):
                raise ValueError(f"{name} summary does not match raw samples")

        misses = sum(
            sample.frame_time_ms > self.target_frame_ms for sample in self.samples
        )
        if self.frame_budget_miss_count != misses or not math.isclose(
            self.frame_budget_miss_fraction,
            misses / self.sample_count,
            abs_tol=0.0001,
        ):
            raise ValueError("frame-budget miss evidence does not match raw samples")

        component_p95 = {
            name: getattr(self, name).p95_ms
            for name in ("game_thread", "render_thread", "rhi_thread", "gpu")
            if getattr(self, name).available
        }
        largest = max(component_p95, key=lambda name: component_p95[name])
        if self.largest_measured_component != largest:
            raise ValueError("largest_measured_component does not match p95 evidence")
        if self.process_peak_working_set_mb < self.process_working_set_mb:
            raise ValueError("peak process memory cannot be below current process memory")
        return self


class RuntimePerformanceFindingIntent(StrictModel):
    severity: Literal["info", "warning", "critical"]
    category: Literal[
        "frame_pacing",
        "game_thread",
        "render_thread",
        "rhi_thread",
        "gpu",
        "memory",
        "measurement",
    ]
    evidence_fields: list[RuntimePerformanceEvidenceField] = Field(
        min_length=1, max_length=12
    )
    observation: LongText
    recommendation: LongText

    @model_validator(mode="after")
    def evidence_fields_are_unique(self) -> "RuntimePerformanceFindingIntent":
        if len(self.evidence_fields) != len(set(self.evidence_fields)):
            raise ValueError("runtime finding evidence fields must be unique")
        return self


class RuntimePerformanceReviewIntent(StrictModel):
    """Model-authored interpretation; the host owns every numeric measurement."""

    schema_version: Literal["0.1"] = "0.1"
    outcome: Literal["review_complete", "unresolved"]
    summary: LongText
    primary_bottleneck: RuntimeBottleneck
    findings: list[RuntimePerformanceFindingIntent] = Field(
        default_factory=list, max_length=16
    )
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def outcome_is_consistent(self) -> "RuntimePerformanceReviewIntent":
        if self.outcome == "review_complete" and self.missing_capabilities:
            raise ValueError("complete runtime reviews cannot contain capability gaps")
        if self.outcome == "unresolved" and (
            self.findings or not self.missing_capabilities
        ):
            raise ValueError("unresolved runtime reviews contain only capability gaps")
        return self


class AssistantRuntimePerformanceReport(StrictModel):
    """Read-only AI review preserving the exact host-owned runtime capture."""

    schema_version: Literal["0.1"] = "0.1"
    report_id: Identifier
    status: Literal["review_complete", "unresolved"]
    request: LongText
    capture: UnrealRuntimePerformanceCapture
    capture_sha256: Sha256
    analyzed_by: ModelIdentity
    summary: LongText
    primary_bottleneck: RuntimeBottleneck
    findings: list[RuntimePerformanceFindingIntent] = Field(
        default_factory=list, max_length=16
    )
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: Literal[False] = False

    @model_validator(mode="after")
    def status_matches_report(self) -> "AssistantRuntimePerformanceReport":
        if self.status == "review_complete" and self.missing_capabilities:
            raise ValueError("complete runtime reports cannot contain capability gaps")
        if self.status == "unresolved" and (
            self.findings or not self.missing_capabilities
        ):
            raise ValueError("unresolved runtime reports contain only capability gaps")
        return self


class InsightsGpuQueue(StrictModel):
    """One GPU queue discovered by Unreal TraceServices."""

    queue_id: Identifier
    display_name: ShortText
    gpu_index: Annotated[int, Field(ge=0, le=31)]
    queue_index: Annotated[int, Field(ge=0, le=31)]
    queue_type: Annotated[int, Field(ge=0, le=255)]
    event_count: Annotated[int, Field(ge=0)]


class InsightsGpuScope(StrictModel):
    """Host-aggregated inclusive timing for one queue-local GPU scope."""

    scope_id: Identifier
    queue_id: Identifier
    name: ShortText
    instance_count: Annotated[int, Field(ge=1)]
    total_inclusive_ms: NonNegativeFiniteFloat
    mean_inclusive_ms: NonNegativeFiniteFloat
    max_inclusive_ms: NonNegativeFiniteFloat
    min_depth: Annotated[int, Field(ge=0, le=1024)]
    max_depth: Annotated[int, Field(ge=0, le=1024)]

    @model_validator(mode="after")
    def summary_is_recomputable(self) -> "InsightsGpuScope":
        expected_mean = self.total_inclusive_ms / self.instance_count
        if not math.isclose(self.mean_inclusive_ms, expected_mean, abs_tol=0.001):
            raise ValueError("GPU scope mean must equal total / instance_count")
        if self.max_inclusive_ms + 0.001 < self.mean_inclusive_ms:
            raise ValueError("GPU scope maximum cannot be below its mean")
        if self.min_depth > self.max_depth:
            raise ValueError("GPU scope depth range must be ordered")
        return self


class UnrealInsightsGpuCapture(StrictModel):
    """Trace-backed GPU scope evidence captured and parsed inside Unreal Editor."""

    schema_version: Literal["0.1"] = "0.1"
    capture_id: Identifier
    captured_at_utc: datetime
    project_name: ShortText
    world_path: Annotated[str, Field(min_length=1, max_length=500)]
    capture_mode: Literal["pie", "simulate"]
    requested_duration_seconds: Annotated[float, Field(ge=1.0, le=30.0)]
    captured_duration_seconds: Annotated[float, Field(gt=0.0, le=60.0)]
    analyzed_trace_duration_seconds: Annotated[float, Field(gt=0.0, le=120.0)]
    viewport_width_px: Annotated[int, Field(ge=1, le=16384)]
    viewport_height_px: Annotated[int, Field(ge=1, le=16384)]
    gpu_name: ShortText
    trace_file_name: RelativeArtifactPath
    trace_file_size_bytes: Annotated[int, Field(gt=0)]
    trace_sha256: Sha256
    channels: list[Identifier] = Field(min_length=1, max_length=32)
    gpu_queue_count: Annotated[int, Field(ge=1, le=128)]
    total_gpu_event_count: Annotated[int, Field(ge=1)]
    queues: list[InsightsGpuQueue] = Field(min_length=1, max_length=128)
    scopes: list[InsightsGpuScope] = Field(min_length=1, max_length=64)
    measurement_notes: list[ShortText] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def trace_evidence_is_consistent(self) -> "UnrealInsightsGpuCapture":
        if self.gpu_queue_count != len(self.queues):
            raise ValueError("GPU queue count must match the queue list")
        queue_ids = [queue.queue_id for queue in self.queues]
        if len(queue_ids) != len(set(queue_ids)):
            raise ValueError("GPU queue IDs must be unique")
        scope_ids = [scope.scope_id for scope in self.scopes]
        if len(scope_ids) != len(set(scope_ids)):
            raise ValueError("GPU scope IDs must be unique")
        known_queues = set(queue_ids)
        if any(scope.queue_id not in known_queues for scope in self.scopes):
            raise ValueError("every GPU scope must reference a captured queue")
        if sum(scope.instance_count for scope in self.scopes) > self.total_gpu_event_count:
            raise ValueError("top GPU scope instances cannot exceed all captured events")
        totals = [scope.total_inclusive_ms for scope in self.scopes]
        if totals != sorted(totals, reverse=True):
            raise ValueError("GPU scopes must be ordered by total inclusive time")
        if len(self.channels) != len(set(self.channels)):
            raise ValueError("trace channels must be unique")
        return self


class InsightsGpuFindingIntent(StrictModel):
    severity: Literal["info", "warning", "critical"]
    category: Literal["gpu_scope", "gpu_queue", "measurement"]
    evidence_scope_ids: list[Identifier] = Field(min_length=1, max_length=12)
    observation: LongText
    recommendation: LongText

    @model_validator(mode="after")
    def scope_ids_are_unique(self) -> "InsightsGpuFindingIntent":
        if len(self.evidence_scope_ids) != len(set(self.evidence_scope_ids)):
            raise ValueError("GPU finding scope IDs must be unique")
        return self


class InsightsGpuReviewIntent(StrictModel):
    """Model-authored interpretation of host-owned Unreal Insights evidence."""

    schema_version: Literal["0.1"] = "0.1"
    outcome: Literal["review_complete", "unresolved"]
    summary: LongText
    primary_scope_id: Identifier | None = None
    findings: list[InsightsGpuFindingIntent] = Field(default_factory=list, max_length=16)
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def outcome_is_consistent(self) -> "InsightsGpuReviewIntent":
        if self.outcome == "review_complete" and self.missing_capabilities:
            raise ValueError("complete GPU scope reviews cannot contain capability gaps")
        if self.outcome == "unresolved" and (
            self.primary_scope_id is not None
            or self.findings
            or not self.missing_capabilities
        ):
            raise ValueError("unresolved GPU scope reviews contain only capability gaps")
        return self


class AssistantInsightsGpuReport(StrictModel):
    """Read-only local-model review preserving the exact trace-derived capture."""

    schema_version: Literal["0.1"] = "0.1"
    report_id: Identifier
    status: Literal["review_complete", "unresolved"]
    request: LongText
    capture: UnrealInsightsGpuCapture
    capture_sha256: Sha256
    capture_file_sha256: Sha256
    analyzed_by: ModelIdentity
    summary: LongText
    primary_scope_id: Identifier | None = None
    findings: list[InsightsGpuFindingIntent] = Field(default_factory=list, max_length=16)
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: Literal[False] = False

    @model_validator(mode="after")
    def report_is_grounded(self) -> "AssistantInsightsGpuReport":
        expected_capture_sha256 = hashlib.sha256(
            self.capture.model_dump_json().encode("utf-8")
        ).hexdigest()
        if self.capture_sha256 != expected_capture_sha256:
            raise ValueError("GPU report capture hash does not match its embedded capture")
        known_scope_ids = {scope.scope_id for scope in self.capture.scopes}
        cited_scope_ids = {
            scope_id
            for finding in self.findings
            for scope_id in finding.evidence_scope_ids
        }
        if self.primary_scope_id is not None and self.primary_scope_id not in known_scope_ids:
            raise ValueError("primary GPU scope must exist in the capture")
        if not cited_scope_ids.issubset(known_scope_ids):
            raise ValueError("GPU findings may cite only captured scope IDs")
        if self.status == "review_complete" and self.missing_capabilities:
            raise ValueError("complete GPU scope reports cannot contain capability gaps")
        if self.status == "unresolved" and (
            self.primary_scope_id is not None
            or self.findings
            or not self.missing_capabilities
        ):
            raise ValueError("unresolved GPU scope reports contain only capability gaps")
        return self


class ActorGpuImpactTarget(StrictModel):
    """One Editor Actor and its temporary PIE/SIE counterpart."""

    editor_actor_path: Annotated[str, Field(min_length=1, max_length=500)]
    runtime_actor_path: Annotated[str, Field(min_length=1, max_length=500)]
    actor_label: ShortText
    actor_class: Annotated[str, Field(min_length=1, max_length=500)]
    primitive_component_count: Annotated[int, Field(ge=1, le=1024)]
    baseline_hidden_in_game: Literal[False]
    restored_hidden_in_game: Literal[False]


class ActorGpuScopeDelta(StrictModel):
    """Recomputable normalized change for one scope present in both traces."""

    delta_id: Identifier
    baseline_scope_id: Identifier
    variant_scope_id: Identifier
    queue_name: ShortText
    scope_name: ShortText
    baseline_total_ms_per_second: NonNegativeFiniteFloat
    variant_total_ms_per_second: NonNegativeFiniteFloat
    baseline_instances_per_second: NonNegativeFiniteFloat
    variant_instances_per_second: NonNegativeFiniteFloat
    baseline_minus_variant_ms_per_second: FiniteFloat
    relative_reduction_percent: Annotated[
        float, Field(ge=-1_000_000.0, le=100.0, allow_inf_nan=False)
    ]
    direction_when_hidden: Literal["decreased", "increased", "unchanged"]

    @model_validator(mode="after")
    def delta_is_recomputable(self) -> "ActorGpuScopeDelta":
        expected_delta = (
            self.baseline_total_ms_per_second - self.variant_total_ms_per_second
        )
        expected_percent = (
            expected_delta / self.baseline_total_ms_per_second * 100.0
        )
        if not math.isclose(
            self.baseline_minus_variant_ms_per_second,
            expected_delta,
            abs_tol=0.001,
        ):
            raise ValueError("Actor GPU scope delta does not match its normalized totals")
        if not math.isclose(
            self.relative_reduction_percent,
            expected_percent,
            abs_tol=0.001,
        ):
            raise ValueError("Actor GPU scope percentage does not match its normalized totals")
        epsilon = 0.001
        expected_direction = (
            "decreased"
            if expected_delta > epsilon
            else "increased"
            if expected_delta < -epsilon
            else "unchanged"
        )
        if self.direction_when_hidden != expected_direction:
            raise ValueError("Actor GPU scope direction does not match its delta")
        return self


class UnrealActorGpuImpactExperiment(StrictModel):
    """Host-owned sequential A/B traces around one temporary runtime visibility change."""

    schema_version: Literal["0.1"] = "0.1"
    experiment_id: Identifier
    created_at_utc: datetime
    method: Literal["temporary_runtime_actor_hidden"]
    target: ActorGpuImpactTarget
    variant_warmup_seconds: Annotated[float, Field(ge=0.1, le=10.0)]
    baseline: UnrealInsightsGpuCapture
    variant: UnrealInsightsGpuCapture
    comparison_count: Annotated[int, Field(ge=1, le=64)]
    deltas: list[ActorGpuScopeDelta] = Field(min_length=1, max_length=64)
    unmatched_baseline_scope_ids: list[Identifier] = Field(
        default_factory=list, max_length=64
    )
    unmatched_variant_scope_ids: list[Identifier] = Field(
        default_factory=list, max_length=64
    )
    trial_count: Literal[1] = 1
    repeatability: Literal["single_trial"] = "single_trial"
    runtime_state_restored: Literal[True] = True
    measurement_notes: list[ShortText] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def experiment_is_recomputable(self) -> "UnrealActorGpuImpactExperiment":
        baseline = self.baseline
        variant = self.variant
        if baseline.capture_id == variant.capture_id:
            raise ValueError("Actor GPU baseline and variant require distinct capture IDs")
        stable_fields = (
            "project_name",
            "world_path",
            "capture_mode",
            "viewport_width_px",
            "viewport_height_px",
            "gpu_name",
            "channels",
        )
        if any(getattr(baseline, field) != getattr(variant, field) for field in stable_fields):
            raise ValueError("Actor GPU baseline and variant environment must match")
        if self.comparison_count != len(self.deltas):
            raise ValueError("Actor GPU comparison count must match the delta list")
        delta_ids = [delta.delta_id for delta in self.deltas]
        if len(delta_ids) != len(set(delta_ids)):
            raise ValueError("Actor GPU delta IDs must be unique")
        baseline_scopes = {scope.scope_id: scope for scope in baseline.scopes}
        variant_scopes = {scope.scope_id: scope for scope in variant.scopes}
        matched_baseline: set[str] = set()
        matched_variant: set[str] = set()
        for delta in self.deltas:
            baseline_scope = baseline_scopes.get(delta.baseline_scope_id)
            variant_scope = variant_scopes.get(delta.variant_scope_id)
            if baseline_scope is None or variant_scope is None:
                raise ValueError("Actor GPU deltas must cite scopes from both captures")
            if (
                baseline_scope.queue_id != variant_scope.queue_id
                or baseline_scope.name != variant_scope.name
                or delta.queue_name
                != next(
                    queue.display_name
                    for queue in baseline.queues
                    if queue.queue_id == baseline_scope.queue_id
                )
                or delta.scope_name != baseline_scope.name
            ):
                raise ValueError("Actor GPU deltas must preserve queue-local scope identity")
            expected_baseline_total = (
                baseline_scope.total_inclusive_ms / baseline.captured_duration_seconds
            )
            expected_variant_total = (
                variant_scope.total_inclusive_ms / variant.captured_duration_seconds
            )
            expected_baseline_instances = (
                baseline_scope.instance_count / baseline.captured_duration_seconds
            )
            expected_variant_instances = (
                variant_scope.instance_count / variant.captured_duration_seconds
            )
            if not all(
                (
                    math.isclose(
                        delta.baseline_total_ms_per_second,
                        expected_baseline_total,
                        abs_tol=0.001,
                    ),
                    math.isclose(
                        delta.variant_total_ms_per_second,
                        expected_variant_total,
                        abs_tol=0.001,
                    ),
                    math.isclose(
                        delta.baseline_instances_per_second,
                        expected_baseline_instances,
                        abs_tol=0.001,
                    ),
                    math.isclose(
                        delta.variant_instances_per_second,
                        expected_variant_instances,
                        abs_tol=0.001,
                    ),
                )
            ):
                raise ValueError("Actor GPU normalized rates must match both captures")
            matched_baseline.add(delta.baseline_scope_id)
            matched_variant.add(delta.variant_scope_id)
        expected_unmatched_baseline = set(baseline_scopes) - matched_baseline
        expected_unmatched_variant = set(variant_scopes) - matched_variant
        if set(self.unmatched_baseline_scope_ids) != expected_unmatched_baseline:
            raise ValueError("Actor GPU unmatched baseline IDs are incomplete")
        if set(self.unmatched_variant_scope_ids) != expected_unmatched_variant:
            raise ValueError("Actor GPU unmatched variant IDs are incomplete")
        magnitudes = [abs(delta.baseline_minus_variant_ms_per_second) for delta in self.deltas]
        if magnitudes != sorted(magnitudes, reverse=True):
            raise ValueError("Actor GPU deltas must be ordered by absolute change")
        return self


class ActorGpuImpactFindingIntent(StrictModel):
    severity: Literal["info", "warning", "critical"]
    category: Literal["impact_candidate", "measurement_risk", "next_experiment"]
    evidence_delta_ids: list[Identifier] = Field(min_length=1, max_length=12)
    observation: LongText
    recommendation: LongText

    @model_validator(mode="after")
    def delta_ids_are_unique(self) -> "ActorGpuImpactFindingIntent":
        if len(self.evidence_delta_ids) != len(set(self.evidence_delta_ids)):
            raise ValueError("Actor GPU finding delta IDs must be unique")
        return self


class ActorGpuImpactReviewIntent(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    outcome: Literal["review_complete", "unresolved"]
    summary: LongText
    primary_delta_id: Identifier | None = None
    findings: list[ActorGpuImpactFindingIntent] = Field(default_factory=list, max_length=16)
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def outcome_is_consistent(self) -> "ActorGpuImpactReviewIntent":
        if self.outcome == "review_complete" and self.missing_capabilities:
            raise ValueError("complete Actor GPU reviews cannot contain capability gaps")
        if self.outcome == "unresolved" and (
            self.primary_delta_id is not None
            or self.findings
            or not self.missing_capabilities
        ):
            raise ValueError("unresolved Actor GPU reviews contain only capability gaps")
        return self


class AssistantActorGpuImpactReport(StrictModel):
    """Read-only local-model review preserving the exact A/B experiment."""

    schema_version: Literal["0.1"] = "0.1"
    report_id: Identifier
    status: Literal["review_complete", "unresolved"]
    request: LongText
    experiment: UnrealActorGpuImpactExperiment
    experiment_sha256: Sha256
    experiment_file_sha256: Sha256
    analyzed_by: ModelIdentity
    summary: LongText
    primary_delta_id: Identifier | None = None
    findings: list[ActorGpuImpactFindingIntent] = Field(default_factory=list, max_length=16)
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: Literal[False] = False

    @model_validator(mode="after")
    def report_is_grounded(self) -> "AssistantActorGpuImpactReport":
        expected_sha256 = hashlib.sha256(
            self.experiment.model_dump_json().encode("utf-8")
        ).hexdigest()
        if self.experiment_sha256 != expected_sha256:
            raise ValueError("Actor GPU report hash does not match its embedded experiment")
        known_delta_ids = {delta.delta_id for delta in self.experiment.deltas}
        cited_delta_ids = {
            delta_id
            for finding in self.findings
            for delta_id in finding.evidence_delta_ids
        }
        if self.primary_delta_id is not None and self.primary_delta_id not in known_delta_ids:
            raise ValueError("primary Actor GPU delta must exist in the experiment")
        if not cited_delta_ids.issubset(known_delta_ids):
            raise ValueError("Actor GPU findings may cite only measured delta IDs")
        if self.status == "review_complete" and self.missing_capabilities:
            raise ValueError("complete Actor GPU reports cannot contain capability gaps")
        if self.status == "unresolved" and (
            self.primary_delta_id is not None
            or self.findings
            or not self.missing_capabilities
        ):
            raise ValueError("unresolved Actor GPU reports contain only capability gaps")
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
    "unreal-lighting-rig-context": UnrealLightingRigContext,
    "assistant-lighting-rig-proposal": AssistantLightingRigProposal,
    "unreal-lighting-rig-review-context": UnrealLightingRigReviewContext,
    "assistant-lighting-rig-review-proposal": AssistantLightingRigReviewProposal,
    "unreal-camera-context": UnrealCameraContext,
    "assistant-camera-proposal": AssistantCameraProposal,
    "unreal-camera-selection-context": UnrealCameraSelectionContext,
    "assistant-camera-batch-proposal": AssistantCameraBatchProposal,
    "unreal-performance-selection-context": UnrealPerformanceSelectionContext,
    "assistant-performance-proposal": AssistantPerformanceProposal,
    "unreal-runtime-performance-capture": UnrealRuntimePerformanceCapture,
    "assistant-runtime-performance-report": AssistantRuntimePerformanceReport,
    "unreal-insights-gpu-capture": UnrealInsightsGpuCapture,
    "assistant-insights-gpu-report": AssistantInsightsGpuReport,
    "unreal-actor-gpu-impact-experiment": UnrealActorGpuImpactExperiment,
    "assistant-actor-gpu-impact-report": AssistantActorGpuImpactReport,
    "render-spec-patch": RenderSpecPatch,
    "correction-decision": CorrectionDecision,
    "evaluation-report": EvaluationReport,
    "visual-benchmark-suite": VisualBenchmarkSuite,
    "visual-benchmark-report": VisualBenchmarkReport,
    "render-workflow-manifest": RenderWorkflowManifest,
    "capability-manifest": CapabilityManifest,
    "run-manifest": RunManifest,
}
