"""Approval-gated, role-aware three-point lighting rigs for Unreal Editor."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.contracts import (
    AssistantLightingRigProposal,
    EditorLightSnapshot,
    LightingRigIntent,
    LightingRigLightAction,
    LightingRigLightSnapshot,
    LightingRigPropertyChange,
    ModelIdentity,
    UnrealLightingRigContext,
)
from render_master_bot.models import Vector3
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema


SYSTEM_PROMPT = """You assign exactly three frozen Unreal local lights to a bounded three-point rig.
Return exactly one LightingRigIntent JSON object matching the supplied schema.
The Editor has already selected one subject, one perspective Camera/Cine Camera, and exactly three
Movable local lights. Never invent, rename, spawn, delete, or omit an Actor. For a proposed rig,
assign each exact supplied light actor_path once and use key, fill, and rim exactly once.
The host—not you—computes trusted positions, aim rotations, intensities, attenuation, and cone angles
from frozen subject bounds and the camera basis. Do not output numeric transforms or intensities.
contrast controls the Fill/Rim ratios: soft, balanced, or dramatic.
palette can preserve existing temperatures, set neutral, use warm Key/cool Rim, or cool Key/warm Rim.
key_side is from the camera's view. spacing controls deterministic distance from the subject.
brightness scales the captured positive intensity baseline using dim, balanced, or bright.
Use actor labels, existing locations, and the request to make the role assignment. If the user names
a role for a light, preserve that instruction exactly. If the request needs more or fewer lights,
changes the subject or camera, requires light creation/deletion/type changes, asks for an unsupported
effect, or is ambiguous, return outcome=unresolved with a concrete missing capability.
Do not output Unreal commands, code, Markdown, or prose outside the JSON.
Always include every top-level field. Use this exact shape:
{"schema_version":"0.1","outcome":"proposed","assignments":[
{"actor_path":"exact supplied path","role":"key"},
{"actor_path":"exact supplied path","role":"fill"},
{"actor_path":"exact supplied path","role":"rim"}],
"contrast":"balanced","palette":"preserve","key_side":"camera_left",
"spacing":"standard","brightness":"balanced","rationale":"...",
"missing_capabilities":[]}
For unresolved, use assignments=[] and include at least one concrete missing capability.
"""

FORMAT_RETRY_PROMPT = """The previous LightingRigIntent was rejected:
{validation_error}

