"""Approval-gated world/local Transform proposals for selected Unreal Actors."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.contracts import (
    ActorTransformSnapshot,
    AssistantTransformBatchProposal,
    AssistantTransformProposal,
    ModelIdentity,
    TransformAxisEdit,
    TransformActorAction,
    TransformChange,
    TransformEditIntent,
    UnrealActorTransformContext,
    UnrealTransformSelectionContext,
)
from render_master_bot.models import Vector3
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema


SYSTEM_PROMPT = """You interpret one user's requested transform edit for an Editor-selected Unreal Actor selection.
Return exactly one TransformEditIntent JSON object matching the supplied schema.
Use Unreal coordinates: +X forward, +Y right, +Z up. Locations are centimeters.
Rotation axes map to Unreal as x=roll, y=pitch, z=yaw, in degrees.
Use coordinate_space=local only when the user explicitly says local, relative, each object's own
axes, forward/back/right/left relative to each Actor, or rotation around each Actor's own axis.
Otherwise use coordinate_space=world. In local space, location and rotation support add only;
never use set. The same intent is applied independently to every selected Actor.
For each channel, omitted axes are preserved. Use:
- location: preserve, set, or add;
- rotation: preserve, set, or add;
- scale: preserve, set, or multiply.
Never use multiply for location or rotation. Never use add for scale.
Examples: "move up 50 cm" means world location add z=50; "move each selected Actor forward
100 cm in its own local space" means local location add x=100; "rotate each around its local Z by
30 degrees" means local rotation add z=30; "double their height" means scale multiply z=2.
Do not choose, reorder, or rename Actors. Do not output Unreal commands, code, Markdown, or prose.
If the request is not a uniform transform edit for the full selection, depends on unknown geometry,
requires arranging actors relative to one another, or is too ambiguous to map safely, return
outcome=unresolved with all channels preserved and name the missing capability.
Always include every top-level field. A proposed response has this exact shape:
{"schema_version":"0.1","outcome":"proposed","coordinate_space":"world",
"location":{"operation":"preserve","x":null,"y":null,"z":null},
"rotation":{"operation":"preserve","x":null,"y":null,"z":null},
"scale":{"operation":"preserve","x":null,"y":null,"z":null},
"rationale":"...","missing_capabilities":[]}
Replace preserve and the relevant null axes only where the request calls for a change.
For unresolved, keep all three channels exactly preserved and provide at least one short string in
missing_capabilities. Never claim that the schema itself is a missing capability.
"""

FORMAT_RETRY_PROMPT = """The previous TransformEditIntent was rejected:
{validation_error}

