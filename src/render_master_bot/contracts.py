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
from render_master_bot.serialization import canonical_sha256


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


class UnrealLevelAuditContext(StrictModel):
    """Complete static performance evidence for one Editor level."""

    schema_version: Literal["0.1"] = "0.1"
    audit_id: Identifier
    captured_at_utc: datetime
    project_name: ShortText
    level_path: Annotated[str, Field(min_length=1, max_length=500)]
    static_mesh_actor_count: Annotated[int, Field(ge=1, le=100_000)]
    excluded_actor_count: Annotated[int, Field(ge=0, le=100_000)]
    actors: list[UnrealStaticMeshPerformanceContext] = Field(
        min_length=1, max_length=4096
    )

    @model_validator(mode="after")
    def actors_cover_the_auditable_level(self) -> "UnrealLevelAuditContext":
        if self.static_mesh_actor_count != len(self.actors) + self.excluded_actor_count:
            raise ValueError(
                "level audit Actor counts must equal captured plus excluded Actors"
            )
        paths = [actor.actor_path for actor in self.actors]
        if len(paths) != len(set(paths)):
            raise ValueError("level audit cannot repeat an Actor path")
        guids = [
            actor.actor_guid for actor in self.actors if actor.actor_guid is not None
        ]
        if len(guids) != len(set(guids)):
            raise ValueError("level audit cannot repeat an Actor GUID")
        if any(actor.project_name != self.project_name for actor in self.actors):
            raise ValueError("level audit Actors must belong to the captured project")
        if any(actor.level_path != self.level_path for actor in self.actors):
            raise ValueError("level audit Actors must belong to the captured level")
        return self


class LevelAuditStatistics(StrictModel):
    """Host-computed whole-level counts; none are model-authored."""

    captured_actor_count: Annotated[int, Field(ge=1, le=4096)]
    excluded_actor_count: Annotated[int, Field(ge=0, le=100_000)]
    total_lod0_triangles: Annotated[int, Field(ge=0, le=10**15)]
    total_material_slots: Annotated[int, Field(ge=0, le=10**9)]
    actors_without_nanite_count: Annotated[int, Field(ge=0, le=4096)]
    single_lod_actor_count: Annotated[int, Field(ge=0, le=4096)]
    ticking_component_count: Annotated[int, Field(ge=0, le=4096)]
    shadow_casting_actor_count: Annotated[int, Field(ge=0, le=4096)]
    unbounded_draw_distance_count: Annotated[int, Field(ge=0, le=4096)]
    query_and_physics_collision_count: Annotated[int, Field(ge=0, le=4096)]
    high_priority_actor_count: Annotated[int, Field(ge=0, le=4096)]


class LevelAuditActorAssessment(StrictModel):
    """Transparent deterministic priority signal for one captured Actor."""

    assessment_id: Identifier
    actor_path: Annotated[str, Field(min_length=1, max_length=1000)]
    risk_score: Annotated[int, Field(ge=0, le=100)]
    risk_band: Literal["low", "medium", "high", "critical"]
    evidence_fields: list[PerformanceEvidenceField] = Field(
        default_factory=list, max_length=10
    )
    reasons: list[ShortText] = Field(default_factory=list, max_length=10)
    recommended_next_test: Literal[
        "none", "selected_mesh_review", "repeatable_actor_gpu_study"
    ]

    @model_validator(mode="after")
    def evidence_is_unique(self) -> "LevelAuditActorAssessment":
        if len(self.evidence_fields) != len(set(self.evidence_fields)):
            raise ValueError("level audit assessment evidence fields must be unique")
        return self


def _level_audit_risk_components(
    actor: UnrealStaticMeshPerformanceContext,
) -> tuple[int, list[PerformanceEvidenceField], list[str]]:
    score = 0
    evidence: list[PerformanceEvidenceField] = []
    reasons: list[str] = []

    def add(
        points: int,
        fields: tuple[PerformanceEvidenceField, ...],
        reason: str,
    ) -> None:
        nonlocal score
        score += points
        for field in fields:
            if field not in evidence:
                evidence.append(field)
        reasons.append(reason)

    if actor.lod0_triangles >= 2_000_000:
        add(30, ("lod0_triangles",), "LOD0 contains at least 2,000,000 triangles.")
    elif actor.lod0_triangles >= 500_000:
        add(20, ("lod0_triangles",), "LOD0 contains at least 500,000 triangles.")
    elif actor.lod0_triangles >= 100_000:
        add(10, ("lod0_triangles",), "LOD0 contains at least 100,000 triangles.")

    if not actor.nanite_enabled and actor.lod0_triangles >= 500_000:
        add(
            25,
            ("nanite_enabled", "lod0_triangles"),
            "A dense mesh is captured without Nanite enabled.",
        )
    elif not actor.nanite_enabled and actor.lod0_triangles >= 100_000:
        add(
            15,
            ("nanite_enabled", "lod0_triangles"),
            "A high-triangle mesh is captured without Nanite enabled.",
        )

    if actor.lod_count == 1 and actor.lod0_triangles >= 100_000:
        add(
            20,
            ("lod_count", "lod0_triangles"),
            "The high-triangle mesh has only one captured LOD.",
        )
    elif actor.lod_count == 1:
        add(5, ("lod_count",), "The mesh has only one captured LOD.")

    if actor.material_slot_count >= 8:
        add(
            12,
            ("material_slot_count",),
            "The mesh exposes at least eight material slots.",
        )
    elif actor.material_slot_count >= 4:
        add(
            6,
            ("material_slot_count",),
            "The mesh exposes at least four material slots.",
        )

    if actor.component_tick_enabled:
        add(
            15,
            ("component_tick_enabled",),
            "The StaticMeshComponent has Tick enabled.",
        )
    if actor.collision_mode == "query_and_physics":
        add(
            5,
            ("collision_mode",),
            "The component participates in query and physics collision.",
        )
    if actor.performance.cast_shadow and actor.bounds_radius_cm >= 5_000:
        add(
            10,
            ("cast_shadow", "bounds_radius_cm"),
            "A very large captured bounds radius also casts shadows.",
        )
    if (
        actor.performance.max_draw_distance_cm == 0
        and actor.bounds_radius_cm <= 500
    ):
        add(
            5,
            ("max_draw_distance_cm", "bounds_radius_cm"),
            "A compact Actor has no component draw-distance limit.",
        )
    return min(score, 100), evidence, reasons


def build_level_audit_assessments(
    context: UnrealLevelAuditContext,
) -> list[LevelAuditActorAssessment]:
    provisional: list[tuple[UnrealStaticMeshPerformanceContext, int, list, list]] = []
    for actor in context.actors:
        score, evidence, reasons = _level_audit_risk_components(actor)
        provisional.append((actor, score, evidence, reasons))
    provisional.sort(key=lambda item: (-item[1], item[0].actor_path.casefold()))

    assessments: list[LevelAuditActorAssessment] = []
    for index, (actor, score, evidence, reasons) in enumerate(provisional):
        band = (
            "critical"
            if score >= 75
            else "high"
            if score >= 50
            else "medium"
            if score >= 25
            else "low"
        )
        next_test = (
            "repeatable_actor_gpu_study"
            if score >= 50
            else "selected_mesh_review"
            if score >= 25
            else "none"
        )
        assessments.append(
            LevelAuditActorAssessment(
                assessment_id=f"audit_actor_{index:04d}",
                actor_path=actor.actor_path,
                risk_score=score,
                risk_band=band,
                evidence_fields=evidence,
                reasons=reasons,
                recommended_next_test=next_test,
            )
        )
    return assessments


def build_level_audit_statistics(
    context: UnrealLevelAuditContext,
) -> LevelAuditStatistics:
    scores = [_level_audit_risk_components(actor)[0] for actor in context.actors]
    return LevelAuditStatistics(
        captured_actor_count=len(context.actors),
        excluded_actor_count=context.excluded_actor_count,
        total_lod0_triangles=sum(actor.lod0_triangles for actor in context.actors),
        total_material_slots=sum(
            actor.material_slot_count for actor in context.actors
        ),
        actors_without_nanite_count=sum(
            not actor.nanite_enabled for actor in context.actors
        ),
        single_lod_actor_count=sum(actor.lod_count == 1 for actor in context.actors),
        ticking_component_count=sum(
            actor.component_tick_enabled for actor in context.actors
        ),
        shadow_casting_actor_count=sum(
            actor.performance.cast_shadow for actor in context.actors
        ),
        unbounded_draw_distance_count=sum(
            actor.performance.max_draw_distance_cm == 0 for actor in context.actors
        ),
        query_and_physics_collision_count=sum(
            actor.collision_mode == "query_and_physics" for actor in context.actors
        ),
        high_priority_actor_count=sum(score >= 50 for score in scores),
    )


class LevelAuditFindingIntent(StrictModel):
    severity: Literal["info", "warning", "critical"]
    category: Literal[
        "level_overview",
        "geometry",
        "materials",
        "nanite",
        "collision",
        "tick",
        "shadows",
        "culling",
        "measurement",
    ]
    evidence_assessment_ids: list[Identifier] = Field(min_length=1, max_length=12)
    observation: LongText
    recommendation: LongText

    @model_validator(mode="after")
    def assessment_ids_are_unique(self) -> "LevelAuditFindingIntent":
        if len(self.evidence_assessment_ids) != len(
            set(self.evidence_assessment_ids)
        ):
            raise ValueError("level audit finding assessment IDs must be unique")
        return self


class LevelAuditActorEditIntent(StrictModel):
    assessment_id: Identifier
    cast_shadow: bool | None = None
    max_draw_distance_cm: Annotated[
        float, Field(ge=0, le=1_000_000, allow_inf_nan=False)
    ] | None = None
    rationale: LongText

    @model_validator(mode="after")
    def includes_one_edit(self) -> "LevelAuditActorEditIntent":
        if self.cast_shadow is None and self.max_draw_distance_cm is None:
            raise ValueError("level audit action must include at least one edit")
        if self.max_draw_distance_cm is not None and 0 < self.max_draw_distance_cm < 500:
            raise ValueError("non-zero max draw distance must be at least 500 cm")
        return self