Return one corrected complete JSON object. Use every supplied actor_path exactly once with key, fill,
and rim exactly once. If unresolved, use assignments=[] and name a concrete missing capability.
No Markdown or commentary.
"""

EPSILON = 1e-6
MIN_RIG_DISTANCE_CM = 100.0
MAX_RIG_DISTANCE_CM = 1_000_000.0
MAX_LOCAL_INTENSITY = 1_000_000_000.0
MAX_ATTENUATION_CM = 10_000_000.0

CONTRAST_RATIOS = {
    "soft": {"key": 1.0, "fill": 0.65, "rim": 0.55},
    "balanced": {"key": 1.0, "fill": 0.40, "rim": 0.65},
    "dramatic": {"key": 1.0, "fill": 0.20, "rim": 0.90},
}
BRIGHTNESS_FACTORS = {"dim": 0.75, "balanced": 1.0, "bright": 1.5}
SPACING_FACTORS = {"tight": 2.2, "standard": 3.0, "wide": 4.0}
PALETTE_TEMPERATURES = {
    "neutral": {"key": 5600.0, "fill": 5600.0, "rim": 5600.0},
    "warm_cool": {"key": 4200.0, "fill": 5000.0, "rim": 7000.0},
    "cool_warm": {"key": 6800.0, "fill": 6000.0, "rim": 4200.0},
}


class StructuredLightingRigClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict,
    ) -> StructuredResponse: ...


class LightingRigProposalError(RuntimeError):
    """Raised when role-aware rig evidence or model output is unsafe."""

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
class LightingRigProposalResult:
    proposal: AssistantLightingRigProposal
    response: StructuredResponse
    attempt_count: int


def load_lighting_rig_context(path: str | Path) -> UnrealLightingRigContext:
    try:
        return UnrealLightingRigContext.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise LightingRigProposalError(
            f"invalid Unreal lighting-rig context: {exc}"
        ) from exc


def _vector(x: float, y: float, z: float) -> Vector3:
    return Vector3(x=x, y=y, z=z)


def _add(*vectors: Vector3) -> Vector3:
    return _vector(
        sum(value.x for value in vectors),
        sum(value.y for value in vectors),
        sum(value.z for value in vectors),
    )


def _subtract(left: Vector3, right: Vector3) -> Vector3:
    return _vector(left.x - right.x, left.y - right.y, left.z - right.z)


def _scale(value: Vector3, factor: float) -> Vector3:
    return _vector(value.x * factor, value.y * factor, value.z * factor)


def _dot(left: Vector3, right: Vector3) -> float:
    return left.x * right.x + left.y * right.y + left.z * right.z


def _length(value: Vector3) -> float:
    return math.sqrt(_dot(value, value))


def _normalized(value: Vector3) -> Vector3:
    length = _length(value)
    if length <= EPSILON:
        raise LightingRigProposalError("lighting-rig direction has zero length")
    return _scale(value, 1.0 / length)


def _camera_basis(rotation: Vector3) -> tuple[Vector3, Vector3, Vector3]:
    """Match Unreal's FRotationMatrix axes for Roll=X, Pitch=Y, Yaw=Z."""

    roll = math.radians(rotation.x)
    pitch = math.radians(rotation.y)
    yaw = math.radians(rotation.z)
    sr, cr = math.sin(roll), math.cos(roll)
    sp, cp = math.sin(pitch), math.cos(pitch)
    sy, cy = math.sin(yaw), math.cos(yaw)
    forward = _vector(cp * cy, cp * sy, sp)
    right = _vector(sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    up = _vector(-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp)
    return forward, right, up


def _aim_rotation(location: Vector3, target: Vector3) -> Vector3:
    direction = _subtract(target, location)
    horizontal = math.hypot(direction.x, direction.y)
    if horizontal <= EPSILON and abs(direction.z) <= EPSILON:
        raise LightingRigProposalError("a rig light cannot occupy the subject center")
    pitch = math.degrees(math.atan2(direction.z, horizontal))
    yaw = math.degrees(math.atan2(direction.y, direction.x))
    return _vector(0.0, pitch, yaw)


def _role_locations(
    context: UnrealLightingRigContext,
    intent: LightingRigIntent,
) -> dict[str, Vector3]:
    center = context.subject.bounds.center_cm
    forward, right, up = _camera_basis(context.camera.rotation_deg)
    camera_to_subject = _subtract(center, context.camera.location_cm)
    if _length(camera_to_subject) <= EPSILON:
        raise LightingRigProposalError("camera and subject center cannot occupy the same point")
    if _dot(forward, _normalized(camera_to_subject)) < 0.1:
        raise LightingRigProposalError(
            "the frozen camera is not facing the selected subject"
        )

    bounds = context.subject.bounds
    radius = max(
        bounds.sphere_radius_cm,
        bounds.extent_cm.x,
        bounds.extent_cm.y,
        bounds.extent_cm.z,
        25.0,
    )
    distance = min(
        MAX_RIG_DISTANCE_CM,
        max(MIN_RIG_DISTANCE_CM, radius * SPACING_FACTORS[intent.spacing]),
    )
    side = _scale(right, -1.0 if intent.key_side == "camera_left" else 1.0)
    return {
        "key": _add(
            center,
            _scale(forward, -0.85 * distance),
            _scale(side, 0.85 * distance),
            _scale(up, 0.75 * distance),
        ),
        "fill": _add(
            center,
            _scale(forward, -0.75 * distance),
            _scale(side, -0.95 * distance),
            _scale(up, 0.35 * distance),
        ),
        "rim": _add(
            center,
            _scale(forward, 0.75 * distance),
            _scale(side, -0.45 * distance),
            _scale(up, 0.90 * distance),
        ),
    }


def _changed_properties(
    before: LightingRigLightSnapshot,
    after: LightingRigLightSnapshot,
) -> list[LightingRigPropertyChange]:
    properties: list[str] = []
    if before.location_cm != after.location_cm:
        properties.append("location")
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
        if getattr(before.light, attribute) != getattr(after.light, attribute):
            properties.append(name)
    return [LightingRigPropertyChange(property=name) for name in properties]


def compile_lighting_rig_intent(
    context: UnrealLightingRigContext,
    intent: LightingRigIntent,
) -> list[LightingRigLightAction]:
    if intent.outcome != "proposed":
        raise LightingRigProposalError("cannot compile an unresolved lighting-rig intent")

    selected_paths = {light.target.actor_path for light in context.lights}
    assigned_paths = {assignment.actor_path for assignment in intent.assignments}
    if assigned_paths != selected_paths:
        missing = sorted(selected_paths - assigned_paths)
        invented = sorted(assigned_paths - selected_paths)
        raise LightingRigProposalError(
            "lighting-rig assignments must target the complete frozen selection; "
            f"missing={missing}, invented={invented}"
        )
    role_by_path = {
        assignment.actor_path: assignment.role for assignment in intent.assignments
    }
    locations = _role_locations(context, intent)
    baseline = max(light.target.light.intensity for light in context.lights)
    baseline *= BRIGHTNESS_FACTORS[intent.brightness]
    ratios = CONTRAST_RATIOS[intent.contrast]
    temperatures = PALETTE_TEMPERATURES.get(intent.palette)
    subject_center = context.subject.bounds.center_cm
    subject_radius = context.subject.bounds.sphere_radius_cm

    actions: list[LightingRigLightAction] = []
    for selected in context.lights:
        role = role_by_path[selected.target.actor_path]
        before = LightingRigLightSnapshot(
            location_cm=selected.location_cm,
            light=selected.target.light,
        )
        location = locations[role]
        light_values = selected.target.light.model_dump(mode="python")
        if selected.target.light_kind != "point":
            light_values["rotation_deg"] = _aim_rotation(location, subject_center)

        intensity = baseline * ratios[role]
        if not 0.0 <= intensity <= MAX_LOCAL_INTENSITY:
            raise LightingRigProposalError(
                f"computed {role} intensity is outside the safety boundary"
            )
        light_values["intensity"] = intensity

        distance_to_subject = _length(_subtract(subject_center, location))
        required_attenuation = min(
            MAX_ATTENUATION_CM,
            max(1.0, (distance_to_subject + subject_radius) * 1.25),
        )
        current_attenuation = selected.target.light.attenuation_radius_cm
        assert current_attenuation is not None
        light_values["attenuation_radius_cm"] = max(
            current_attenuation,
            required_attenuation,
        )

        if selected.target.light_kind == "spot":
            angular_radius = math.degrees(
                math.atan2(subject_radius, max(distance_to_subject, EPSILON))
            )
            needed_outer = min(70.0, max(10.0, angular_radius * 1.35))
            current_outer = selected.target.light.outer_cone_deg
            current_inner = selected.target.light.inner_cone_deg
            assert current_outer is not None and current_inner is not None
            outer = max(current_outer, needed_outer)
            light_values["outer_cone_deg"] = outer
            light_values["inner_cone_deg"] = min(current_inner, outer * 0.70)

        if temperatures is not None:
            light_values["use_temperature"] = True
            light_values["temperature_kelvin"] = temperatures[role]

        after = LightingRigLightSnapshot(
            location_cm=location,
            light=EditorLightSnapshot.model_validate(light_values),
        )
        actions.append(LightingRigLightAction(
            role=role,
            target=selected,
            before=before,
            after=after,
            changes=_changed_properties(before, after),
        ))

    if not any(action.changes for action in actions):
        raise LightingRigProposalError("lighting-rig request produces no observable change")
    return actions


def _parse_intent(response: StructuredResponse) -> LightingRigIntent:
    return LightingRigIntent.model_validate_json(response.content)


def propose_lighting_rig(
    *,
    prompt: str,
    context: UnrealLightingRigContext,
    client: StructuredLightingRigClient,
    model: str,
    proposal_id: str,
) -> LightingRigProposalResult:
    request = prompt.strip()
    if not request:
        raise LightingRigProposalError("lighting-rig request cannot be empty")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Editor-captured lighting-rig evidence (read-only):\n"
                f"{context.model_dump_json(indent=2)}\n\nUser request:\n{request}"
            ),
        },
    ]
    schema = ollama_model_schema(LightingRigIntent)
    response = client.chat_structured(model=model, messages=messages, json_schema=schema)
    attempt_count = 1

    def compile_response(
        value: StructuredResponse,
    ) -> tuple[LightingRigIntent, list[LightingRigLightAction]]:
        parsed = _parse_intent(value)
        actions = (
            compile_lighting_rig_intent(context, parsed)
            if parsed.outcome == "proposed"
            else []
        )
        return parsed, actions

    try:
        intent, actions = compile_response(response)
    except (ValidationError, LightingRigProposalError) as first_error:
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
            intent, actions = compile_response(response)
        except (ValidationError, LightingRigProposalError) as exc:
            raise LightingRigProposalError(
                f"model returned an unsafe lighting-rig intent after one retry: {exc}",
                response=response,
                attempt_count=attempt_count,
            ) from exc

    proposal = AssistantLightingRigProposal(
        proposal_id=proposal_id,
        status=intent.outcome,
        request=request,
        context=context,
        proposed_by=ModelIdentity(provider="ollama", model=response.model),
        contrast=intent.contrast,
        palette=intent.palette,
        key_side=intent.key_side,
        spacing=intent.spacing,
        brightness=intent.brightness,
        actions=actions,
        rationale=intent.rationale,
        missing_capabilities=intent.missing_capabilities,
    )
    return LightingRigProposalResult(
        proposal=proposal,
        response=response,
        attempt_count=attempt_count,
    )