Return one corrected complete JSON object. Do not include Markdown or commentary.
Include schema_version, outcome, coordinate_space, location, rotation, scale, rationale, and
missing_capabilities. If outcome is unresolved, all three operations must be preserve with null axes,
and missing_capabilities must contain at least one concrete capability.
"""

MAX_LOCATION_CM = 10_000_000.0
MAX_LOCATION_DELTA_CM = 1_000_000.0
MAX_ROTATION_EDIT_DEG = 3_600.0
MIN_ABS_SCALE = 0.01
MAX_ABS_SCALE = 100.0
EPSILON = 1e-6


class StructuredTransformClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict,
    ) -> StructuredResponse: ...


class TransformProposalError(RuntimeError):
    """Raised when a transform request or model response is unsafe or invalid."""

    def __init__(
        self,
        message: str,
        response: StructuredResponse | None = None,
        attempt_count: int = 1,
    ):
        super().__init__(message)
        self.response = response
        self.attempt_count = attempt_count


@dataclass(frozen=True)
class TransformProposalResult:
    proposal: AssistantTransformProposal
    response: StructuredResponse
    attempt_count: int


@dataclass(frozen=True)
class TransformBatchProposalResult:
    proposal: AssistantTransformBatchProposal
    response: StructuredResponse
    attempt_count: int


def load_transform_context(path: str | Path) -> UnrealActorTransformContext:
    try:
        return UnrealActorTransformContext.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise TransformProposalError(f"invalid Unreal transform context: {exc}") from exc


def load_transform_selection_context(
    path: str | Path,
) -> UnrealTransformSelectionContext:
    try:
        return UnrealTransformSelectionContext.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise TransformProposalError(
            f"invalid Unreal Transform selection context: {exc}"
        ) from exc


def _channel_values(snapshot: ActorTransformSnapshot, channel: str) -> Vector3:
    return {
        "location": snapshot.location_cm,
        "rotation": snapshot.rotation_deg,
        "scale": snapshot.scale,
    }[channel]


def _normalize_degrees(value: float) -> float:
    normalized = (value + 180.0) % 360.0 - 180.0
    return 180.0 if math.isclose(normalized, -180.0, abs_tol=EPSILON) else normalized


def _rotator_to_quaternion(rotation: Vector3) -> tuple[float, float, float, float]:
    """Match Unreal FRotator(Roll=X, Pitch=Y, Yaw=Z)::Quaternion()."""

    pitch = math.radians(math.fmod(rotation.y, 360.0)) * 0.5
    yaw = math.radians(math.fmod(rotation.z, 360.0)) * 0.5
    roll = math.radians(math.fmod(rotation.x, 360.0)) * 0.5
    sp, cp = math.sin(pitch), math.cos(pitch)
    sy, cy = math.sin(yaw), math.cos(yaw)
    sr, cr = math.sin(roll), math.cos(roll)
    return (
        cr * sp * sy - sr * cp * cy,
        -cr * sp * cy - sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def _quaternion_multiply(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    value = (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )
    magnitude = math.sqrt(sum(component * component for component in value))
    if magnitude <= EPSILON:
        raise TransformProposalError("local rotation produced an invalid quaternion")
    return tuple(component / magnitude for component in value)


def _quaternion_to_rotator(
    quaternion: tuple[float, float, float, float],
) -> Vector3:
    """Match Unreal FQuat::Rotator() and return X=Roll, Y=Pitch, Z=Yaw."""

    x, y, z, w = quaternion
    singularity_test = z * x - w * y
    yaw_y = 2.0 * (w * z + x * y)
    yaw_x = 1.0 - 2.0 * (y * y + z * z)
    threshold = 0.4999995
    if singularity_test < -threshold:
        pitch = -90.0
        yaw = _normalize_degrees(-2.0 * math.degrees(math.atan2(x, w)))
        roll = 0.0
    elif singularity_test > threshold:
        pitch = 90.0
        yaw = _normalize_degrees(2.0 * math.degrees(math.atan2(x, w)))
        roll = 0.0
    else:
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, 2.0 * singularity_test))))
        yaw = math.degrees(math.atan2(yaw_y, yaw_x))
        roll = math.degrees(
            math.atan2(-2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
        )
    return Vector3(
        x=_normalize_degrees(roll),
        y=_normalize_degrees(pitch),
        z=_normalize_degrees(yaw),
    )


def _rotate_local_vector(rotation: Vector3, vector: Vector3) -> Vector3:
    qx, qy, qz, qw = _rotator_to_quaternion(rotation)
    ux, uy, uz = qx, qy, qz
    vx, vy, vz = vector.x, vector.y, vector.z
    dot_uv = ux * vx + uy * vy + uz * vz
    dot_uu = ux * ux + uy * uy + uz * uz
    cross_x = uy * vz - uz * vy
    cross_y = uz * vx - ux * vz
    cross_z = ux * vy - uy * vx
    return Vector3(
        x=2.0 * dot_uv * ux + (qw * qw - dot_uu) * vx + 2.0 * qw * cross_x,
        y=2.0 * dot_uv * uy + (qw * qw - dot_uu) * vy + 2.0 * qw * cross_y,
        z=2.0 * dot_uv * uz + (qw * qw - dot_uu) * vz + 2.0 * qw * cross_z,
    )


def _changed_axes(before: Vector3, after: Vector3) -> list[str]:
    return [
        axis
        for axis in ("x", "y", "z")
        if not math.isclose(
            float(getattr(before, axis)),
            float(getattr(after, axis)),
            rel_tol=0.0,
            abs_tol=EPSILON,
        )
    ]


def _apply_channel(
    *,
    channel: str,
    before: Vector3,
    edit: TransformAxisEdit,
) -> tuple[Vector3, list[str]]:
    if edit.operation == "preserve":
        return before, []
    allowed = {
        "location": {"set", "add"},
        "rotation": {"set", "add"},
        "scale": {"set", "multiply"},
    }[channel]
    if edit.operation not in allowed:
        raise TransformProposalError(
            f"{channel} does not support the {edit.operation!r} operation"
        )

    result = before.model_dump()
    changed_axes: list[str] = []
    for axis in ("x", "y", "z"):
        value = getattr(edit, axis)
        if value is None:
            continue
        original = float(getattr(before, axis))
        if channel == "location" and edit.operation == "add":
            if abs(value) > MAX_LOCATION_DELTA_CM:
                raise TransformProposalError(
                    f"location delta on {axis} exceeds {MAX_LOCATION_DELTA_CM:g} cm"
                )
            updated = original + value
        elif channel == "rotation" and edit.operation == "add":
            if abs(value) > MAX_ROTATION_EDIT_DEG:
                raise TransformProposalError(
                    f"rotation delta on {axis} exceeds {MAX_ROTATION_EDIT_DEG:g} degrees"
                )
            updated = original + value
        elif edit.operation == "multiply":
            updated = original * value
        else:
            updated = value
        if channel == "location" and abs(updated) > MAX_LOCATION_CM:
            raise TransformProposalError(
                f"resulting location on {axis} exceeds {MAX_LOCATION_CM:g} cm"
            )
        if channel == "rotation":
            if edit.operation == "set" and abs(value) > MAX_ROTATION_EDIT_DEG:
                raise TransformProposalError(
                    f"rotation value on {axis} exceeds {MAX_ROTATION_EDIT_DEG:g} degrees"
                )
            updated = _normalize_degrees(updated)
        if channel == "scale" and not MIN_ABS_SCALE <= abs(updated) <= MAX_ABS_SCALE:
            raise TransformProposalError(
                f"resulting scale on {axis} must have magnitude between "
                f"{MIN_ABS_SCALE:g} and {MAX_ABS_SCALE:g}"
            )
        result[axis] = updated
        if not math.isclose(updated, original, rel_tol=0.0, abs_tol=EPSILON):
            changed_axes.append(axis)
    return Vector3.model_validate(result), changed_axes


def compile_transform_intent(
    context: UnrealActorTransformContext,
    intent: TransformEditIntent,
    *,
    allow_noop: bool = False,
) -> tuple[ActorTransformSnapshot, list[TransformChange]]:
    if intent.outcome != "proposed":
        raise TransformProposalError("cannot compile an unresolved transform intent")
    if not context.is_editable or context.is_locked:
        raise TransformProposalError("captured Actor is not editable or is locked")

    if intent.coordinate_space == "local":
        if intent.location.operation not in {"preserve", "add"}:
            raise TransformProposalError(
                "local-space location supports only preserve or add"
            )
        if intent.rotation.operation not in {"preserve", "add"}:
            raise TransformProposalError(
                "local-space rotation supports only preserve or add"
            )

        before = context.transform
        after_location = before.location_cm
        if intent.location.operation == "add":
            local_delta = Vector3(
                x=intent.location.x or 0.0,
                y=intent.location.y or 0.0,
                z=intent.location.z or 0.0,
            )
            for axis in ("x", "y", "z"):
                if abs(getattr(local_delta, axis)) > MAX_LOCATION_DELTA_CM:
                    raise TransformProposalError(
                        f"local location delta on {axis} exceeds "
                        f"{MAX_LOCATION_DELTA_CM:g} cm"
                    )
            world_delta = _rotate_local_vector(before.rotation_deg, local_delta)
            for axis in ("x", "y", "z"):
                if abs(getattr(world_delta, axis)) > MAX_LOCATION_DELTA_CM:
                    raise TransformProposalError(
                        f"resulting world location delta on {axis} exceeds "
                        f"{MAX_LOCATION_DELTA_CM:g} cm"
                    )
            after_location = Vector3(
                x=before.location_cm.x + world_delta.x,
                y=before.location_cm.y + world_delta.y,
                z=before.location_cm.z + world_delta.z,
            )
            for axis in ("x", "y", "z"):
                if abs(getattr(after_location, axis)) > MAX_LOCATION_CM:
                    raise TransformProposalError(
                        f"resulting location on {axis} exceeds {MAX_LOCATION_CM:g} cm"
                    )

        after_rotation = before.rotation_deg
        if intent.rotation.operation == "add":
            local_delta_rotation = Vector3(
                x=intent.rotation.x or 0.0,
                y=intent.rotation.y or 0.0,
                z=intent.rotation.z or 0.0,
            )
            for axis in ("x", "y", "z"):
                if abs(getattr(local_delta_rotation, axis)) > MAX_ROTATION_EDIT_DEG:
                    raise TransformProposalError(
                        f"local rotation delta on {axis} exceeds "
                        f"{MAX_ROTATION_EDIT_DEG:g} degrees"
                    )
            after_rotation = _quaternion_to_rotator(
                _quaternion_multiply(
                    _rotator_to_quaternion(before.rotation_deg),
                    _rotator_to_quaternion(local_delta_rotation),
                )
            )

        after_scale, _ = _apply_channel(
            channel="scale",
            before=before.scale,
            edit=intent.scale,
        )
        after = ActorTransformSnapshot(
            location_cm=after_location,
            rotation_deg=after_rotation,
            scale=after_scale,
        )
        changes: list[TransformChange] = []
        for channel, edit, channel_before, channel_after in (
            ("location", intent.location, before.location_cm, after.location_cm),
            ("rotation", intent.rotation, before.rotation_deg, after.rotation_deg),
            ("scale", intent.scale, before.scale, after.scale),
        ):
            axes = _changed_axes(channel_before, channel_after)
            if axes:
                changes.append(TransformChange(
                    channel=channel,
                    operation=edit.operation,
                    axes=axes,
                    before=channel_before,
                    after=channel_after,
                ))
        if not changes and not allow_noop:
            raise TransformProposalError("transform request produces no observable change")
        return after, changes

    after_values: dict[str, Vector3] = {}
    changes: list[TransformChange] = []
    for channel, edit in (
        ("location", intent.location),
        ("rotation", intent.rotation),
        ("scale", intent.scale),
    ):
        before = _channel_values(context.transform, channel)
        after, axes = _apply_channel(channel=channel, before=before, edit=edit)
        after_values[channel] = after
        if axes:
            changes.append(TransformChange(
                channel=channel,
                operation=edit.operation,
                axes=axes,
                before=before,
                after=after,
            ))
    if not changes and not allow_noop:
        raise TransformProposalError("transform request produces no observable change")
    return ActorTransformSnapshot(
        location_cm=after_values["location"],
        rotation_deg=after_values["rotation"],
        scale=after_values["scale"],
    ), changes


def _parse_intent(response: StructuredResponse) -> TransformEditIntent:
    return TransformEditIntent.model_validate_json(response.content)


def propose_transform_change(
    *,
    prompt: str,
    context: UnrealActorTransformContext,
    client: StructuredTransformClient,
    model: str,
    proposal_id: str,
) -> TransformProposalResult:
    request = prompt.strip()
    if not request:
        raise TransformProposalError("transform request cannot be empty")
    target_text = context.model_dump_json(indent=2)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Editor-captured target evidence (read-only):\n"
                f"{target_text}\n\nUser request:\n{request}"
            ),
        },
    ]
    schema = ollama_model_schema(TransformEditIntent)
    response = client.chat_structured(
        model=model,
        messages=messages,
        json_schema=schema,
    )
    attempt_count = 1
    try:
        intent = _parse_intent(response)
        after, changes = (
            compile_transform_intent(context, intent)
            if intent.outcome == "proposed"
            else (None, [])
        )
    except (ValidationError, TransformProposalError) as first_error:
        response = client.chat_structured(
            model=model,
            messages=[
                *messages,
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": FORMAT_RETRY_PROMPT.format(
                        validation_error=str(first_error)[:2000]
                    ),
                },
            ],
            json_schema=schema,
        )
        attempt_count = 2
        try:
            intent = _parse_intent(response)
            after, changes = (
                compile_transform_intent(context, intent)
                if intent.outcome == "proposed"
                else (None, [])
            )
        except (ValidationError, TransformProposalError) as exc:
            raise TransformProposalError(
                f"model returned an unsafe transform intent after one retry: {exc}",
                response=response,
                attempt_count=attempt_count,
            ) from exc

    proposal = AssistantTransformProposal(
        proposal_id=proposal_id,
        status=intent.outcome,
        request=request,
        target=context,
        proposed_by=ModelIdentity(provider="ollama", model=response.model),
        coordinate_space=intent.coordinate_space,
        before=context.transform,
        after=after,
        changes=changes,
        rationale=intent.rationale,
        missing_capabilities=intent.missing_capabilities,
    )
    return TransformProposalResult(
        proposal=proposal,
        response=response,
        attempt_count=attempt_count,
    )


def propose_transform_batch_change(
    *,
    prompt: str,
    selection: UnrealTransformSelectionContext,
    client: StructuredTransformClient,
    model: str,
    proposal_id: str,
) -> TransformBatchProposalResult:
    request = prompt.strip()
    if not request:
        raise TransformProposalError("Transform request cannot be empty")
    selection_text = selection.model_dump_json(indent=2)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Editor-captured ordered selection evidence (read-only):\n"
                f"{selection_text}\n\nUser request:\n{request}"
            ),
        },
    ]
    schema = ollama_model_schema(TransformEditIntent)
    response = client.chat_structured(
        model=model,
        messages=messages,
        json_schema=schema,
    )
    attempt_count = 1

    def compile_actions(intent: TransformEditIntent) -> list[TransformActorAction]:
        if intent.outcome != "proposed":
            return []
        actions = []
        for actor in selection.actors:
            after, changes = compile_transform_intent(actor, intent, allow_noop=True)
            actions.append(TransformActorAction(
                target=actor,
                before=actor.transform,
                after=after,
                changes=changes,
            ))
        if not any(action.changes for action in actions):
            raise TransformProposalError(
                "batch Transform request produces no observable change"
            )
        return actions

    try:
        intent = _parse_intent(response)
        actions = compile_actions(intent)
    except (ValidationError, TransformProposalError) as first_error:
        response = client.chat_structured(
            model=model,
            messages=[
                *messages,
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": FORMAT_RETRY_PROMPT.format(
                        validation_error=str(first_error)[:2000]
                    ),
                },
            ],
            json_schema=schema,
        )
        attempt_count = 2
        try:
            intent = _parse_intent(response)
            actions = compile_actions(intent)
        except (ValidationError, TransformProposalError) as exc:
            raise TransformProposalError(
                f"model returned an unsafe batch Transform intent after one retry: {exc}",
                response=response,
                attempt_count=attempt_count,
            ) from exc

    proposal = AssistantTransformBatchProposal(
        proposal_id=proposal_id,
        status=intent.outcome,
        request=request,
        selection=selection,
        proposed_by=ModelIdentity(provider="ollama", model=response.model),
        coordinate_space=intent.coordinate_space,
        actions=actions,
        rationale=intent.rationale,
        missing_capabilities=intent.missing_capabilities,
    )
    return TransformBatchProposalResult(
        proposal=proposal,
        response=response,
        attempt_count=attempt_count,
    )