class LevelAuditReviewIntent(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    outcome: Literal["proposed", "review_only", "unresolved"]
    summary: LongText
    primary_assessment_id: Identifier | None = None
    findings: list[LevelAuditFindingIntent] = Field(default_factory=list, max_length=64)
    actions: list[LevelAuditActorEditIntent] = Field(default_factory=list, max_length=32)
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def outcome_is_consistent(self) -> "LevelAuditReviewIntent":
        action_ids = [action.assessment_id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("level audit intent cannot repeat an action assessment ID")
        if self.outcome == "proposed":
            if not self.actions or self.missing_capabilities:
                raise ValueError("proposed level audits require actions and no gaps")
        elif self.outcome == "review_only":
            if self.actions or self.missing_capabilities:
                raise ValueError("review-only level audits cannot contain actions or gaps")
        elif (
            self.primary_assessment_id is not None
            or self.findings
            or self.actions
            or not self.missing_capabilities
        ):
            raise ValueError("unresolved level audits contain only capability gaps")
        return self


class LevelAuditActorAction(PerformanceActorAction):
    assessment_id: Identifier


class AssistantLevelAuditReport(StrictModel):
    """Whole-level static audit with optional approval-gated safe actions."""

    schema_version: Literal["0.1"] = "0.1"
    audit_id: Identifier
    status: Literal["proposed", "review_only", "unresolved"]
    request: LongText
    context: UnrealLevelAuditContext
    context_sha256: Sha256
    statistics: LevelAuditStatistics
    assessments: list[LevelAuditActorAssessment] = Field(
        min_length=1, max_length=4096
    )
    analyzed_by: ModelIdentity
    summary: LongText
    primary_assessment_id: Identifier | None = None
    findings: list[LevelAuditFindingIntent] = Field(default_factory=list, max_length=64)
    actions: list[LevelAuditActorAction] = Field(default_factory=list, max_length=32)
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: bool
    auto_save: Literal[False] = False
    undo_supported: Literal[True] = True

    @model_validator(mode="after")
    def report_is_grounded(self) -> "AssistantLevelAuditReport":
        expected_hash = hashlib.sha256(
            self.context.model_dump_json().encode("utf-8")
        ).hexdigest()
        if self.context_sha256 != expected_hash:
            raise ValueError("level audit context hash is invalid")
        if self.statistics != build_level_audit_statistics(self.context):
            raise ValueError("level audit statistics are not recomputable")
        expected_assessments = build_level_audit_assessments(self.context)
        if self.assessments != expected_assessments:
            raise ValueError("level audit assessments are not recomputable")
        assessments_by_id = {
            item.assessment_id: item for item in self.assessments
        }
        known_ids = set(assessments_by_id)
        if (
            self.primary_assessment_id is not None
            and self.primary_assessment_id not in known_ids
        ):
            raise ValueError("primary level audit assessment is unavailable")
        cited_ids = {
            assessment_id
            for finding in self.findings
            for assessment_id in finding.evidence_assessment_ids
        }
        if not cited_ids.issubset(known_ids):
            raise ValueError("level audit findings cite unavailable assessments")
        context_by_path = {actor.actor_path: actor for actor in self.context.actors}
        action_ids = [action.assessment_id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("level audit actions repeat an assessment")
        for action in self.actions:
            assessment = assessments_by_id.get(action.assessment_id)
            if assessment is None or action.target.actor_path != assessment.actor_path:
                raise ValueError("level audit action target does not match its assessment")
            if action.target != context_by_path.get(action.target.actor_path):
                raise ValueError("level audit action target does not match captured context")
            if not action.changes:
                raise ValueError("level audit actions must contain an observable change")
        if self.status == "proposed":
            if (
                not self.actions
                or self.missing_capabilities
                or not self.modifies_editor_scene
            ):
                raise ValueError("proposed level audits require safe executable actions")
        elif self.status == "review_only":
            if self.actions or self.missing_capabilities or self.modifies_editor_scene:
                raise ValueError("review-only level audits cannot modify the scene")
        elif (
            self.primary_assessment_id is not None
            or self.findings
            or self.actions
            or not self.missing_capabilities
            or self.modifies_editor_scene
        ):
            raise ValueError("unresolved level audit reports contain only gaps")
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


class ActorGpuImpactStudyTrial(StrictModel):
    """One counterbalanced visible/hidden pair with chronological order preserved."""

    trial_id: Identifier
    order: Literal["visible_then_hidden", "hidden_then_visible"]
    visible: UnrealInsightsGpuCapture
    hidden: UnrealInsightsGpuCapture

    @model_validator(mode="after")
    def captures_are_distinct(self) -> "ActorGpuImpactStudyTrial":
        if self.visible.capture_id == self.hidden.capture_id:
            raise ValueError("Actor GPU study trial captures must be distinct")
        return self


class UnrealActorGpuImpactStudyCaptureSet(StrictModel):
    """Eight host-owned captures for A/A noise and AB/BA/AB Actor trials."""

    schema_version: Literal["0.1"] = "0.1"
    study_id: Identifier
    created_at_utc: datetime
    method: Literal["temporary_runtime_actor_hidden_counterbalanced"]
    target: ActorGpuImpactTarget
    requested_capture_duration_seconds: Annotated[float, Field(ge=1.0, le=30.0)]
    warmup_seconds: Annotated[float, Field(ge=0.1, le=10.0)]
    schedule: Literal["AA_AB_BA_AB"]
    noise_control_visible: list[UnrealInsightsGpuCapture] = Field(
        min_length=2, max_length=2
    )
    trials: list[ActorGpuImpactStudyTrial] = Field(min_length=3, max_length=3)
    sequence_capture_ids: list[Identifier] = Field(min_length=8, max_length=8)
    trial_count: Literal[3] = 3
    repeatability: Literal["counterbalanced_three_trial"] = (
        "counterbalanced_three_trial"
    )
    runtime_state_restored: Literal[True] = True
    measurement_notes: list[ShortText] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def study_schedule_is_consistent(self) -> "UnrealActorGpuImpactStudyCaptureSet":
        expected_trial_ids = ["trial_001", "trial_002", "trial_003"]
        expected_orders = [
            "visible_then_hidden",
            "hidden_then_visible",
            "visible_then_hidden",
        ]
        if [trial.trial_id for trial in self.trials] != expected_trial_ids:
            raise ValueError("Actor GPU study requires trial_001 through trial_003")
        if [trial.order for trial in self.trials] != expected_orders:
            raise ValueError("Actor GPU study trial order must be AB, BA, AB")

        chronological = [
            *self.noise_control_visible,
            self.trials[0].visible,
            self.trials[0].hidden,
            self.trials[1].hidden,
            self.trials[1].visible,
            self.trials[2].visible,
            self.trials[2].hidden,
        ]
        expected_sequence = [capture.capture_id for capture in chronological]
        if self.sequence_capture_ids != expected_sequence:
            raise ValueError("Actor GPU study capture sequence must match AA_AB_BA_AB")
        if len(expected_sequence) != len(set(expected_sequence)):
            raise ValueError("Actor GPU study capture IDs must be unique")
        trace_names = [capture.trace_file_name.casefold() for capture in chronological]
        if len(trace_names) != len(set(trace_names)):
            raise ValueError("Actor GPU study trace filenames must be unique")
        timestamps = [capture.captured_at_utc for capture in chronological]
        if timestamps != sorted(timestamps):
            raise ValueError("Actor GPU study captures must preserve chronological time")

        reference = chronological[0]
        stable_fields = (
            "project_name",
            "world_path",
            "capture_mode",
            "viewport_width_px",
            "viewport_height_px",
            "gpu_name",
            "channels",
        )
        for capture in chronological:
            if any(
                getattr(capture, field) != getattr(reference, field)
                for field in stable_fields
            ):
                raise ValueError("Actor GPU study capture environments must match")
            if not math.isclose(
                capture.requested_duration_seconds,
                self.requested_capture_duration_seconds,
                abs_tol=0.001,
            ):
                raise ValueError("Actor GPU study requested durations must match")
        return self


class ActorGpuImpactStudyTrialMeasurement(StrictModel):
    """Recomputable paired rate change for one queue-local scope and trial."""

    trial_id: Identifier
    order: Literal["visible_then_hidden", "hidden_then_visible"]
    visible_scope_id: Identifier
    hidden_scope_id: Identifier
    visible_total_ms_per_second: NonNegativeFiniteFloat
    hidden_total_ms_per_second: NonNegativeFiniteFloat
    baseline_minus_hidden_ms_per_second: FiniteFloat

    @model_validator(mode="after")
    def paired_delta_is_recomputable(self) -> "ActorGpuImpactStudyTrialMeasurement":
        expected = (
            self.visible_total_ms_per_second - self.hidden_total_ms_per_second
        )
        if not math.isclose(
            self.baseline_minus_hidden_ms_per_second, expected, abs_tol=0.001
        ):
            raise ValueError("Actor GPU study trial delta is not recomputable")
        return self


class ActorGpuImpactStudyAggregate(StrictModel):
    """Host-computed noise and three-trial statistics for one common scope."""

    aggregate_id: Identifier
    queue_name: ShortText
    scope_name: ShortText
    control_scope_ids: list[Identifier] = Field(min_length=2, max_length=2)
    control_first_total_ms_per_second: NonNegativeFiniteFloat
    control_second_total_ms_per_second: NonNegativeFiniteFloat
    control_noise_abs_ms_per_second: NonNegativeFiniteFloat
    trial_measurements: list[ActorGpuImpactStudyTrialMeasurement] = Field(
        min_length=3, max_length=3
    )
    median_visible_total_ms_per_second: NonNegativeFiniteFloat
    median_hidden_total_ms_per_second: NonNegativeFiniteFloat
    median_baseline_minus_hidden_ms_per_second: FiniteFloat
    delta_mad_ms_per_second: NonNegativeFiniteFloat
    relative_reduction_percent: Annotated[
        float, Field(ge=-1_000_000.0, le=100.0, allow_inf_nan=False)
    ]
    decreased_vote_count: Annotated[int, Field(ge=0, le=3)]
    increased_vote_count: Annotated[int, Field(ge=0, le=3)]
    unchanged_vote_count: Annotated[int, Field(ge=0, le=3)]
    dominant_direction_when_hidden: Literal[
        "decreased", "increased", "unchanged", "mixed"
    ]
    signal_excess_over_control_noise_ms_per_second: FiniteFloat
    classification: Literal[
        "stable_candidate", "noise_limited", "inconsistent_direction"
    ]

    @model_validator(mode="after")
    def aggregate_is_recomputable(self) -> "ActorGpuImpactStudyAggregate":
        expected_trial_ids = ["trial_001", "trial_002", "trial_003"]
        if [item.trial_id for item in self.trial_measurements] != expected_trial_ids:
            raise ValueError("Actor GPU study aggregate requires all three trials")
        deltas = sorted(
            item.baseline_minus_hidden_ms_per_second
            for item in self.trial_measurements
        )
        visible_rates = sorted(
            item.visible_total_ms_per_second for item in self.trial_measurements
        )
        hidden_rates = sorted(
            item.hidden_total_ms_per_second for item in self.trial_measurements
        )
        median_delta = deltas[1]
        median_visible = visible_rates[1]
        median_hidden = hidden_rates[1]
        mad = sorted(abs(value - median_delta) for value in deltas)[1]
        noise = abs(
            self.control_first_total_ms_per_second
            - self.control_second_total_ms_per_second
        )
        if not all(
            (
                math.isclose(
                    self.control_noise_abs_ms_per_second, noise, abs_tol=0.001
                ),
                math.isclose(
                    self.median_visible_total_ms_per_second,
                    median_visible,
                    abs_tol=0.001,
                ),
                math.isclose(
                    self.median_hidden_total_ms_per_second,
                    median_hidden,
                    abs_tol=0.001,
                ),
                math.isclose(
                    self.median_baseline_minus_hidden_ms_per_second,
                    median_delta,
                    abs_tol=0.001,
                ),
                math.isclose(self.delta_mad_ms_per_second, mad, abs_tol=0.001),
                math.isclose(
                    self.relative_reduction_percent,
                    median_delta / median_visible * 100.0,
                    abs_tol=0.001,
                ),
                math.isclose(
                    self.signal_excess_over_control_noise_ms_per_second,
                    abs(median_delta) - noise,
                    abs_tol=0.001,
                ),
            )
        ):
            raise ValueError("Actor GPU study aggregate statistics are not recomputable")

        epsilon = 0.001
        decreased = sum(value > epsilon for value in deltas)
        increased = sum(value < -epsilon for value in deltas)
        unchanged = 3 - decreased - increased
        if (
            self.decreased_vote_count,
            self.increased_vote_count,
            self.unchanged_vote_count,
        ) != (decreased, increased, unchanged):
            raise ValueError("Actor GPU study direction votes are not recomputable")
        dominant = (
            "decreased"
            if decreased >= 2
            else "increased"
            if increased >= 2
            else "unchanged"
            if unchanged >= 2
            else "mixed"
        )
        classification = (
            "stable_candidate"
            if dominant in {"decreased", "increased"}
            and abs(median_delta) > noise + epsilon
            else "noise_limited"
            if abs(median_delta) <= noise + epsilon
            else "inconsistent_direction"
        )
        if self.dominant_direction_when_hidden != dominant:
            raise ValueError("Actor GPU study dominant direction is not recomputable")
        if self.classification != classification:
            raise ValueError("Actor GPU study classification is not recomputable")
        return self


def build_actor_gpu_impact_study_aggregates(
    capture_set: UnrealActorGpuImpactStudyCaptureSet,
) -> list[ActorGpuImpactStudyAggregate]:
    """Deterministically intersect all eight captures and compute study statistics."""

    chronological = [
        *capture_set.noise_control_visible,
        capture_set.trials[0].visible,
        capture_set.trials[0].hidden,
        capture_set.trials[1].hidden,
        capture_set.trials[1].visible,
        capture_set.trials[2].visible,
        capture_set.trials[2].hidden,
    ]

    def scope_map(
        capture: UnrealInsightsGpuCapture,
    ) -> dict[tuple[str, str], tuple[InsightsGpuScope, str, float]]:
        queue_names = {queue.queue_id: queue.display_name for queue in capture.queues}
        return {
            (scope.queue_id, scope.name): (
                scope,
                queue_names[scope.queue_id],
                scope.total_inclusive_ms / capture.captured_duration_seconds,
            )
            for scope in capture.scopes
        }

    maps = [scope_map(capture) for capture in chronological]
    common_keys = set(maps[0])
    for mapping in maps[1:]:
        common_keys &= set(mapping)

    provisional: list[ActorGpuImpactStudyAggregate] = []
    for queue_id, scope_name in sorted(common_keys):
        entries = [mapping[(queue_id, scope_name)] for mapping in maps]
        queue_names = {entry[1] for entry in entries}
        if len(queue_names) != 1:
            continue
        control_first, control_second = entries[0], entries[1]
        trial_entries = [
            (capture_set.trials[0], entries[2], entries[3]),
            (capture_set.trials[1], entries[5], entries[4]),
            (capture_set.trials[2], entries[6], entries[7]),
        ]
        measurements = [
            ActorGpuImpactStudyTrialMeasurement(
                trial_id=trial.trial_id,
                order=trial.order,
                visible_scope_id=visible[0].scope_id,
                hidden_scope_id=hidden[0].scope_id,
                visible_total_ms_per_second=visible[2],
                hidden_total_ms_per_second=hidden[2],
                baseline_minus_hidden_ms_per_second=visible[2] - hidden[2],
            )
            for trial, visible, hidden in trial_entries
        ]
        deltas = sorted(
            item.baseline_minus_hidden_ms_per_second for item in measurements
        )
        visible_rates = sorted(
            item.visible_total_ms_per_second for item in measurements
        )
        hidden_rates = sorted(
            item.hidden_total_ms_per_second for item in measurements
        )
        median_delta = deltas[1]
        median_visible = visible_rates[1]
        if median_visible <= 0.0:
            continue
        median_hidden = hidden_rates[1]
        mad = sorted(abs(value - median_delta) for value in deltas)[1]
        noise = abs(control_first[2] - control_second[2])
        epsilon = 0.001
        decreased = sum(value > epsilon for value in deltas)
        increased = sum(value < -epsilon for value in deltas)
        unchanged = 3 - decreased - increased
        dominant = (
            "decreased"
            if decreased >= 2
            else "increased"
            if increased >= 2
            else "unchanged"
            if unchanged >= 2
            else "mixed"
        )
        classification = (
            "stable_candidate"
            if dominant in {"decreased", "increased"}
            and abs(median_delta) > noise + epsilon
            else "noise_limited"
            if abs(median_delta) <= noise + epsilon
            else "inconsistent_direction"
        )
        provisional.append(
            ActorGpuImpactStudyAggregate(
                aggregate_id="aggregate_pending",
                queue_name=entries[0][1],
                scope_name=scope_name,
                control_scope_ids=[
                    control_first[0].scope_id,
                    control_second[0].scope_id,
                ],
                control_first_total_ms_per_second=control_first[2],
                control_second_total_ms_per_second=control_second[2],
                control_noise_abs_ms_per_second=noise,
                trial_measurements=measurements,
                median_visible_total_ms_per_second=median_visible,
                median_hidden_total_ms_per_second=median_hidden,
                median_baseline_minus_hidden_ms_per_second=median_delta,
                delta_mad_ms_per_second=mad,
                relative_reduction_percent=median_delta / median_visible * 100.0,
                decreased_vote_count=decreased,
                increased_vote_count=increased,
                unchanged_vote_count=unchanged,
                dominant_direction_when_hidden=dominant,
                signal_excess_over_control_noise_ms_per_second=(
                    abs(median_delta) - noise
                ),
                classification=classification,
            )
        )
    provisional.sort(
        key=lambda item: (
            -abs(item.median_baseline_minus_hidden_ms_per_second),
            item.queue_name.casefold(),
            item.scope_name.casefold(),
        )
    )
    return [
        item.model_copy(update={"aggregate_id": f"aggregate_{index:03d}"})
        for index, item in enumerate(provisional[:64])
    ]


class ActorGpuImpactStudyFindingIntent(StrictModel):
    severity: Literal["info", "warning", "critical"]
    category: Literal[
        "stable_candidate",
        "noise_limited",
        "inconsistent_direction",
        "measurement_risk",
        "next_experiment",
    ]
    evidence_aggregate_ids: list[Identifier] = Field(min_length=1, max_length=12)
    observation: LongText
    recommendation: LongText

    @model_validator(mode="after")
    def aggregate_ids_are_unique(self) -> "ActorGpuImpactStudyFindingIntent":
        if len(self.evidence_aggregate_ids) != len(
            set(self.evidence_aggregate_ids)
        ):
            raise ValueError("Actor GPU study finding aggregate IDs must be unique")
        return self


class ActorGpuImpactStudyReviewIntent(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    outcome: Literal["review_complete", "unresolved"]
    summary: LongText
    primary_aggregate_id: Identifier | None = None
    findings: list[ActorGpuImpactStudyFindingIntent] = Field(
        default_factory=list, max_length=16
    )
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def outcome_is_consistent(self) -> "ActorGpuImpactStudyReviewIntent":
        if self.outcome == "review_complete" and self.missing_capabilities:
            raise ValueError("complete Actor GPU studies cannot contain capability gaps")
        if self.outcome == "unresolved" and (
            self.primary_aggregate_id is not None
            or self.findings
            or not self.missing_capabilities
        ):
            raise ValueError("unresolved Actor GPU studies contain only capability gaps")
        return self


class AssistantActorGpuImpactStudyReport(StrictModel):
    """Read-only model review with deterministic statistics over eight captures."""

    schema_version: Literal["0.1"] = "0.1"
    report_id: Identifier
    status: Literal["review_complete", "unresolved"]
    request: LongText
    capture_set: UnrealActorGpuImpactStudyCaptureSet
    capture_set_sha256: Sha256
    capture_set_file_sha256: Sha256
    analyzed_by: ModelIdentity
    aggregates: list[ActorGpuImpactStudyAggregate] = Field(
        default_factory=list, max_length=64
    )
    summary: LongText
    primary_aggregate_id: Identifier | None = None
    findings: list[ActorGpuImpactStudyFindingIntent] = Field(
        default_factory=list, max_length=16
    )
    missing_capabilities: list[ShortText] = Field(default_factory=list, max_length=16)
    modifies_editor_scene: Literal[False] = False

    @model_validator(mode="after")
    def report_is_grounded(self) -> "AssistantActorGpuImpactStudyReport":
        expected_sha256 = hashlib.sha256(
            self.capture_set.model_dump_json().encode("utf-8")
        ).hexdigest()
        if self.capture_set_sha256 != expected_sha256:
            raise ValueError("Actor GPU study hash does not match its capture set")
        expected_aggregates = build_actor_gpu_impact_study_aggregates(
            self.capture_set
        )
        if self.aggregates != expected_aggregates:
            raise ValueError("Actor GPU study aggregates do not match raw captures")
        known_ids = {item.aggregate_id for item in self.aggregates}
        cited_ids = {
            aggregate_id
            for finding in self.findings
            for aggregate_id in finding.evidence_aggregate_ids
        }
        if (
            self.primary_aggregate_id is not None
            and self.primary_aggregate_id not in known_ids
        ):
            raise ValueError("primary Actor GPU study aggregate is unavailable")
        if not cited_ids.issubset(known_ids):
            raise ValueError("Actor GPU study findings cite unavailable aggregates")
        aggregate_classifications = {
            item.aggregate_id: item.classification for item in self.aggregates
        }
        for finding in self.findings:
            if finding.category not in {
                "stable_candidate",
                "noise_limited",
                "inconsistent_direction",
            }:
                continue
            cited_classifications = {
                aggregate_classifications[aggregate_id]
                for aggregate_id in finding.evidence_aggregate_ids
            }
            if finding.category not in cited_classifications:
                raise ValueError(
                    "Actor GPU study finding category contradicts its cited "
                    "aggregate classification"
                )
        if self.status == "review_complete" and (
            self.missing_capabilities or not self.aggregates
        ):
            raise ValueError("complete Actor GPU studies require measured aggregates")
        if self.status == "unresolved" and (
            self.primary_aggregate_id is not None
            or self.findings
            or not self.missing_capabilities
        ):
            raise ValueError("unresolved Actor GPU study reports contain only gaps")
        return self


class ShadowOptimizationTrial(StrictModel):
    """One counterbalanced baseline/candidate pair for Cast Shadow."""

    trial_id: Identifier
    order: Literal["baseline_then_candidate", "candidate_then_baseline"]
    baseline: UnrealInsightsGpuCapture
    candidate: UnrealInsightsGpuCapture

    @model_validator(mode="after")
    def order_matches_capture_time(self) -> "ShadowOptimizationTrial":
        if self.baseline.capture_id == self.candidate.capture_id:
            raise ValueError("shadow optimization trial captures must be distinct")
        baseline_first = self.baseline.captured_at_utc < self.candidate.captured_at_utc
        if baseline_first != (self.order == "baseline_then_candidate"):
            raise ValueError("shadow optimization trial order contradicts capture time")
        return self


class UnrealShadowOptimizationCaptureSet(StrictModel):
    """Eight traces for one temporary Cast Shadow true/false experiment."""

    schema_version: Literal["0.1"] = "0.1"
    experiment_id: Identifier
    created_at_utc: datetime
    request: LongText
    target: UnrealStaticMeshPerformanceContext
    runtime_actor_path: Annotated[str, Field(min_length=1, max_length=1000)]
    candidate_cast_shadow: Literal[False] = False
    noise_control_baseline: list[UnrealInsightsGpuCapture] = Field(
        min_length=2, max_length=2
    )
    trials: list[ShadowOptimizationTrial] = Field(min_length=3, max_length=3)
    requested_capture_duration_seconds: Annotated[
        float, Field(ge=5.0, le=5.0, allow_inf_nan=False)
    ] = 5.0
    warmup_seconds_per_capture: Annotated[
        float, Field(ge=1.0, le=1.0, allow_inf_nan=False)
    ] = 1.0
    schedule: Literal["AA_AB_BA_AB"] = "AA_AB_BA_AB"
    runtime_state_restored: Literal[True] = True
    modifies_editor_scene: Literal[False] = False

    @model_validator(mode="after")
    def capture_set_is_controlled(self) -> "UnrealShadowOptimizationCaptureSet":
        if not self.target.performance.cast_shadow:
            raise ValueError("shadow optimization requires Cast Shadow enabled at baseline")
        expected_trials = [
            ("trial_001", "baseline_then_candidate"),
            ("trial_002", "candidate_then_baseline"),
            ("trial_003", "baseline_then_candidate"),
        ]
        if [(item.trial_id, item.order) for item in self.trials] != expected_trials:
            raise ValueError("shadow optimization requires the AA_AB_BA_AB schedule")
        chronological = [
            *self.noise_control_baseline,
            self.trials[0].baseline,
            self.trials[0].candidate,
            self.trials[1].candidate,
            self.trials[1].baseline,
            self.trials[2].baseline,
            self.trials[2].candidate,
        ]
        capture_ids = [capture.capture_id for capture in chronological]
        trace_names = [capture.trace_file_name for capture in chronological]
        if len(capture_ids) != len(set(capture_ids)):
            raise ValueError("shadow optimization capture IDs must be unique")
        if len(trace_names) != len(set(trace_names)):
            raise ValueError("shadow optimization trace filenames must be unique")
        timestamps = [capture.captured_at_utc for capture in chronological]
        if timestamps != sorted(timestamps):
            raise ValueError("shadow optimization captures must preserve chronological time")
        reference = chronological[0]
        stable_fields = (
            "project_name",
            "world_path",
            "capture_mode",
            "viewport_width_px",
            "viewport_height_px",
            "gpu_name",
            "channels",
        )
        for capture in chronological:
            if any(
                getattr(capture, field) != getattr(reference, field)
                for field in stable_fields
            ):
                raise ValueError("shadow optimization captures require one fixed workload")
            if not math.isclose(
                capture.requested_duration_seconds,
                self.requested_capture_duration_seconds,
                abs_tol=0.001,
            ):
                raise ValueError("shadow optimization capture durations must match")
        if self.target.project_name != reference.project_name:
            raise ValueError("shadow optimization target and traces must share a project")
        return self


class ShadowOptimizationTrialMeasurement(StrictModel):
    trial_id: Identifier
    order: Literal["baseline_then_candidate", "candidate_then_baseline"]
    baseline_scope_id: Identifier
    candidate_scope_id: Identifier
    baseline_total_ms_per_second: NonNegativeFiniteFloat
    candidate_total_ms_per_second: NonNegativeFiniteFloat
    baseline_minus_candidate_ms_per_second: FiniteFloat

    @model_validator(mode="after")
    def delta_is_recomputable(self) -> "ShadowOptimizationTrialMeasurement":
        expected = (
            self.baseline_total_ms_per_second
            - self.candidate_total_ms_per_second
        )
        if not math.isclose(
            self.baseline_minus_candidate_ms_per_second,
            expected,
            abs_tol=0.001,
        ):
            raise ValueError("shadow optimization trial delta is not recomputable")
        return self


class ShadowOptimizationAggregate(StrictModel):
    aggregate_id: Identifier
    queue_name: ShortText
    scope_name: ShortText
    control_scope_ids: list[Identifier] = Field(min_length=2, max_length=2)
    control_first_total_ms_per_second: NonNegativeFiniteFloat
    control_second_total_ms_per_second: NonNegativeFiniteFloat
    control_noise_abs_ms_per_second: NonNegativeFiniteFloat
    trial_measurements: list[ShadowOptimizationTrialMeasurement] = Field(
        min_length=3, max_length=3
    )
    median_baseline_total_ms_per_second: NonNegativeFiniteFloat
    median_candidate_total_ms_per_second: NonNegativeFiniteFloat
    median_baseline_minus_candidate_ms_per_second: FiniteFloat
    delta_mad_ms_per_second: NonNegativeFiniteFloat
    relative_reduction_percent: Annotated[
        float, Field(ge=-1_000_000.0, le=100.0, allow_inf_nan=False)
    ]
    decreased_vote_count: Annotated[int, Field(ge=0, le=3)]
    increased_vote_count: Annotated[int, Field(ge=0, le=3)]
    unchanged_vote_count: Annotated[int, Field(ge=0, le=3)]
    dominant_direction_when_disabled: Literal[
        "decreased", "increased", "unchanged", "mixed"
    ]
    signal_excess_over_control_noise_ms_per_second: FiniteFloat
    classification: Literal[
        "stable_candidate", "noise_limited", "inconsistent_direction"
    ]

    @model_validator(mode="after")
    def aggregate_is_recomputable(self) -> "ShadowOptimizationAggregate":
        if [item.trial_id for item in self.trial_measurements] != [
            "trial_001",
            "trial_002",
            "trial_003",
        ]:
            raise ValueError("shadow optimization aggregate requires all three trials")
        deltas = sorted(
            item.baseline_minus_candidate_ms_per_second
            for item in self.trial_measurements
        )
        baseline_rates = sorted(
            item.baseline_total_ms_per_second for item in self.trial_measurements
        )
        candidate_rates = sorted(
            item.candidate_total_ms_per_second for item in self.trial_measurements
        )
        median_delta = deltas[1]
        median_baseline = baseline_rates[1]
        median_candidate = candidate_rates[1]
        mad = sorted(abs(value - median_delta) for value in deltas)[1]
        noise = abs(
            self.control_first_total_ms_per_second
            - self.control_second_total_ms_per_second
        )
        if not all(
            (
                math.isclose(self.control_noise_abs_ms_per_second, noise, abs_tol=0.001),
                math.isclose(
                    self.median_baseline_total_ms_per_second,
                    median_baseline,
                    abs_tol=0.001,
                ),
                math.isclose(
                    self.median_candidate_total_ms_per_second,
                    median_candidate,
                    abs_tol=0.001,
                ),
                math.isclose(
                    self.median_baseline_minus_candidate_ms_per_second,
                    median_delta,
                    abs_tol=0.001,
                ),
                math.isclose(self.delta_mad_ms_per_second, mad, abs_tol=0.001),
                math.isclose(
                    self.relative_reduction_percent,
                    median_delta / median_baseline * 100.0,
                    abs_tol=0.001,
                ),
                math.isclose(
                    self.signal_excess_over_control_noise_ms_per_second,
                    abs(median_delta) - noise,
                    abs_tol=0.001,
                ),
            )
        ):
            raise ValueError("shadow optimization aggregate is not recomputable")
        epsilon = 0.001
        decreased = sum(value > epsilon for value in deltas)
        increased = sum(value < -epsilon for value in deltas)
        unchanged = 3 - decreased - increased
        dominant = (
            "decreased"
            if decreased >= 2
            else "increased"
            if increased >= 2
            else "unchanged"
            if unchanged >= 2
            else "mixed"
        )
        classification = (
            "stable_candidate"
            if dominant in {"decreased", "increased"}
            and abs(median_delta) > noise + epsilon
            else "noise_limited"
            if abs(median_delta) <= noise + epsilon
            else "inconsistent_direction"
        )
        if (
            self.decreased_vote_count,
            self.increased_vote_count,
            self.unchanged_vote_count,
        ) != (decreased, increased, unchanged):
            raise ValueError("shadow optimization votes are not recomputable")
        if self.dominant_direction_when_disabled != dominant:
            raise ValueError("shadow optimization direction is not recomputable")
        if self.classification != classification:
            raise ValueError("shadow optimization classification is not recomputable")
        return self


def build_shadow_optimization_aggregates(
    capture_set: UnrealShadowOptimizationCaptureSet,
) -> list[ShadowOptimizationAggregate]:
    """Intersect all eight traces and compute deterministic shadow deltas."""

    chronological = [
        *capture_set.noise_control_baseline,
        capture_set.trials[0].baseline,
        capture_set.trials[0].candidate,
        capture_set.trials[1].candidate,
        capture_set.trials[1].baseline,
        capture_set.trials[2].baseline,
        capture_set.trials[2].candidate,
    ]

    def scope_map(
        capture: UnrealInsightsGpuCapture,
    ) -> dict[tuple[str, str], tuple[InsightsGpuScope, str, float]]:
        queue_names = {queue.queue_id: queue.display_name for queue in capture.queues}
        return {
            (scope.queue_id, scope.name): (
                scope,
                queue_names[scope.queue_id],
                scope.total_inclusive_ms / capture.captured_duration_seconds,
            )
            for scope in capture.scopes
        }

    maps = [scope_map(capture) for capture in chronological]
    common_keys = set(maps[0])
    for mapping in maps[1:]:
        common_keys &= set(mapping)
    provisional: list[ShadowOptimizationAggregate] = []
    for queue_id, scope_name in sorted(common_keys):
        entries = [mapping[(queue_id, scope_name)] for mapping in maps]
        queue_names = {entry[1] for entry in entries}
        if len(queue_names) != 1:
            continue
        control_first, control_second = entries[0], entries[1]
        trial_entries = [
            (capture_set.trials[0], entries[2], entries[3]),
            (capture_set.trials[1], entries[5], entries[4]),
            (capture_set.trials[2], entries[6], entries[7]),
        ]
        measurements = [
            ShadowOptimizationTrialMeasurement(
                trial_id=trial.trial_id,
                order=trial.order,
                baseline_scope_id=baseline[0].scope_id,
                candidate_scope_id=candidate[0].scope_id,
                baseline_total_ms_per_second=baseline[2],
                candidate_total_ms_per_second=candidate[2],
                baseline_minus_candidate_ms_per_second=baseline[2] - candidate[2],
            )
            for trial, baseline, candidate in trial_entries
        ]
        deltas = sorted(
            item.baseline_minus_candidate_ms_per_second for item in measurements
        )
        baseline_rates = sorted(
            item.baseline_total_ms_per_second for item in measurements
        )
        candidate_rates = sorted(
            item.candidate_total_ms_per_second for item in measurements
        )
        median_delta = deltas[1]
        median_baseline = baseline_rates[1]
        if median_baseline <= 0.0:
            continue
        median_candidate = candidate_rates[1]
        mad = sorted(abs(value - median_delta) for value in deltas)[1]
        noise = abs(control_first[2] - control_second[2])
        epsilon = 0.001
        decreased = sum(value > epsilon for value in deltas)
        increased = sum(value < -epsilon for value in deltas)
        unchanged = 3 - decreased - increased
        dominant = (
            "decreased"
            if decreased >= 2
            else "increased"
            if increased >= 2
            else "unchanged"
            if unchanged >= 2
            else "mixed"
        )
        classification = (
            "stable_candidate"
            if dominant in {"decreased", "increased"}
            and abs(median_delta) > noise + epsilon
            else "noise_limited"
            if abs(median_delta) <= noise + epsilon
            else "inconsistent_direction"
        )
        provisional.append(
            ShadowOptimizationAggregate(
                aggregate_id="aggregate_pending",
                queue_name=entries[0][1],
                scope_name=scope_name,
                control_scope_ids=[control_first[0].scope_id, control_second[0].scope_id],
                control_first_total_ms_per_second=control_first[2],
                control_second_total_ms_per_second=control_second[2],
                control_noise_abs_ms_per_second=noise,
                trial_measurements=measurements,
                median_baseline_total_ms_per_second=median_baseline,
                median_candidate_total_ms_per_second=median_candidate,
                median_baseline_minus_candidate_ms_per_second=median_delta,
                delta_mad_ms_per_second=mad,
                relative_reduction_percent=median_delta / median_baseline * 100.0,
                decreased_vote_count=decreased,
                increased_vote_count=increased,
                unchanged_vote_count=unchanged,
                dominant_direction_when_disabled=dominant,
                signal_excess_over_control_noise_ms_per_second=abs(median_delta) - noise,
                classification=classification,
            )
        )
    provisional.sort(
        key=lambda item: (
            -abs(item.median_baseline_minus_candidate_ms_per_second),
            item.queue_name.casefold(),
            item.scope_name.casefold(),
        )
    )
    return [
        item.model_copy(update={"aggregate_id": f"aggregate_{index:03d}"})
        for index, item in enumerate(provisional[:64])
    ]


class ShadowOptimizationDecisionThresholds(StrictModel):
    minimum_signal_excess_ms_per_second: Literal[1.0] = 1.0
    minimum_relative_reduction_percent: Literal[1.0] = 1.0
    maximum_mad_to_median_ratio: Literal[1.0] = 1.0
    minimum_decreased_votes: Literal[2] = 2


def shadow_optimization_qualifying_aggregates(
    aggregates: list[ShadowOptimizationAggregate],
    thresholds: ShadowOptimizationDecisionThresholds,
) -> list[ShadowOptimizationAggregate]:
    return [
        item
        for item in aggregates
        if item.classification == "stable_candidate"
        and item.dominant_direction_when_disabled == "decreased"
        and item.signal_excess_over_control_noise_ms_per_second
        >= thresholds.minimum_signal_excess_ms_per_second
        and item.relative_reduction_percent
        >= thresholds.minimum_relative_reduction_percent
        and item.decreased_vote_count >= thresholds.minimum_decreased_votes
        and item.delta_mad_ms_per_second
        <= abs(item.median_baseline_minus_candidate_ms_per_second)
        * thresholds.maximum_mad_to_median_ratio
    ]


class AssistantShadowOptimizationReport(StrictModel):
    """Deterministic decision and optional approval-gated Cast Shadow action."""

    schema_version: Literal["0.1"] = "0.1"
    report_id: Identifier
    status: Literal["proposed", "rejected", "unresolved"]
    decision: Literal["propose_apply", "reject", "unresolved"]
    capture_set: UnrealShadowOptimizationCaptureSet
    capture_set_sha256: Sha256
    capture_set_file_sha256: Sha256
    analysis_method: Literal["deterministic_host"] = "deterministic_host"
    thresholds: ShadowOptimizationDecisionThresholds = Field(
        default_factory=ShadowOptimizationDecisionThresholds
    )
    aggregates: list[ShadowOptimizationAggregate] = Field(
        default_factory=list, max_length=64
    )
    primary_aggregate_id: Identifier | None = None
    summary: LongText
    proposed_action: PerformanceActorAction | None = None
    rejection_reasons: list[ShortText] = Field(default_factory=list, max_length=8)
    runtime_state_restored: Literal[True] = True
    modifies_editor_scene: Literal[False] = False
    approval_required: bool
    auto_save: Literal[False] = False
    undo_supported: Literal[True] = True
    visual_evidence_role: Literal["operator_regression_review"] = (
        "operator_regression_review"
    )

    @model_validator(mode="after")
    def report_is_recomputable(self) -> "AssistantShadowOptimizationReport":
        expected_hash = hashlib.sha256(
            self.capture_set.model_dump_json().encode("utf-8")
        ).hexdigest()
        if self.capture_set_sha256 != expected_hash:
            raise ValueError("shadow optimization capture-set hash is invalid")
        expected_aggregates = build_shadow_optimization_aggregates(self.capture_set)
        if self.aggregates != expected_aggregates:
            raise ValueError("shadow optimization aggregates do not match raw captures")
        qualifying = shadow_optimization_qualifying_aggregates(
            expected_aggregates, self.thresholds
        )
        expected_status = (
            "unresolved"
            if not expected_aggregates
            else "proposed"
            if qualifying
            else "rejected"
        )
        expected_decision = {
            "unresolved": "unresolved",
            "proposed": "propose_apply",
            "rejected": "reject",
        }[expected_status]
        if self.status != expected_status or self.decision != expected_decision:
            raise ValueError("shadow optimization decision is not recomputable")
        expected_primary = qualifying[0].aggregate_id if qualifying else None
        if self.primary_aggregate_id != expected_primary:
            raise ValueError("shadow optimization primary aggregate is invalid")
        if self.approval_required != (expected_status == "proposed"):
            raise ValueError("shadow optimization approval gate is invalid")
        if expected_status == "proposed":
            if self.proposed_action is None or self.rejection_reasons:
                raise ValueError("proposed shadow optimization requires one action")
            expected_after = self.capture_set.target.performance.model_copy(
                update={"cast_shadow": False}
            )
            if (
                self.proposed_action.target != self.capture_set.target
                or self.proposed_action.before != self.capture_set.target.performance
                or self.proposed_action.after != expected_after
                or [change.property for change in self.proposed_action.changes]
                != ["cast_shadow"]
            ):
                raise ValueError("shadow optimization action is not evidence-bound")
        elif self.proposed_action is not None:
            raise ValueError("rejected shadow optimization cannot contain an action")
        if expected_status != "proposed" and not self.rejection_reasons:
            raise ValueError("non-proposed shadow optimization needs explicit reasons")
        return self


class LevelProfilingActorAttempt(StrictModel):
    """One deterministically selected static candidate and its measured outcome."""

    assessment_id: Identifier
    actor_path: Annotated[str, Field(min_length=1, max_length=1000)]
    actor_name: ShortText
    outcome: Literal["measured", "skipped"]
    study_report: AssistantActorGpuImpactStudyReport | None = None
    skip_reason: ShortText | None = None

    @model_validator(mode="after")
    def outcome_matches_payload(self) -> "LevelProfilingActorAttempt":
        if self.outcome == "measured":
            if self.study_report is None or self.skip_reason is not None:
                raise ValueError("measured profiling attempts require only a study report")
        elif self.study_report is not None or self.skip_reason is None:
            raise ValueError("skipped profiling attempts require only a skip reason")
        return self


class UnrealLevelProfilingRun(StrictModel):
    """Complete profiling-first evidence bundle for one whole-level run."""

    schema_version: Literal["0.1"] = "0.1"
    profiling_run_id: Identifier
    created_at_utc: datetime
    request: LongText
    max_actor_studies: Literal[2] = 2
    measurement_priority: Literal["profiling_first"] = "profiling_first"
    visual_evidence_role: Literal["auxiliary_regression_guard"] = (
        "auxiliary_regression_guard"
    )
    level_audit_report: AssistantLevelAuditReport
    runtime_performance_report: AssistantRuntimePerformanceReport
    gpu_scope_report: AssistantInsightsGpuReport
    actor_attempts: list[LevelProfilingActorAttempt] = Field(max_length=2)
    runtime_state_restored: Literal[True] = True
    modifies_editor_scene: Literal[False] = False

    @model_validator(mode="after")
    def evidence_bundle_is_consistent(self) -> "UnrealLevelProfilingRun":
        audit = self.level_audit_report
        runtime = self.runtime_performance_report.capture
        gpu = self.gpu_scope_report.capture
        if audit.status != "review_only" or audit.actions or audit.modifies_editor_scene:
            raise ValueError("profiling runs require a read-only whole-level audit")
        if runtime.sample_count != 120 or runtime.warmup_frames != 30:
            raise ValueError("profiling runs require the fixed 30 plus 120 frame capture")
        if not math.isclose(gpu.requested_duration_seconds, 5.0, abs_tol=0.001):
            raise ValueError("profiling runs require a five-second GPU scope trace")
        if (
            runtime.project_name != gpu.project_name
            or runtime.world_path != gpu.world_path
            or runtime.capture_mode != gpu.capture_mode
            or runtime.viewport_width_px != gpu.viewport_width_px
            or runtime.viewport_height_px != gpu.viewport_height_px
            or runtime.gpu_name != gpu.gpu_name
        ):
            raise ValueError("runtime and GPU captures must describe one fixed PIE/SIE workload")
        if audit.context.project_name != runtime.project_name:
            raise ValueError("static and runtime profiling evidence must share one project")

        eligible = [
            assessment
            for assessment in audit.assessments
            if assessment.recommended_next_test == "repeatable_actor_gpu_study"
        ][: self.max_actor_studies]
        expected_ids = [assessment.assessment_id for assessment in eligible]
        if [attempt.assessment_id for attempt in self.actor_attempts] != expected_ids:
            raise ValueError("Actor attempts must follow the deterministic Top-N audit order")

        actors_by_path = {actor.actor_path: actor for actor in audit.context.actors}
        assessments_by_id = {
            assessment.assessment_id: assessment for assessment in audit.assessments
        }
        for attempt in self.actor_attempts:
            assessment = assessments_by_id[attempt.assessment_id]
            actor = actors_by_path.get(assessment.actor_path)
            if (
                actor is None
                or attempt.actor_path != assessment.actor_path
                or attempt.actor_name != actor.actor_name
            ):
                raise ValueError("profiling Actor attempt does not match static audit evidence")
            if attempt.outcome == "skipped":
                continue
            assert attempt.study_report is not None
            report = attempt.study_report
            capture_set = report.capture_set
            if report.modifies_editor_scene or not capture_set.runtime_state_restored:
                raise ValueError("Actor GPU studies must be read-only and restore runtime state")
            if capture_set.target.editor_actor_path != attempt.actor_path:
                raise ValueError("Actor GPU study target does not match its audit candidate")
            captures = [
                *capture_set.noise_control_visible,
                *[
                    capture
                    for trial in capture_set.trials
                    for capture in (trial.visible, trial.hidden)
                ],
            ]
            if len(captures) != 8:
                raise ValueError("profiling Actor studies must preserve all eight captures")
            for capture in captures:
                if (
                    capture.project_name != runtime.project_name
                    or capture.world_path != runtime.world_path
                    or capture.capture_mode != runtime.capture_mode
                    or capture.viewport_width_px != runtime.viewport_width_px
                    or capture.viewport_height_px != runtime.viewport_height_px
                    or capture.gpu_name != runtime.gpu_name
                    or not math.isclose(
                        capture.requested_duration_seconds, 5.0, abs_tol=0.001
                    )
                ):
                    raise ValueError(
                        "Actor GPU study captures must match the fixed profiling workload"
                    )
        return self


class LevelProfilingActorFinding(StrictModel):
    """Deterministic strongest-scope result for one attempted Actor study."""

    assessment_id: Identifier
    actor_path: Annotated[str, Field(min_length=1, max_length=1000)]
    actor_name: ShortText
    static_risk_score: Annotated[int, Field(ge=0, le=100)]
    static_risk_band: Literal["high", "critical"]
    outcome: Literal[
        "stable_candidate", "noise_limited", "inconsistent_direction", "unresolved", "skipped"
    ]
    primary_aggregate_id: Identifier | None = None
    scope_name: ShortText | None = None
    median_delta_ms_per_second: FiniteFloat | None = None
    control_noise_ms_per_second: NonNegativeFiniteFloat | None = None
    signal_excess_ms_per_second: FiniteFloat | None = None
    relative_reduction_percent: FiniteFloat | None = None
    conclusion: LongText


class AssistantLevelProfilingReport(StrictModel):
    """Deterministic analysis of one complete profiling-first evidence bundle."""

    schema_version: Literal["0.1"] = "0.1"
    report_id: Identifier
    status: Literal["complete", "partial"]
    run: UnrealLevelProfilingRun
    run_sha256: Sha256
    level_audit_id: Identifier
    runtime_capture_id: Identifier
    gpu_capture_id: Identifier
    actor_study_ids: list[Identifier] = Field(max_length=2)
    summary: LongText
    primary_bottleneck: RuntimeBottleneck
    frame_p95_ms: NonNegativeFiniteFloat
    frame_budget_miss_fraction: UnitFloat
    gpu_p95_ms: NonNegativeFiniteFloat | None = None
    primary_gpu_scope_id: Identifier
    primary_gpu_scope_name: ShortText
    primary_gpu_scope_ms_per_second: NonNegativeFiniteFloat
    measured_actor_count: Annotated[int, Field(ge=0, le=2)]
    skipped_actor_count: Annotated[int, Field(ge=0, le=2)]
    stable_actor_candidate_count: Annotated[int, Field(ge=0, le=2)]
    actor_findings: list[LevelProfilingActorFinding] = Field(max_length=2)
    limitations: list[ShortText] = Field(min_length=1, max_length=16)
    runtime_state_restored: Literal[True] = True
    modifies_editor_scene: Literal[False] = False

    @model_validator(mode="after")
    def report_matches_run(self) -> "AssistantLevelProfilingReport":
        expected_sha256 = hashlib.sha256(
            self.run.model_dump_json().encode("utf-8")
        ).hexdigest()
        if self.run_sha256 != expected_sha256:
            raise ValueError("level profiling report run hash is invalid")
        if self.level_audit_id != self.run.level_audit_report.audit_id:
            raise ValueError("level profiling report audit ID is invalid")
        if self.runtime_capture_id != self.run.runtime_performance_report.capture.capture_id:
            raise ValueError("level profiling report runtime capture ID is invalid")
        if self.gpu_capture_id != self.run.gpu_scope_report.capture.capture_id:
            raise ValueError("level profiling report GPU capture ID is invalid")
        expected_studies = [
            attempt.study_report.capture_set.study_id
            for attempt in self.run.actor_attempts
            if attempt.study_report is not None
        ]
        if self.actor_study_ids != expected_studies:
            raise ValueError("level profiling report Actor study IDs are invalid")
        runtime_capture = self.run.runtime_performance_report.capture
        gpu_capture = self.run.gpu_scope_report.capture
        primary_scope = gpu_capture.scopes[0]
        expected_gpu_p95 = (
            runtime_capture.gpu.p95_ms if runtime_capture.gpu.available else None
        )
        if (
            self.primary_bottleneck
            != self.run.runtime_performance_report.primary_bottleneck
            or not math.isclose(
                self.frame_p95_ms, runtime_capture.frame_time.p95_ms, abs_tol=0.001
            )
            or self.gpu_p95_ms != expected_gpu_p95
            or self.primary_gpu_scope_id != primary_scope.scope_id
            or self.primary_gpu_scope_name != primary_scope.name
            or not math.isclose(
                self.primary_gpu_scope_ms_per_second,
                primary_scope.total_inclusive_ms
                / gpu_capture.captured_duration_seconds,
                abs_tol=0.001,
            )
        ):
            raise ValueError("level profiling summary metrics do not match raw captures")
        if len(self.actor_findings) != len(self.run.actor_attempts):
            raise ValueError("level profiling report must account for every Actor attempt")
        if self.measured_actor_count + self.skipped_actor_count != len(
            self.run.actor_attempts
        ):
            raise ValueError("level profiling Actor counts are invalid")
        if self.stable_actor_candidate_count != sum(
            finding.outcome == "stable_candidate" for finding in self.actor_findings
        ):
            raise ValueError("stable Actor candidate count is invalid")
        assessments = {
            item.assessment_id: item
            for item in self.run.level_audit_report.assessments
        }
        for attempt, finding in zip(
            self.run.actor_attempts, self.actor_findings, strict=True
        ):
            assessment = assessments[attempt.assessment_id]
            if (
                finding.assessment_id != attempt.assessment_id
                or finding.actor_path != attempt.actor_path
                or finding.actor_name != attempt.actor_name
                or finding.static_risk_score != assessment.risk_score
                or finding.static_risk_band != assessment.risk_band
            ):
                raise ValueError("level profiling Actor finding identity is invalid")
            if attempt.outcome == "skipped":
                if finding.outcome != "skipped" or any(
                    value is not None
                    for value in (
                        finding.primary_aggregate_id,
                        finding.scope_name,
                        finding.median_delta_ms_per_second,
                        finding.control_noise_ms_per_second,
                        finding.signal_excess_ms_per_second,
                        finding.relative_reduction_percent,
                    )
                ):
                    raise ValueError("skipped profiling finding contains measured evidence")
                continue
            assert attempt.study_report is not None
            if attempt.study_report.status == "unresolved":
                if finding.outcome != "unresolved" or finding.primary_aggregate_id is not None:
                    raise ValueError("unresolved profiling finding is invalid")
                continue
            primary = attempt.study_report.aggregates[0]
            if (
                finding.outcome != primary.classification
                or finding.primary_aggregate_id != primary.aggregate_id
                or finding.scope_name != primary.scope_name
                or finding.median_delta_ms_per_second is None
                or not math.isclose(
                    finding.median_delta_ms_per_second,
                    primary.median_baseline_minus_hidden_ms_per_second,
                    abs_tol=0.001,
                )
                or finding.control_noise_ms_per_second is None
                or not math.isclose(
                    finding.control_noise_ms_per_second,
                    primary.control_noise_abs_ms_per_second,
                    abs_tol=0.001,
                )
                or finding.signal_excess_ms_per_second is None
                or not math.isclose(
                    finding.signal_excess_ms_per_second,
                    primary.signal_excess_over_control_noise_ms_per_second,
                    abs_tol=0.001,
                )
                or finding.relative_reduction_percent is None
                or not math.isclose(
                    finding.relative_reduction_percent,
                    primary.relative_reduction_percent,
                    abs_tol=0.001,
                )
            ):
                raise ValueError("level profiling Actor finding does not match its study")
        expected_status = (
            "partial"
            if self.skipped_actor_count
            or self.run.runtime_performance_report.status == "unresolved"
            or self.run.gpu_scope_report.status == "unresolved"
            or any(
                attempt.study_report is not None
                and attempt.study_report.status == "unresolved"
                for attempt in self.run.actor_attempts
            )
            else "complete"
        )
        if self.status != expected_status:
            raise ValueError("level profiling status does not match evidence completeness")
        return self


class ShadowOptimizationVerificationThresholds(StrictModel):
    """Fixed whole-level regression guards after one approved shadow edit."""

    maximum_frame_p95_regression_ms: Literal[0.5] = 0.5
    maximum_frame_p95_regression_percent: Literal[2.0] = 2.0
    maximum_gpu_p95_regression_ms: Literal[0.5] = 0.5
    maximum_gpu_p95_regression_percent: Literal[2.0] = 2.0
    maximum_frame_budget_miss_increase_fraction: Literal[0.02] = 0.02
    maximum_target_scope_regression_ms_per_second: Literal[1.0] = 1.0
    maximum_target_scope_regression_percent: Literal[2.0] = 2.0


class ShadowOptimizationVerificationMetrics(StrictModel):
    """Host-recomputed before/after metrics for the fixed workload."""

    baseline_frame_p95_ms: NonNegativeFiniteFloat
    candidate_frame_p95_ms: NonNegativeFiniteFloat
    frame_p95_delta_ms: FiniteFloat
    frame_p95_delta_percent: FiniteFloat | None = None
    baseline_frame_budget_miss_fraction: UnitFloat
    candidate_frame_budget_miss_fraction: UnitFloat
    frame_budget_miss_delta_fraction: FiniteFloat
    baseline_gpu_p95_ms: NonNegativeFiniteFloat | None = None
    candidate_gpu_p95_ms: NonNegativeFiniteFloat | None = None
    gpu_p95_delta_ms: FiniteFloat | None = None
    gpu_p95_delta_percent: FiniteFloat | None = None
    target_scope_queue_name: ShortText
    target_scope_name: ShortText
    baseline_target_scope_ms_per_second: NonNegativeFiniteFloat | None = None
    candidate_target_scope_ms_per_second: NonNegativeFiniteFloat | None = None
    target_scope_delta_ms_per_second: FiniteFloat | None = None
    target_scope_delta_percent: FiniteFloat | None = None


def _verification_percent_delta(
    baseline: float,
    candidate: float,
) -> float | None:
    if math.isclose(baseline, 0.0, abs_tol=0.001):
        return None
    return (candidate - baseline) / baseline * 100.0


def _verification_scope_rate(
    report: AssistantLevelProfilingReport,
    queue_name: str,
    scope_name: str,
) -> float | None:
    capture = report.run.gpu_scope_report.capture
    queue_names = {queue.queue_id: queue.display_name for queue in capture.queues}
    for scope in capture.scopes:
        if (
            queue_names.get(scope.queue_id) == queue_name
            and scope.name == scope_name
        ):
            return scope.total_inclusive_ms / capture.captured_duration_seconds
    return None


def build_shadow_optimization_verification_metrics(
    shadow_report: AssistantShadowOptimizationReport,
    baseline_report: AssistantLevelProfilingReport,
    candidate_report: AssistantLevelProfilingReport,
) -> ShadowOptimizationVerificationMetrics:
    """Recompute all whole-level comparison values from embedded raw reports."""

    primary = next(
        (
            aggregate
            for aggregate in shadow_report.aggregates
            if aggregate.aggregate_id == shadow_report.primary_aggregate_id
        ),
        None,
    )
    if primary is None:
        queue_name = "unavailable"
        scope_name = "unavailable"
        baseline_scope = None
        candidate_scope = None
    else:
        queue_name = primary.queue_name
        scope_name = primary.scope_name
        baseline_scope = _verification_scope_rate(
            baseline_report, queue_name, scope_name
        )
        candidate_scope = _verification_scope_rate(
            candidate_report, queue_name, scope_name
        )
    comparable_scope = baseline_scope is not None and candidate_scope is not None
    baseline_gpu = baseline_report.gpu_p95_ms
    candidate_gpu = candidate_report.gpu_p95_ms
    comparable_gpu = baseline_gpu is not None and candidate_gpu is not None
    return ShadowOptimizationVerificationMetrics(
        baseline_frame_p95_ms=baseline_report.frame_p95_ms,
        candidate_frame_p95_ms=candidate_report.frame_p95_ms,
        frame_p95_delta_ms=(
            candidate_report.frame_p95_ms - baseline_report.frame_p95_ms
        ),
        frame_p95_delta_percent=_verification_percent_delta(
            baseline_report.frame_p95_ms, candidate_report.frame_p95_ms
        ),
        baseline_frame_budget_miss_fraction=(
            baseline_report.frame_budget_miss_fraction
        ),
        candidate_frame_budget_miss_fraction=(
            candidate_report.frame_budget_miss_fraction
        ),
        frame_budget_miss_delta_fraction=(
            candidate_report.frame_budget_miss_fraction
            - baseline_report.frame_budget_miss_fraction
        ),
        baseline_gpu_p95_ms=baseline_gpu if comparable_gpu else None,
        candidate_gpu_p95_ms=candidate_gpu if comparable_gpu else None,
        gpu_p95_delta_ms=(
            candidate_gpu - baseline_gpu if comparable_gpu else None
        ),
        gpu_p95_delta_percent=(
            _verification_percent_delta(baseline_gpu, candidate_gpu)
            if comparable_gpu
            else None
        ),
        target_scope_queue_name=queue_name,
        target_scope_name=scope_name,
        baseline_target_scope_ms_per_second=(
            baseline_scope if comparable_scope else None
        ),
        candidate_target_scope_ms_per_second=(
            candidate_scope if comparable_scope else None
        ),
        target_scope_delta_ms_per_second=(
            candidate_scope - baseline_scope if comparable_scope else None
        ),
        target_scope_delta_percent=(
            _verification_percent_delta(baseline_scope, candidate_scope)
            if comparable_scope
            else None
        ),
    )


def shadow_optimization_verification_workload_matches(
    shadow_report: AssistantShadowOptimizationReport,
    baseline_report: AssistantLevelProfilingReport,
    candidate_report: AssistantLevelProfilingReport,
) -> bool:
    """Require the original experiment and both full runs to share one workload."""

    baseline_runtime = baseline_report.run.runtime_performance_report.capture
    candidate_runtime = candidate_report.run.runtime_performance_report.capture
    baseline_gpu = baseline_report.run.gpu_scope_report.capture
    candidate_gpu = candidate_report.run.gpu_scope_report.capture
    shadow_capture = shadow_report.capture_set.noise_control_baseline[0]
    runtime_identity = (
        baseline_runtime.project_name,
        baseline_runtime.world_path,
        baseline_runtime.capture_mode,
        baseline_runtime.viewport_width_px,
        baseline_runtime.viewport_height_px,
        baseline_runtime.gpu_name,
    )
    candidate_identity = (
        candidate_runtime.project_name,
        candidate_runtime.world_path,
        candidate_runtime.capture_mode,
        candidate_runtime.viewport_width_px,
        candidate_runtime.viewport_height_px,
        candidate_runtime.gpu_name,
    )
    shadow_identity = (
        shadow_capture.project_name,
        shadow_capture.world_path,
        shadow_capture.capture_mode,
        shadow_capture.viewport_width_px,
        shadow_capture.viewport_height_px,
        shadow_capture.gpu_name,
    )
    return all(
        (
            runtime_identity == candidate_identity == shadow_identity,
            baseline_report.run.level_audit_report.context.level_path
            == candidate_report.run.level_audit_report.context.level_path,
            baseline_runtime.target_fps == candidate_runtime.target_fps,
            math.isclose(
                baseline_runtime.target_frame_ms,
                candidate_runtime.target_frame_ms,
                abs_tol=0.001,
            ),
            baseline_runtime.warmup_frames == candidate_runtime.warmup_frames == 30,
            baseline_runtime.sample_count == candidate_runtime.sample_count == 120,
            baseline_gpu.channels == candidate_gpu.channels == shadow_capture.channels,
            math.isclose(
                baseline_gpu.requested_duration_seconds,
                candidate_gpu.requested_duration_seconds,
                abs_tol=0.001,
            ),
            math.isclose(
                baseline_gpu.requested_duration_seconds,
                shadow_capture.requested_duration_seconds,
                abs_tol=0.001,
            ),
        )
    )


def shadow_optimization_verification_target_matches(
    shadow_report: AssistantShadowOptimizationReport,
    baseline_report: AssistantLevelProfilingReport,
    candidate_report: AssistantLevelProfilingReport,
) -> bool:
    """Prove the whole-level runs bracket the exact approved true-to-false edit."""

    action = shadow_report.proposed_action
    if (
        shadow_report.status != "proposed"
        or shadow_report.decision != "propose_apply"
        or action is None
        or action.before.cast_shadow is not True
        or action.after.cast_shadow is not False
        or action.before.max_draw_distance_cm != action.after.max_draw_distance_cm
        or [change.property for change in action.changes] != ["cast_shadow"]
    ):
        return False
    target_path = action.target.actor_path
    baseline_actor = next(
        (
            actor
            for actor in baseline_report.run.level_audit_report.context.actors
            if actor.actor_path == target_path
        ),
        None,
    )
    candidate_actor = next(
        (
            actor
            for actor in candidate_report.run.level_audit_report.context.actors
            if actor.actor_path == target_path
        ),
        None,
    )
    expected_candidate = action.target.model_copy(update={"performance": action.after})
    baseline_was_stable = any(
        finding.actor_path == target_path and finding.outcome == "stable_candidate"
        for finding in baseline_report.actor_findings
    )
    return (
        baseline_actor == action.target
        and candidate_actor == expected_candidate
        and baseline_was_stable
    )


def shadow_optimization_verification_sources_are_distinct(
    baseline_report: AssistantLevelProfilingReport,
    candidate_report: AssistantLevelProfilingReport,
    baseline_file_sha256: str,
    candidate_file_sha256: str,
) -> bool:
    """Reject a copied Before report masquerading as a post-apply recapture."""

    baseline = baseline_report.run
    candidate = candidate_report.run
    return all(
        (
            baseline_file_sha256 != candidate_file_sha256,
            baseline_report.report_id != candidate_report.report_id,
            baseline.profiling_run_id != candidate.profiling_run_id,
            baseline.level_audit_report.audit_id
            != candidate.level_audit_report.audit_id,
            baseline.runtime_performance_report.capture.capture_id
            != candidate.runtime_performance_report.capture.capture_id,
            baseline.gpu_scope_report.capture.capture_id
            != candidate.gpu_scope_report.capture.capture_id,
            baseline.gpu_scope_report.capture.trace_sha256
            != candidate.gpu_scope_report.capture.trace_sha256,
        )
    )


def _verification_regressed(
    baseline: float,
    candidate: float,
    absolute_limit: float,
    relative_limit_percent: float,
) -> bool:
    return candidate - baseline > max(
        absolute_limit,
        baseline * relative_limit_percent / 100.0,
    ) + 0.001


def build_shadow_optimization_verification_decision(
    *,
    metrics: ShadowOptimizationVerificationMetrics,
    thresholds: ShadowOptimizationVerificationThresholds,
    workload_matches: bool,
    target_transition_matches: bool,
    sources_are_distinct: bool,
) -> tuple[Literal["retain", "rollback", "unresolved"], list[str]]:
    """Apply the fixed whole-level guardrails to one recomputed comparison."""

    unresolved: list[str] = []
    if not sources_are_distinct:
        unresolved.append(
            "The post-apply run does not contain fresh, distinct profiling evidence."
        )
    if not workload_matches:
        unresolved.append(
            "Before, shadow A/B, and post-apply captures do not describe one fixed workload."
        )
    if not target_transition_matches:
        unresolved.append(
            "Whole-level evidence does not bracket the exact approved Cast Shadow true-to-false target transition."
        )
    if unresolved:
        return "unresolved", unresolved

    regressions: list[str] = []
    if _verification_regressed(
        metrics.baseline_frame_p95_ms,
        metrics.candidate_frame_p95_ms,
        thresholds.maximum_frame_p95_regression_ms,
        thresholds.maximum_frame_p95_regression_percent,
    ):
        regressions.append(
            f"Frame p95 regressed by {metrics.frame_p95_delta_ms:.3f} ms "
            "beyond the fixed absolute/relative guard."
        )
    if (
        metrics.frame_budget_miss_delta_fraction
        > thresholds.maximum_frame_budget_miss_increase_fraction + 0.0001
    ):
        regressions.append(
            "Frame-budget misses increased by "
            f"{metrics.frame_budget_miss_delta_fraction * 100.0:.2f} percentage points."
        )
    if (
        metrics.baseline_gpu_p95_ms is not None
        and metrics.candidate_gpu_p95_ms is not None
        and _verification_regressed(
            metrics.baseline_gpu_p95_ms,
            metrics.candidate_gpu_p95_ms,
            thresholds.maximum_gpu_p95_regression_ms,
            thresholds.maximum_gpu_p95_regression_percent,
        )
    ):
        assert metrics.gpu_p95_delta_ms is not None
        regressions.append(
            f"GPU p95 regressed by {metrics.gpu_p95_delta_ms:.3f} ms "
            "beyond the fixed absolute/relative guard."
        )
    if (
        metrics.baseline_target_scope_ms_per_second is not None
        and metrics.candidate_target_scope_ms_per_second is not None
        and _verification_regressed(
            metrics.baseline_target_scope_ms_per_second,
            metrics.candidate_target_scope_ms_per_second,
            thresholds.maximum_target_scope_regression_ms_per_second,
            thresholds.maximum_target_scope_regression_percent,
        )
    ):
        assert metrics.target_scope_delta_ms_per_second is not None
        regressions.append(
            f"The qualified GPU scope regressed by {metrics.target_scope_delta_ms_per_second:.3f} ms/s "
            "beyond the fixed absolute/relative guard."
        )
    if regressions:
        return "rollback", regressions

    reasons = [
        "No fixed whole-level regression guard was exceeded; the original controlled A/B result remains the candidate-specific performance evidence."
    ]
    if metrics.gpu_p95_delta_ms is None:
        reasons.append(
            "GPU p95 was unavailable in at least one runtime capture, so it was not used as a retain guard."
        )
    if metrics.target_scope_delta_ms_per_second is None:
        reasons.append(
            "The qualified shadow scope was not present in both whole-level Top-64 traces, so it was not used as a retain guard."
        )
    return "retain", reasons


class AssistantShadowOptimizationVerificationReport(StrictModel):
    """Recomputable post-apply retain/rollback recommendation."""

    schema_version: Literal["0.1"] = "0.1"
    report_id: Identifier
    status: Literal["complete", "unresolved"]
    decision: Literal["retain", "rollback", "unresolved"]
    shadow_optimization_report: AssistantShadowOptimizationReport
    shadow_optimization_report_sha256: Sha256
    shadow_optimization_report_file_sha256: Sha256
    baseline_level_profiling_report: AssistantLevelProfilingReport
    baseline_level_profiling_report_sha256: Sha256
    baseline_level_profiling_report_file_sha256: Sha256
    candidate_level_profiling_report: AssistantLevelProfilingReport
    candidate_level_profiling_report_sha256: Sha256
    candidate_level_profiling_report_file_sha256: Sha256
    analysis_method: Literal["deterministic_host"] = "deterministic_host"
    thresholds: ShadowOptimizationVerificationThresholds = Field(
        default_factory=ShadowOptimizationVerificationThresholds
    )
    workload_matches: bool
    target_transition_matches: bool
    sources_are_distinct: bool
    metrics: ShadowOptimizationVerificationMetrics
    summary: LongText
    decision_reasons: list[ShortText] = Field(min_length=1, max_length=8)
    formal_change_applied: Literal[True] = True
    modifies_editor_scene: Literal[False] = False
    retaining_requires_action: Literal[False] = False
    rollback_requires_approval: Literal[True] = True
    rollback_undo_supported: Literal[True] = True
    auto_save: Literal[False] = False
    visual_evidence_role: Literal["operator_regression_review"] = (
        "operator_regression_review"
    )

    @model_validator(mode="after")
    def verification_is_recomputable(self) -> "AssistantShadowOptimizationVerificationReport":
        source_pairs = (
            (
                self.shadow_optimization_report,
                self.shadow_optimization_report_sha256,
            ),
            (
                self.baseline_level_profiling_report,
                self.baseline_level_profiling_report_sha256,
            ),
            (
                self.candidate_level_profiling_report,
                self.candidate_level_profiling_report_sha256,
            ),
        )
        if any(
            hashlib.sha256(source.model_dump_json().encode("utf-8")).hexdigest()
            != observed_hash
            for source, observed_hash in source_pairs
        ):
            raise ValueError("shadow verification source report hash is invalid")
        expected_workload = shadow_optimization_verification_workload_matches(
            self.shadow_optimization_report,
            self.baseline_level_profiling_report,
            self.candidate_level_profiling_report,
        )
        expected_target = shadow_optimization_verification_target_matches(
            self.shadow_optimization_report,
            self.baseline_level_profiling_report,
            self.candidate_level_profiling_report,
        )
        expected_distinct = shadow_optimization_verification_sources_are_distinct(
            self.baseline_level_profiling_report,
            self.candidate_level_profiling_report,
            self.baseline_level_profiling_report_file_sha256,
            self.candidate_level_profiling_report_file_sha256,
        )
        expected_metrics = build_shadow_optimization_verification_metrics(
            self.shadow_optimization_report,
            self.baseline_level_profiling_report,
            self.candidate_level_profiling_report,
        )
        expected_decision, expected_reasons = (
            build_shadow_optimization_verification_decision(
                metrics=expected_metrics,
                thresholds=self.thresholds,
                workload_matches=expected_workload,
                target_transition_matches=expected_target,
                sources_are_distinct=expected_distinct,
            )
        )
        expected_status = "unresolved" if expected_decision == "unresolved" else "complete"
        if (
            self.workload_matches != expected_workload
            or self.target_transition_matches != expected_target
            or self.sources_are_distinct != expected_distinct
            or self.metrics != expected_metrics
            or self.decision != expected_decision
            or self.status != expected_status
            or self.decision_reasons != expected_reasons
        ):
            raise ValueError("shadow verification decision is not recomputable")
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
        "reference_quality_rejected",
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
            "reference_quality_rejected",
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


TrainingText = Annotated[str, Field(min_length=1, max_length=500_000)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class TrainingMessage(StrictModel):
    """One ordered chat message preserved for supervised fine-tuning."""

    role: Literal["system", "user", "assistant"]
    content: TrainingText


class TrainingArtifactReference(StrictModel):
    """Portable, hash-pinned evidence used to derive a training example."""

    role: Identifier
    path: RelativeArtifactPath
    sha256: Sha256


class TrainingSourceSkip(StrictModel):
    """A discovered workflow excluded by a deterministic quality rule."""

    manifest_path: RelativeArtifactPath
    reason: Literal[
        "invalid_manifest",
        "nonpassing_workflow",
        "missing_artifact",
        "hash_mismatch",
        "invalid_artifact",
        "duplicate_example",
        "campaign_not_eligible",
    ]
    detail: LongText


class TrainingExample(StrictModel):
    """Traceable SFT example derived from one validated RenderMasterBot workflow."""

    schema_version: Literal["0.1"] = "0.1"
    example_id: Identifier
    task_type: Literal["render_spec_planning"]
    source_group_id: Identifier
    source_manifest_path: RelativeArtifactPath
    source_manifest_sha256: Sha256
    source_artifacts: list[TrainingArtifactReference] = Field(min_length=3, max_length=16)
    messages: list[TrainingMessage] = Field(min_length=3, max_length=3)
    response_contract: Literal["render-spec"]
    response_sha256: Sha256
    example_sha256: Sha256

    @model_validator(mode="after")
    def training_example_is_consistent(self) -> "TrainingExample":
        if [message.role for message in self.messages] != [
            "system",
            "user",
            "assistant",
        ]:
            raise ValueError("training messages must be ordered system, user, assistant")
        artifact_keys = [(item.role, item.path) for item in self.source_artifacts]
        if len(artifact_keys) != len(set(artifact_keys)):
            raise ValueError("training source artifacts must be unique")
        try:
            response = RenderSpec.model_validate_json(self.messages[-1].content)
        except ValueError as exc:
            raise ValueError("assistant message is not a valid RenderSpec") from exc
        if canonical_sha256(response) != self.response_sha256:
            raise ValueError("training response_sha256 does not match the assistant message")
        payload = self.model_dump(mode="json", exclude={"example_sha256"})
        if canonical_sha256(payload) != self.example_sha256:
            raise ValueError("training example_sha256 does not match the example payload")
        return self


class TrainingDatasetSplit(StrictModel):
    """One workflow-grouped JSONL partition of a training dataset."""

    name: Literal["train", "validation", "test"]
    jsonl_path: RelativeArtifactPath
    sha256: Sha256
    example_count: NonNegativeInt
    example_ids: list[Identifier] = Field(default_factory=list, max_length=1_000_000)
    source_group_ids: list[Identifier] = Field(default_factory=list, max_length=1_000_000)
    task_counts: dict[Identifier, NonNegativeInt] = Field(default_factory=dict)

    @model_validator(mode="after")
    def split_counts_are_consistent(self) -> "TrainingDatasetSplit":
        if self.example_count != len(self.example_ids):
            raise ValueError("split example_count does not match example_ids")
        if len(self.example_ids) != len(set(self.example_ids)):
            raise ValueError("split example IDs must be unique")
        if len(self.source_group_ids) != len(set(self.source_group_ids)):
            raise ValueError("split source group IDs must be unique")
        if sum(self.task_counts.values()) != self.example_count:
            raise ValueError("split task_counts do not sum to example_count")
        return self


class TrainingDatasetCampaignSource(StrictModel):
    """A byte-for-byte campaign snapshot with its original relative location."""

    source_run_path: RelativeArtifactPath
    snapshot_path: RelativeArtifactPath
    sha256: Sha256


class TrainingDatasetManifest(StrictModel):
    """Deterministic identity and provenance for a private local SFT dataset."""

    schema_version: Literal["0.1"] = "0.1"
    builder_version: Literal["0.1", "0.2", "0.3"] = "0.1"
    dataset_id: Identifier
    split_strategy: Literal["group_sha256_v1"] = "group_sha256_v1"
    split_group_key: Literal["source_group_id"] = "source_group_id"
    split_seed: ShortText
    validation_group_fraction: UnitFloat = 0.1
    test_group_fraction: UnitFloat = 0.1
    campaign_run_path: RelativeArtifactPath | None = None
    campaign_run_sha256: Sha256 | None = None
    campaign_sources: list[TrainingDatasetCampaignSource] | None = Field(
        default=None, min_length=1, max_length=256,
    )
    discovered_source_manifest_count: NonNegativeInt
    accepted_source_manifests: list[TrainingArtifactReference] = Field(
        default_factory=list,
        max_length=1_000_000,
    )
    skipped_sources: list[TrainingSourceSkip] = Field(
        default_factory=list,
        max_length=1_000_000,
    )
    total_example_count: NonNegativeInt
    task_counts: dict[Identifier, NonNegativeInt] = Field(default_factory=dict)
    splits: list[TrainingDatasetSplit] = Field(min_length=3, max_length=3)
    dataset_sha256: Sha256

    @model_validator(mode="after")
    def dataset_manifest_is_consistent(self) -> "TrainingDatasetManifest":
        if (self.campaign_run_path is None) != (self.campaign_run_sha256 is None):
            raise ValueError("campaign run path and hash must be supplied together")
        if self.builder_version == "0.3":
            if not self.campaign_sources or self.campaign_run_path is not None:
                raise ValueError("builder 0.3 requires campaign snapshots without legacy provenance")
        elif self.campaign_sources is not None:
            raise ValueError("campaign snapshots require builder 0.3")
        if self.campaign_sources:
            for attribute in ("source_run_path", "snapshot_path"):
                paths = [getattr(item, attribute).casefold() for item in self.campaign_sources]
                if len(paths) != len(set(paths)):
                    raise ValueError(f"campaign {attribute} values must be unique")
        if [split.name for split in self.splits] != ["train", "validation", "test"]:
            raise ValueError("dataset splits must be ordered train, validation, test")
        if self.discovered_source_manifest_count != (
            len(self.accepted_source_manifests) + len(self.skipped_sources)
        ):
            raise ValueError("source manifest counts are inconsistent")
        if self.total_example_count != sum(split.example_count for split in self.splits):
            raise ValueError("dataset example count does not match splits")
        if sum(self.task_counts.values()) != self.total_example_count:
            raise ValueError("dataset task_counts do not sum to total_example_count")
        payload = self.model_dump(
            mode="json",
            exclude={"dataset_sha256"},
            exclude_none=True,
        )
        if canonical_sha256(payload) != self.dataset_sha256:
            raise ValueError("dataset_sha256 does not match the manifest payload")
        return self


class TrainingReadinessThresholds(StrictModel):
    qlora_smoke_train: NonNegativeInt = 20
    qlora_smoke_validation: NonNegativeInt = 2
    qlora_smoke_test: NonNegativeInt = 2
    product_train: NonNegativeInt = 500
    product_validation: NonNegativeInt = 50
    product_test: NonNegativeInt = 50


class TrainingDatasetAuditReport(StrictModel):
    """Integrity and readiness result for one generated training dataset."""

    schema_version: Literal["0.1"] = "0.1"
    dataset_id: Identifier
    manifest_sha256: Sha256
    dataset_sha256: Sha256
    source_evidence_checked: bool = False
    campaign_snapshot_count: NonNegativeInt = 0
    integrity_status: Literal["passed", "failed"]
    split_counts: dict[Literal["train", "validation", "test"], NonNegativeInt]
    valid_example_count: NonNegativeInt
    invalid_example_count: NonNegativeInt
    file_hash_error_count: NonNegativeInt
    manifest_membership_error_count: NonNegativeInt
    duplicate_example_count: NonNegativeInt
    cross_split_example_overlap_count: NonNegativeInt
    cross_split_group_overlap_count: NonNegativeInt
    thresholds: TrainingReadinessThresholds = Field(default_factory=TrainingReadinessThresholds)
    qlora_smoke_ready: bool
    product_finetune_ready: bool
    readiness_reasons: list[ShortText] = Field(default_factory=list, max_length=16)
    issues: list[LongText] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def audit_status_is_consistent(self) -> "TrainingDatasetAuditReport":
        integrity_errors = (
            self.invalid_example_count
            + self.file_hash_error_count
            + self.manifest_membership_error_count
            + self.duplicate_example_count
            + self.cross_split_example_overlap_count
            + self.cross_split_group_overlap_count
        )
        if (self.integrity_status == "passed") != (integrity_errors == 0):
            raise ValueError("audit integrity_status does not match integrity errors")
        split_counts = self.split_counts
        smoke_ready = self.integrity_status == "passed" and (
            split_counts.get("train", 0) >= self.thresholds.qlora_smoke_train
            and split_counts.get("validation", 0)
            >= self.thresholds.qlora_smoke_validation
            and split_counts.get("test", 0) >= self.thresholds.qlora_smoke_test
        )
        product_ready = self.integrity_status == "passed" and (
            split_counts.get("train", 0) >= self.thresholds.product_train
            and split_counts.get("validation", 0) >= self.thresholds.product_validation
            and split_counts.get("test", 0) >= self.thresholds.product_test
        )
        if self.qlora_smoke_ready != smoke_ready:
            raise ValueError("qlora_smoke_ready does not match thresholds")
        if self.product_finetune_ready != product_ready:
            raise ValueError("product_finetune_ready does not match thresholds")
        return self


TrainingCampaignCategory = Literal[
    "single_asset",
    "material_assignment",
    "multi_asset_composition",
    "camera_view",
    "lighting",
]
TrainingCampaignViewAxis = Literal[
    "auto-product",
    "from-positive-x",
    "from-negative-x",
    "from-positive-y",
    "from-negative-y",
    "from-positive-z",
    "from-negative-z",
]


class TrainingCampaignExpectation(StrictModel):
    """Host-owned success conditions for one automated corpus case."""

    required_scene_asset_ids: list[Identifier] = Field(min_length=1, max_length=16)
    required_material_asset_ids: list[Identifier] = Field(default_factory=list, max_length=16)
    forbidden_asset_ids: list[Identifier] = Field(default_factory=list, max_length=32)
    minimum_object_count: Annotated[int, Field(ge=1, le=32)] = 1
    maximum_object_count: Annotated[int, Field(ge=1, le=32)] = 1
    minimum_material_assignment_count: Annotated[int, Field(ge=0, le=64)] = 0
    maximum_material_assignment_count: Annotated[int, Field(ge=0, le=64)] = 0
    require_visual_pass: bool = True

    @model_validator(mode="after")
    def expectation_is_consistent(self) -> "TrainingCampaignExpectation":
        for label, values in (
            ("required scene assets", self.required_scene_asset_ids),
            ("required material assets", self.required_material_asset_ids),
            ("forbidden assets", self.forbidden_asset_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"campaign {label} must be unique")
        required = set(self.required_scene_asset_ids) | set(
            self.required_material_asset_ids
        )
        if required & set(self.forbidden_asset_ids):
            raise ValueError("campaign required and forbidden assets cannot overlap")
        if self.minimum_object_count > self.maximum_object_count:
            raise ValueError("campaign minimum object count exceeds maximum")
        if self.minimum_material_assignment_count > self.maximum_material_assignment_count:
            raise ValueError("campaign minimum material assignment count exceeds maximum")
        if self.required_material_asset_ids and self.minimum_material_assignment_count < 1:
            raise ValueError("required materials require at least one material assignment")
        return self


class TrainingCampaignCase(StrictModel):
    """One pre-authored graphics request and its deterministic acceptance boundary."""

    case_id: Identifier
    category: TrainingCampaignCategory
    prompt: LongText
    expectation: TrainingCampaignExpectation
    view_axis: TrainingCampaignViewAxis = "auto-product"
    margin_fraction: Annotated[float, Field(ge=0, lt=0.45, allow_inf_nan=False)] = 0.02
    retrieve_assets: Annotated[int, Field(ge=1, le=50)] = 8
    retrieve_materials: Annotated[int, Field(ge=0, le=50)] = 5
    max_iterations: Annotated[int, Field(ge=1, le=5)] = 3
    timeout_seconds: Annotated[int, Field(ge=60, le=3600)] = 600


class TrainingCampaignSuite(StrictModel):
    """Frozen request suite used for corpus collection and base-model evaluation."""

    schema_version: Literal["0.1"] = "0.1"
    suite_id: Identifier
    description: LongText
    project_name: ShortText
    project_descriptor_sha256: Sha256
    asset_catalog_sha256: Sha256
    split_seed: ShortText = "renderbot-stage0-v1"
    validation_group_fraction: UnitFloat = 0.1
    test_group_fraction: UnitFloat = 0.1
    cases: list[TrainingCampaignCase] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def suite_cases_are_unique(self) -> "TrainingCampaignSuite":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("training campaign case IDs must be unique")
        prompts = [case.prompt.strip().casefold() for case in self.cases]
        if len(prompts) != len(set(prompts)):
            raise ValueError("training campaign prompts must be unique")
        if self.validation_group_fraction + self.test_group_fraction >= 1:
            raise ValueError("campaign split fractions must leave room for training")
        return self


class TrainingReferenceCase(StrictModel):
    """One non-model-authored RenderSpec proposed as trusted planner supervision."""

    case_id: Identifier
    authoring_source: Literal["human_authored", "deterministic_compiler"]
    rationale: LongText
    retrieved_asset_ids: list[Identifier] = Field(min_length=1, max_length=50)
    render_spec: RenderSpec
    render_spec_sha256: Sha256

    @model_validator(mode="after")
    def reference_case_is_consistent(self) -> "TrainingReferenceCase":
        if len(self.retrieved_asset_ids) != len(set(self.retrieved_asset_ids)):
            raise ValueError("reference retrieved asset IDs must be unique")
        if self.render_spec.notes:
            raise ValueError("reference RenderSpecs must keep rationale outside notes")
        if canonical_sha256(self.render_spec) != self.render_spec_sha256:
            raise ValueError("reference RenderSpec SHA-256 does not match its payload")
        return self


class TrainingReferenceSuite(StrictModel):
    """Hash-bound Golden Reference inputs for a frozen training campaign suite."""

    schema_version: Literal["0.1"] = "0.1"
    reference_suite_id: Identifier
    campaign_suite_id: Identifier
    campaign_suite_sha256: Sha256
    authoring_policy: Literal["non_model_reference_v1"] = "non_model_reference_v1"
    cases: list[TrainingReferenceCase] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def reference_cases_are_unique(self) -> "TrainingReferenceSuite":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("training reference case IDs must be unique")
        return self


class TrainingReferenceCaseValidation(StrictModel):
    """Recomputable static readiness result for one Golden Reference case."""

    case_id: Identifier
    status: Literal["ready", "rejected"]
    render_spec_sha256: Sha256
    preflight_verdict: Literal["pass", "needs_review", "fail"]
    observed_scene_asset_ids: list[Identifier] = Field(default_factory=list, max_length=64)
    observed_material_asset_ids: list[Identifier] = Field(default_factory=list, max_length=64)
    observed_object_count: NonNegativeInt
    issue_codes: list[Identifier] = Field(default_factory=list, max_length=32)
    issues: list[LongText] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validation_status_is_consistent(self) -> "TrainingReferenceCaseValidation":
        if len(self.issue_codes) != len(self.issues):
            raise ValueError("reference validation issue codes and details must align")
        if len(self.issue_codes) != len(set(self.issue_codes)):
            raise ValueError("reference validation issue codes must be unique")
        if self.status != ("ready" if not self.issues else "rejected"):
            raise ValueError("reference validation status does not match its issues")
        for values in (
            self.observed_scene_asset_ids,
            self.observed_material_asset_ids,
        ):
            if len(values) != len(set(values)):
                raise ValueError("reference validation asset IDs must be unique")
        return self


class TrainingReferenceValidationReport(StrictModel):
    """Frozen static trust report produced before any Golden Reference is rendered."""

    schema_version: Literal["0.1"] = "0.1"
    reference_suite_id: Identifier
    reference_suite_sha256: Sha256
    campaign_suite_id: Identifier
    campaign_suite_sha256: Sha256
    project_descriptor_sha256: Sha256
    asset_catalog_sha256: Sha256
    integrity_status: Literal["passed", "failed"]
    total_campaign_case_count: NonNegativeInt
    reference_case_count: NonNegativeInt
    ready_case_count: NonNegativeInt
    rejected_case_count: NonNegativeInt
    missing_case_ids: list[Identifier] = Field(default_factory=list, max_length=256)
    full_coverage: bool
    ready_to_run: bool
    case_results: list[TrainingReferenceCaseValidation] = Field(
        min_length=1,
        max_length=256,
    )
    report_sha256: Sha256

    @model_validator(mode="after")
    def validation_report_is_consistent(self) -> "TrainingReferenceValidationReport":
        ready = sum(result.status == "ready" for result in self.case_results)
        rejected = len(self.case_results) - ready
        if (
            self.reference_case_count,
            self.ready_case_count,
            self.rejected_case_count,
        ) != (len(self.case_results), ready, rejected):
            raise ValueError("reference validation aggregate counts are inconsistent")
        passed = rejected == 0
        if (self.integrity_status == "passed") != passed:
            raise ValueError("reference validation integrity status is inconsistent")
        if self.ready_to_run != passed:
            raise ValueError("ready_to_run must match reference validation integrity")
        if self.full_coverage != (
            not self.missing_case_ids
            and self.reference_case_count == self.total_campaign_case_count
        ):
            raise ValueError("reference validation coverage fields are inconsistent")
        payload = self.model_dump(mode="json", exclude={"report_sha256"})
        if canonical_sha256(payload) != self.report_sha256:
            raise ValueError("reference validation report SHA-256 is invalid")
        return self


class TrainingCampaignAttempt(StrictModel):
    """One isolated end-to-end workflow attempt for a campaign case."""

    attempt: Annotated[int, Field(ge=1, le=3)]
    workflow_id: Identifier
    workflow_directory: RelativeArtifactPath
    started_at: datetime
    finished_at: datetime
    duration_seconds: NonNegativeFiniteFloat
    outcome: Literal["succeeded", "stopped", "failed", "interrupted"]
    stop_reason: ShortText | None = None
    workflow_manifest_path: RelativeArtifactPath | None = None
    workflow_manifest_sha256: Sha256 | None = None
    visual_verdict: Literal["pass", "needs_review", "fail"] | None = None
    observed_scene_asset_ids: list[Identifier] = Field(default_factory=list, max_length=64)
    observed_material_asset_ids: list[Identifier] = Field(default_factory=list, max_length=64)
    observed_object_count: NonNegativeInt = 0
    expectation_passed: bool
    expectation_failures: list[ShortText] = Field(default_factory=list, max_length=16)
    training_eligible: bool
    error: LongText | None = None

    @model_validator(mode="after")
    def attempt_is_consistent(self) -> "TrainingCampaignAttempt":
        if self.finished_at < self.started_at:
            raise ValueError("campaign attempt finished before it started")
        if (self.workflow_manifest_path is None) != (
            self.workflow_manifest_sha256 is None
        ):
            raise ValueError("campaign workflow manifest path and hash require each other")
        if self.outcome == "succeeded" and self.workflow_manifest_path is None:
            raise ValueError("successful campaign attempts require a workflow manifest")
        if self.workflow_manifest_path is None and self.error is None:
            raise ValueError("manifest-free campaign attempts require an error")
        if self.expectation_passed != (not self.expectation_failures):
            raise ValueError("campaign expectation result does not match failures")
        eligible = (
            self.outcome == "succeeded"
            and self.expectation_passed
            and self.visual_verdict == "pass"
        )
        if self.training_eligible != eligible:
            raise ValueError("campaign training eligibility is inconsistent")
        for values in (
            self.observed_scene_asset_ids,
            self.observed_material_asset_ids,
        ):
            if len(values) != len(set(values)):
                raise ValueError("campaign observed asset IDs must be unique")
        return self


class TrainingCampaignCaseResult(StrictModel):
    """Terminal state and bounded attempts for one selected campaign case."""

    case_id: Identifier
    category: TrainingCampaignCategory
    status: Literal["succeeded", "stopped", "failed", "interrupted"]
    attempts: list[TrainingCampaignAttempt] = Field(min_length=1, max_length=3)
    training_eligible: bool

    @model_validator(mode="after")
    def case_result_is_consistent(self) -> "TrainingCampaignCaseResult":
        numbers = [attempt.attempt for attempt in self.attempts]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("campaign attempts must be contiguous and one-based")
        if self.status != self.attempts[-1].outcome:
            raise ValueError("campaign case status must match its latest attempt")
        if self.training_eligible != self.attempts[-1].training_eligible:
            raise ValueError("campaign case eligibility must match its latest attempt")
        return self


class TrainingCampaignRun(StrictModel):
    """Resumable campaign state with immutable workflow attempt evidence."""

    schema_version: Literal["0.1"] = "0.1"
    campaign_id: Identifier
    suite_id: Identifier
    suite_sha256: Sha256
    project_name: ShortText
    project_descriptor_sha256: Sha256
    asset_catalog_sha256: Sha256
    reference_suite_sha256: Sha256 | None = None
    planner: ModelIdentity
    evaluator: ModelIdentity
    status: Literal["planned", "running", "completed", "cancelled", "failed"]
    selected_case_ids: list[Identifier] = Field(min_length=1, max_length=256)
    max_attempts_per_case: Annotated[int, Field(ge=1, le=3)] = 2
    started_at: datetime | None = None
    finished_at: datetime | None = None
    active_case_id: Identifier | None = None
    active_attempt: Annotated[int, Field(ge=1, le=3)] | None = None
    active_started_at: datetime | None = None
    case_results: list[TrainingCampaignCaseResult] = Field(
        default_factory=list,
        max_length=256,
    )
    errors: list[LongText] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def campaign_run_is_consistent(self) -> "TrainingCampaignRun":
        if self.reference_suite_sha256 is not None and self.planner.provider not in {
            "human",
            "local",
        }:
            raise ValueError("reference campaigns require a human or local planner identity")
        if len(self.selected_case_ids) != len(set(self.selected_case_ids)):
            raise ValueError("campaign selected case IDs must be unique")
        result_ids = [result.case_id for result in self.case_results]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("campaign case results must be unique")
        if any(case_id not in self.selected_case_ids for case_id in result_ids):
            raise ValueError("campaign result references an unselected case")
        expected_order = [case_id for case_id in self.selected_case_ids if case_id in result_ids]
        if result_ids != expected_order:
            raise ValueError("campaign case results must preserve selected case order")
        active_values = (
            self.active_case_id,
            self.active_attempt,
            self.active_started_at,
        )
        if any(value is None for value in active_values) != all(
            value is None for value in active_values
        ):
            raise ValueError("campaign active case fields must be supplied together")
        if self.active_case_id is not None:
            if self.status != "running":
                raise ValueError("only running campaigns can have an active case")
            if self.active_case_id not in self.selected_case_ids:
                raise ValueError("campaign active case was not selected")
        if self.status == "planned":
            if self.started_at is not None or self.finished_at is not None:
                raise ValueError("planned campaigns cannot have lifecycle timestamps")
        elif self.status == "running":
            if self.started_at is None or self.finished_at is not None:
                raise ValueError("running campaigns require only started_at")
        else:
            if self.started_at is None or self.finished_at is None:
                raise ValueError("terminal campaigns require start and finish timestamps")
            if self.active_case_id is not None:
                raise ValueError("terminal campaigns cannot have an active case")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("campaign finished before it started")
        if self.status == "completed" and result_ids != self.selected_case_ids:
            raise ValueError("completed campaigns require every selected case result")
        return self


class TrainingBaselineCaseOutcome(StrictModel):
    case_id: Identifier
    category: TrainingCampaignCategory
    status: Literal["succeeded", "stopped", "failed", "interrupted"]
    attempt_count: Annotated[int, Field(ge=1, le=3)]
    expectation_passed: bool
    visual_passed: bool
    training_eligible: bool
    primary_failure_reason: Literal[
        "none",
        "workflow_failed",
        "workflow_stopped",
        "workflow_interrupted",
        "expectation_failed",
        "visual_not_passed",
        "black_or_blank_preview",
        "invalid_correction_patch",
        "unsupported_exposure_correction",
        "ollama_empty_content",
        "dimensionless_asset_selected",
        "correction_unresolved",
        "max_iterations_reached",
        "reference_quality_rejected",
    ]

    @model_validator(mode="after")
    def baseline_outcome_is_consistent(self) -> "TrainingBaselineCaseOutcome":
        if self.training_eligible != (self.primary_failure_reason == "none"):
            raise ValueError("baseline failure reason does not match eligibility")
        return self


class TrainingBaselineCategoryMetrics(StrictModel):
    category: TrainingCampaignCategory
    case_count: NonNegativeInt
    workflow_succeeded_count: NonNegativeInt
    expectation_passed_count: NonNegativeInt
    training_eligible_count: NonNegativeInt

    @model_validator(mode="after")
    def category_counts_are_bounded(self) -> "TrainingBaselineCategoryMetrics":
        if any(
            value > self.case_count
            for value in (
                self.workflow_succeeded_count,
                self.expectation_passed_count,
                self.training_eligible_count,
            )
        ):
            raise ValueError("baseline category counts exceed case_count")
        return self


class TrainingBaselineReport(StrictModel):
    """Frozen base-model performance record produced before adapter training."""

    schema_version: Literal["0.1"] = "0.1"
    report_id: Identifier
    frozen: Literal[True] = True
    campaign_id: Identifier
    suite_id: Identifier
    campaign_run_sha256: Sha256
    planner: ModelIdentity
    evaluator: ModelIdentity
    case_outcomes: list[TrainingBaselineCaseOutcome] = Field(min_length=1, max_length=256)
    category_metrics: list[TrainingBaselineCategoryMetrics] = Field(
        min_length=1,
        max_length=16,
    )
    total_case_count: NonNegativeInt
    workflow_succeeded_count: NonNegativeInt
    expectation_passed_count: NonNegativeInt
    training_eligible_count: NonNegativeInt
    workflow_success_rate: UnitFloat
    expectation_pass_rate: UnitFloat
    training_eligibility_rate: UnitFloat
    failure_reason_counts: dict[Identifier, NonNegativeInt] = Field(default_factory=dict)
    report_sha256: Sha256

    @model_validator(mode="after")
    def baseline_report_is_consistent(self) -> "TrainingBaselineReport":
        total = len(self.case_outcomes)
        succeeded = sum(outcome.status == "succeeded" for outcome in self.case_outcomes)
        expected = sum(outcome.expectation_passed for outcome in self.case_outcomes)
        eligible = sum(outcome.training_eligible for outcome in self.case_outcomes)
        if (
            self.total_case_count,
            self.workflow_succeeded_count,
            self.expectation_passed_count,
            self.training_eligible_count,
        ) != (total, succeeded, expected, eligible):
            raise ValueError("baseline aggregate counts do not match case outcomes")
        for actual, expected_rate in (
            (self.workflow_success_rate, succeeded / total),
            (self.expectation_pass_rate, expected / total),
            (self.training_eligibility_rate, eligible / total),
        ):
            if not math.isclose(actual, expected_rate, rel_tol=0, abs_tol=1e-12):
                raise ValueError("baseline rate does not match case outcomes")
        categories = [metric.category for metric in self.category_metrics]
        if len(categories) != len(set(categories)):
            raise ValueError("baseline category metrics must be unique")
        for metric in self.category_metrics:
            matching = [
                outcome
                for outcome in self.case_outcomes
                if outcome.category == metric.category
            ]
            if (
                metric.case_count,
                metric.workflow_succeeded_count,
                metric.expectation_passed_count,
                metric.training_eligible_count,
            ) != (
                len(matching),
                sum(outcome.status == "succeeded" for outcome in matching),
                sum(outcome.expectation_passed for outcome in matching),
                sum(outcome.training_eligible for outcome in matching),
            ):
                raise ValueError("baseline category metrics do not match outcomes")
        failure_counts = {
            reason: count
            for reason, count in self.failure_reason_counts.items()
            if count
        }
        expected_failures: dict[str, int] = {}
        for outcome in self.case_outcomes:
            if outcome.primary_failure_reason != "none":
                expected_failures[outcome.primary_failure_reason] = (
                    expected_failures.get(outcome.primary_failure_reason, 0) + 1
                )
        if failure_counts != expected_failures:
            raise ValueError("baseline failure counts do not match outcomes")
        payload = self.model_dump(mode="json", exclude={"report_sha256"})
        if canonical_sha256(payload) != self.report_sha256:
            raise ValueError("baseline report_sha256 does not match report payload")
        return self


ConversationIntentName = Literal[
    "answer_question",
    "inspect_context",
    "material_project",
    "material_external",
    "transform",
    "light",
    "lighting_rig",
    "lighting_rig_review",
    "camera",
    "performance_selection",
    "level_audit",
    "runtime_performance",
    "gpu_scope",
    "actor_gpu_impact",
    "level_profiling",
    "shadow_optimization",
    "shadow_verification",
    "render_evaluate",
    "clarify",
    "unsupported",
]

ConversationActionIntentName = Literal[
    "material_project",
    "material_external",
    "transform",
    "light",
    "lighting_rig",
    "lighting_rig_review",
    "camera",
    "performance_selection",
    "level_audit",
    "runtime_performance",
    "gpu_scope",
    "actor_gpu_impact",
    "level_profiling",
    "shadow_optimization",
    "shadow_verification",
    "render_evaluate",
]

ConversationRequiredContext = Literal[
    "actor_selection",
    "static_mesh_selection",
    "light_selection",
    "camera_selection",
    "lighting_rig_selection",
    "active_pie_or_sie",
    "applied_lighting_rig",
    "completed_level_profile",
    "applied_shadow_optimization",
    "pending_action_decision",
    "more_specific_request",
]


class ConversationSelectionItem(StrictModel):
    """One live Unreal Actor exposed to the conversational router."""

    actor_name: ShortText
    actor_path: Annotated[str, Field(min_length=1, max_length=1000)]
    actor_class: ShortText
    actor_kind: Literal["static_mesh", "light", "camera", "other"]


class UnrealConversationContext(StrictModel):
    """Bounded read-only Editor context for one conversational turn."""

    schema_version: Literal["0.1"] = "0.1"
    project_name: ShortText
    level_path: Annotated[str, Field(min_length=1, max_length=1000)]
    play_mode: Literal["editor", "pie", "sie"] = "editor"
    selection: list[ConversationSelectionItem] = Field(default_factory=list, max_length=32)
    selection_truncated: bool = False
    lighting_rig_review_ready: bool = False
    shadow_optimization_ready: bool = False
    shadow_verification_ready: bool = False
    pending_action_intent: ConversationActionIntentName | None = None

    @model_validator(mode="after")
    def selection_is_unique(self) -> "UnrealConversationContext":
        paths = [item.actor_path for item in self.selection]
        if len(paths) != len(set(paths)):
            raise ValueError("conversation context cannot repeat an Actor path")
        if self.selection_truncated and len(self.selection) != 32:
            raise ValueError("truncated conversation selection must preserve the first 32 Actors")
        return self


class AssistantConversationMessage(StrictModel):
    """A durable user or assistant message in one local Editor session."""

    message_id: Identifier
    role: Literal["user", "assistant"]
    content: Annotated[str, Field(min_length=1, max_length=8000)]


class AssistantToolEvidenceReference(StrictModel):
    """Immutable file evidence produced by one bounded assistant capability."""

    evidence_id: Identifier
    kind: Literal[
        "proposal",
        "report",
        "capture",
        "render",
        "trace",
        "log",
        "other",
    ]
    path: Annotated[str, Field(min_length=1, max_length=1000)]
    sha256: Sha256


class AssistantToolResultEvent(StrictModel):
    """Host-authored capability result supplied back to the conversation router."""

    schema_version: Literal["0.1"] = "0.1"
    event_id: Identifier
    conversation_id: Identifier
    source_turn_id: Identifier | None = None
    intent: ConversationActionIntentName
    phase: Literal[
        "proposal",
        "analysis",
        "execution",
        "verification",
        "rejection",
        "failure",
        "cancellation",
    ]
    status: Literal[
        "needs_review",
        "succeeded",
        "applied",
        "rejected",
        "failed",
        "cancelled",
        "unresolved",
    ]
    summary: LongText
    proposal_id: Identifier | None = None
    report_id: Identifier | None = None
    subject_ids: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list,
        max_length=32,
    )
    evidence: list[AssistantToolEvidenceReference] = Field(
        default_factory=list,
        max_length=16,
    )
    created_at_utc: datetime

    @model_validator(mode="after")
    def phase_matches_status_and_identity(self) -> "AssistantToolResultEvent":
        allowed_statuses = {
            "proposal": {"needs_review", "unresolved", "failed"},
            "analysis": {"succeeded", "unresolved", "failed"},
            "execution": {"applied", "failed"},
            "verification": {"succeeded", "unresolved", "failed"},
            "rejection": {"rejected"},
            "failure": {"failed"},
            "cancellation": {"cancelled"},
        }
        if self.status not in allowed_statuses[self.phase]:
            raise ValueError(
                f"tool-result phase {self.phase!r} cannot use status {self.status!r}"
            )
        if self.status in {"needs_review", "applied", "rejected"}:
            if self.proposal_id is None:
                raise ValueError(
                    f"tool-result status {self.status!r} requires proposal_id"
                )
        subject_ids = self.subject_ids
        if len(subject_ids) != len(set(subject_ids)):
            raise ValueError("tool-result subject IDs must be unique")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("tool-result evidence IDs must be unique")
        evidence_paths = [item.path for item in self.evidence]
        if len(evidence_paths) != len(set(evidence_paths)):
            raise ValueError("tool-result evidence paths must be unique")
        if (
            self.created_at_utc.tzinfo is None
            or self.created_at_utc.utcoffset() is None
            or self.created_at_utc.utcoffset().total_seconds() != 0
        ):
            raise ValueError("tool-result created_at_utc must use an explicit UTC timezone")
        return self


class AssistantConversationRequest(StrictModel):
    """Complete bounded input for one local-model conversation turn."""

    schema_version: Literal["0.1"] = "0.1"
    conversation_id: Identifier
    turn_id: Identifier
    request: Annotated[str, Field(min_length=1, max_length=8000)]
    context: UnrealConversationContext
    history: list[AssistantConversationMessage] = Field(
        default_factory=list,
        max_length=24,
    )
    result_events: list[AssistantToolResultEvent] = Field(
        default_factory=list,
        max_length=12,
    )

    @model_validator(mode="after")
    def history_is_ordered_and_does_not_duplicate_request(
        self,
    ) -> "AssistantConversationRequest":
        ids = [message.message_id for message in self.history]
        if len(ids) != len(set(ids)):
            raise ValueError("conversation history message IDs must be unique")
        if self.history and self.history[-1].role == "user":
            if self.history[-1].content.strip() == self.request.strip():
                raise ValueError("current request must not be duplicated in history")
        event_ids = [event.event_id for event in self.result_events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("conversation tool-result event IDs must be unique")
        if any(
            event.conversation_id != self.conversation_id
            for event in self.result_events
        ):
            raise ValueError(
                "conversation tool-result events must match the request conversation ID"
            )
        timestamps = [event.created_at_utc for event in self.result_events]
        if timestamps != sorted(timestamps):
            raise ValueError("conversation tool-result events must be chronological")
        return self


class AssistantConversationTaskState(StrictModel):
    """Only the bounded resumable state of one conversationally routed task."""

    source_turn_id: Identifier
    intent: ConversationActionIntentName
    status: Literal["planning", "needs_review"]
    proposal_id: Identifier | None = None
    proposal_evidence: AssistantToolEvidenceReference | None = None

    @model_validator(mode="after")
    def status_matches_resumable_identity(self) -> "AssistantConversationTaskState":
        if self.status == "needs_review":
            if self.proposal_id is None or self.proposal_evidence is None:
                raise ValueError(
                    "reviewable conversation tasks require proposal identity and evidence"
                )
            if self.proposal_evidence.kind != "proposal":
                raise ValueError("reviewable conversation task evidence must be a proposal")
        elif self.proposal_id is not None or self.proposal_evidence is not None:
            raise ValueError("planning conversation tasks cannot claim proposal evidence")
        return self


class AssistantConversationSession(StrictModel):
    """Mutable local Editor session state, distinct from immutable run evidence."""

    schema_version: Literal["0.1"] = "0.1"
    conversation_id: Identifier
    project_name: ShortText
    history: list[AssistantConversationMessage] = Field(
        default_factory=list,
        max_length=100,
    )
    result_events: list[AssistantToolResultEvent] = Field(
        default_factory=list,
        max_length=100,
    )
    active_task: AssistantConversationTaskState | None = None
    updated_at_utc: datetime

    @model_validator(mode="after")
    def session_history_and_task_are_consistent(self) -> "AssistantConversationSession":
        message_ids = [message.message_id for message in self.history]
        if len(message_ids) != len(set(message_ids)):
            raise ValueError("conversation session message IDs must be unique")
        event_ids = [event.event_id for event in self.result_events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("conversation session result-event IDs must be unique")
        if any(
            event.conversation_id != self.conversation_id
            for event in self.result_events
        ):
            raise ValueError("conversation session result events must match its ID")
        timestamps = [event.created_at_utc for event in self.result_events]
        if timestamps != sorted(timestamps):
            raise ValueError("conversation session result events must be chronological")
        if (
            self.updated_at_utc.tzinfo is None
            or self.updated_at_utc.utcoffset() is None
            or self.updated_at_utc.utcoffset().total_seconds() != 0
        ):
            raise ValueError("conversation session updated_at_utc must use UTC")
        if timestamps and self.updated_at_utc < timestamps[-1]:
            raise ValueError("conversation session cannot predate its latest result event")
        if self.active_task is not None and self.active_task.status == "needs_review":
            task = self.active_task
            matching_events = [
                event
                for event in self.result_events
                if event.source_turn_id == task.source_turn_id
                and event.intent == task.intent
                and event.phase == "proposal"
                and event.status == "needs_review"
                and event.proposal_id == task.proposal_id
            ]
            if not matching_events:
                raise ValueError(
                    "reviewable conversation task requires its proposal result event"
                )
            evidence = task.proposal_evidence
            if evidence not in matching_events[-1].evidence:
                raise ValueError(
                    "reviewable conversation task evidence must match its result event"
                )
        return self


class ConversationRouteIntent(StrictModel):
    """Model-generated decision before host-side context enforcement."""

    schema_version: Literal["0.1"] = "0.1"
    outcome: Literal["respond", "clarify", "route"]
    intent: ConversationIntentName
    assistant_message: Annotated[str, Field(min_length=1, max_length=4000)]
    action_prompt: Annotated[str, Field(min_length=1, max_length=8000)] | None
    clarification_question: Annotated[
        str,
        Field(min_length=1, max_length=1000),
    ] | None
    required_context: list[ConversationRequiredContext] = Field(max_length=10)

    @model_validator(mode="after")
    def outcome_matches_payload(self) -> "ConversationRouteIntent":
        if len(self.required_context) != len(set(self.required_context)):
            raise ValueError("required conversation context values must be unique")
        action_intents = {
            "material_project",
            "material_external",
            "transform",
            "light",
            "lighting_rig",
            "lighting_rig_review",
            "camera",
            "performance_selection",
            "level_audit",
            "runtime_performance",
            "gpu_scope",
            "actor_gpu_impact",
            "level_profiling",
            "shadow_optimization",
            "shadow_verification",
            "render_evaluate",
        }
        if self.outcome == "route":
            if self.intent not in action_intents:
                raise ValueError("route outcomes require a registered action intent")
            if self.action_prompt is None:
                raise ValueError("route outcomes require action_prompt")
            if self.clarification_question is not None or self.required_context:
                raise ValueError("route outcomes cannot request more context")
        elif self.outcome == "clarify":
            if self.intent in {"answer_question", "inspect_context", "unsupported"}:
                raise ValueError(
                    "clarify outcomes require clarify or a registered action intent"
                )
            if self.clarification_question is None or not self.required_context:
                raise ValueError(
                    "clarify outcomes require a question and required_context"
                )
            if self.action_prompt is not None:
                raise ValueError("clarify outcomes cannot include action_prompt")
        else:
            if self.intent not in {"answer_question", "inspect_context", "unsupported"}:
                raise ValueError("respond outcomes require a non-executable intent")
            if self.action_prompt is not None or self.clarification_question is not None:
                raise ValueError("respond outcomes cannot include an action or question")
            if self.required_context:
                raise ValueError("respond outcomes cannot request more context")
        return self


class AssistantConversationTurn(StrictModel):
    """Auditable host-validated result returned to the Unreal Editor panel."""

    schema_version: Literal["0.1"] = "0.1"
    conversation_id: Identifier
    turn_id: Identifier
    request: Annotated[str, Field(min_length=1, max_length=8000)]
    outcome: Literal["respond", "clarify", "route"]
    intent: ConversationIntentName
    assistant_message: Annotated[str, Field(min_length=1, max_length=4000)]
    action_prompt: Annotated[str, Field(min_length=1, max_length=8000)] | None = None
    clarification_question: Annotated[
        str,
        Field(min_length=1, max_length=1000),
    ] | None = None
    required_context: list[ConversationRequiredContext] = Field(
        default_factory=list,
        max_length=10,
    )
    context_sha256: Sha256
    model: ShortText
    attempt_count: Literal[1, 2]
    recovery_reason: LongText | None = None

    @model_validator(mode="after")
    def turn_payload_is_a_valid_route(self) -> "AssistantConversationTurn":
        ConversationRouteIntent.model_validate({
            "outcome": self.outcome,
            "intent": self.intent,
            "assistant_message": self.assistant_message,
            "action_prompt": self.action_prompt,
            "clarification_question": self.clarification_question,
            "required_context": self.required_context,
        })
        if self.attempt_count == 1 and self.recovery_reason is not None:
            raise ValueError("first-attempt conversation turns cannot have recovery_reason")
        if self.attempt_count == 2 and self.recovery_reason is None:
            raise ValueError("recovered conversation turns require recovery_reason")
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
    "unreal-level-audit-context": UnrealLevelAuditContext,
    "assistant-level-audit-report": AssistantLevelAuditReport,
    "unreal-runtime-performance-capture": UnrealRuntimePerformanceCapture,
    "assistant-runtime-performance-report": AssistantRuntimePerformanceReport,
    "unreal-insights-gpu-capture": UnrealInsightsGpuCapture,
    "assistant-insights-gpu-report": AssistantInsightsGpuReport,
    "unreal-actor-gpu-impact-experiment": UnrealActorGpuImpactExperiment,
    "assistant-actor-gpu-impact-report": AssistantActorGpuImpactReport,
    "unreal-actor-gpu-impact-study-capture-set": UnrealActorGpuImpactStudyCaptureSet,
    "assistant-actor-gpu-impact-study-report": AssistantActorGpuImpactStudyReport,
    "unreal-shadow-optimization-capture-set": UnrealShadowOptimizationCaptureSet,
    "assistant-shadow-optimization-report": AssistantShadowOptimizationReport,
    "assistant-shadow-optimization-verification-report": AssistantShadowOptimizationVerificationReport,
    "unreal-level-profiling-run": UnrealLevelProfilingRun,
    "assistant-level-profiling-report": AssistantLevelProfilingReport,
    "unreal-conversation-context": UnrealConversationContext,
    "assistant-tool-result-event": AssistantToolResultEvent,
    "assistant-conversation-request": AssistantConversationRequest,
    "assistant-conversation-session": AssistantConversationSession,
    "assistant-conversation-turn": AssistantConversationTurn,
    "render-spec-patch": RenderSpecPatch,
    "correction-decision": CorrectionDecision,
    "evaluation-report": EvaluationReport,
    "visual-benchmark-suite": VisualBenchmarkSuite,
    "visual-benchmark-report": VisualBenchmarkReport,
    "render-workflow-manifest": RenderWorkflowManifest,
    "capability-manifest": CapabilityManifest,
    "run-manifest": RunManifest,
    "training-example": TrainingExample,
    "training-dataset-manifest": TrainingDatasetManifest,
    "training-dataset-audit-report": TrainingDatasetAuditReport,
    "training-campaign-suite": TrainingCampaignSuite,
    "training-reference-suite": TrainingReferenceSuite,
    "training-reference-validation-report": TrainingReferenceValidationReport,
    "training-campaign-run": TrainingCampaignRun,
    "training-baseline-report": TrainingBaselineReport,
}
