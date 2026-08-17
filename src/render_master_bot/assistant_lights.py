"""Approval-gated natural-language property proposals for one Unreal light."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.contracts import (
    AssistantLightProposal,
    EditorLightSnapshot,
    LightEditIntent,
    LightPropertyChange,
    LightScalarEdit,
    ModelIdentity,
    TransformAxisEdit,
    UnrealLightContext,
)
from render_master_bot.models import Vector3
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema


SYSTEM_PROMPT = """You interpret one user's requested property edit for one selected Unreal light.
Return exactly one LightEditIntent JSON object matching the supplied schema.
The Editor has already selected the light. Never choose, rename, spawn, or delete an Actor.
Intensity values use the exact frozen intensity_unit in the context. Never convert or change units.
Use intensity set for an absolute value, add for an absolute increase/decrease, and multiply for a
percentage or factor. For example, "20% brighter" means multiply 1.2.
color_rgb is linear RGB from 0 to 1. A direct color request should set use_temperature=false.
A Kelvin request should set temperature_kelvin and use_temperature=true.
attenuation_radius_cm is available only for point, spot, and rect lights.
inner_cone_deg and outer_cone_deg are available only for spot lights; inner cannot exceed outer.
rotation uses Unreal world axes x=roll, y=pitch, z=yaw and is meaningful only for directional,
spot, and rect lights. Point-light rotation must be unresolved.
Do not output Unreal commands, code, Markdown, or prose outside the JSON.
If the request needs exposure/camera changes, geometry reasoning, multiple lights, unsupported light
properties, or is ambiguous, return outcome=unresolved and name the concrete missing capability.
Always include every top-level field. Use this exact shape and replace only requested values:
{"schema_version":"0.1","outcome":"proposed",
"intensity":{"operation":"preserve","value":null},"color_rgb":null,
"use_temperature":null,"temperature_kelvin":null,"cast_shadows":null,
"attenuation_radius_cm":{"operation":"preserve","value":null},
"inner_cone_deg":{"operation":"preserve","value":null},
"outer_cone_deg":{"operation":"preserve","value":null},
"rotation":{"operation":"preserve","x":null,"y":null,"z":null},
"rationale":"...","missing_capabilities":[]}
For unresolved, keep every edit preserved/null and provide at least one missing capability.
"""

FORMAT_RETRY_PROMPT = """The previous LightEditIntent was rejected:
{validation_error}

Return one corrected complete JSON object with every top-level field. If unresolved, keep every
edit preserved/null and include at least one concrete missing capability. No Markdown or commentary.
"""

MAX_NON_EV_INTENSITY = 1_000_000_000.0
MAX_DIRECTIONAL_LUX = 10_000_000.0
MIN_EV = -20.0
MAX_EV = 30.0
MAX_MULTIPLIER = 100.0
MIN_ATTENUATION_CM = 1.0
MAX_ATTENUATION_CM = 10_000_000.0
MAX_ATTENUATION_DELTA_CM = 1_000_000.0
MAX_ROTATION_EDIT_DEG = 3_600.0
EPSILON = 1e-6


class StructuredLightClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict,
    ) -> StructuredResponse: ...


class LightProposalError(RuntimeError):
    """Raised when a light request or model response is unsafe or invalid."""

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
class LightProposalResult:
    proposal: AssistantLightProposal
    response: StructuredResponse
    attempt_count: int


def load_light_context(path: str | Path) -> UnrealLightContext:
    try:
        return UnrealLightContext.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise LightProposalError(f"invalid Unreal light context: {exc}") from exc


def _normalize_degrees(value: float) -> float:
    normalized = (value + 180.0) % 360.0 - 180.0
    return 180.0 if math.isclose(normalized, -180.0, abs_tol=EPSILON) else normalized


def _apply_scalar(before: float, edit: LightScalarEdit, *, property_name: str) -> float:
    if edit.operation == "preserve":
        return before
    assert edit.value is not None
    if edit.operation == "set":
        return edit.value
    if edit.operation == "add":
        return before + edit.value
    if not 0.0 <= edit.value <= MAX_MULTIPLIER:
        raise LightProposalError(
            f"{property_name} multiplier must be between 0 and {MAX_MULTIPLIER:g}"
        )
    return before * edit.value


def _apply_rotation(before: Vector3, edit: TransformAxisEdit) -> Vector3:
    if edit.operation == "preserve":
        return before
    if edit.operation not in {"set", "add"}:
        raise LightProposalError("light rotation supports only set or add")
    values = before.model_dump()
    for axis in ("x", "y", "z"):
        requested = getattr(edit, axis)
        if requested is None:
            continue
        if abs(requested) > MAX_ROTATION_EDIT_DEG:
            raise LightProposalError(
                f"rotation edit on {axis} exceeds {MAX_ROTATION_EDIT_DEG:g} degrees"
            )
        original = float(getattr(before, axis))
        values[axis] = _normalize_degrees(
            requested if edit.operation == "set" else original + requested
        )
    return Vector3.model_validate(values)


def _changed(before: object, after: object) -> bool:
    if isinstance(before, float) and isinstance(after, float):
        return not math.isclose(before, after, rel_tol=0.0, abs_tol=EPSILON)
    return before != after


def compile_light_intent(
    context: UnrealLightContext,
    intent: LightEditIntent,
) -> tuple[EditorLightSnapshot, list[LightPropertyChange]]:
    if intent.outcome != "proposed":
        raise LightProposalError("cannot compile an unresolved light intent")
    if not context.is_editable or context.is_locked:
        raise LightProposalError("captured light Actor is not editable or is locked")

    local = context.light_kind in {"point", "spot", "rect"}
    if not local and intent.attenuation_radius_cm.operation != "preserve":
        raise LightProposalError("directional lights do not have attenuation radius")
    if context.light_kind != "spot" and (
        intent.inner_cone_deg.operation != "preserve"
        or intent.outer_cone_deg.operation != "preserve"
    ):
        raise LightProposalError("only spot lights have editable cone angles")
    if context.light_kind == "point" and intent.rotation.operation != "preserve":
        raise LightProposalError("point-light rotation has no visual effect")
    if intent.temperature_kelvin is not None and intent.use_temperature is False:
        raise LightProposalError("a Kelvin edit cannot explicitly disable temperature")

    before = context.light
    values = before.model_dump(mode="python")
    changes: list[LightPropertyChange] = []

    def record(property_name: str, operation: str, value: object) -> None:
        before_value = values[property_name]
        values[property_name] = value
        if _changed(before_value, value):
            changes.append(LightPropertyChange(
                property=property_name,
                operation=operation,
            ))

    intensity = _apply_scalar(before.intensity, intent.intensity, property_name="intensity")
    if before.intensity_unit == "ev":
        if intent.intensity.operation == "multiply":
            raise LightProposalError("EV intensity does not support multiply")
        if not MIN_EV <= intensity <= MAX_EV:
            raise LightProposalError(f"EV intensity must be between {MIN_EV:g} and {MAX_EV:g}")
    else:
        maximum = (
            MAX_DIRECTIONAL_LUX
            if before.intensity_unit == "lux"
            else MAX_NON_EV_INTENSITY
        )
        if not 0.0 <= intensity <= maximum:
            raise LightProposalError(
                f"{before.intensity_unit} intensity must be between 0 and {maximum:g}"
            )
    if intent.intensity.operation != "preserve":
        record("intensity", intent.intensity.operation, intensity)

    if intent.color_rgb is not None:
        record("color_rgb", "set", intent.color_rgb.model_dump(mode="python"))
        if intent.use_temperature is None:
            record("use_temperature", "set", False)
    if intent.temperature_kelvin is not None:
        record("temperature_kelvin", "set", intent.temperature_kelvin)
        if intent.use_temperature is None:
            record("use_temperature", "set", True)
    if intent.use_temperature is not None:
        record("use_temperature", "set", intent.use_temperature)
    if intent.cast_shadows is not None:
        record("cast_shadows", "set", intent.cast_shadows)

    if local and intent.attenuation_radius_cm.operation != "preserve":
        assert before.attenuation_radius_cm is not None
        radius = _apply_scalar(
            before.attenuation_radius_cm,
            intent.attenuation_radius_cm,
            property_name="attenuation radius",
        )
        if intent.attenuation_radius_cm.operation == "add" and abs(
            intent.attenuation_radius_cm.value or 0.0
        ) > MAX_ATTENUATION_DELTA_CM:
            raise LightProposalError("attenuation radius delta exceeds the safety bound")
        if not MIN_ATTENUATION_CM <= radius <= MAX_ATTENUATION_CM:
            raise LightProposalError("attenuation radius is outside the safety bounds")
        record("attenuation_radius_cm", intent.attenuation_radius_cm.operation, radius)

    if context.light_kind == "spot":
        assert before.inner_cone_deg is not None and before.outer_cone_deg is not None
        inner = _apply_scalar(
            before.inner_cone_deg,
            intent.inner_cone_deg,
            property_name="inner cone",
        )
        outer = _apply_scalar(
            before.outer_cone_deg,
            intent.outer_cone_deg,
            property_name="outer cone",
        )
        if intent.inner_cone_deg.operation == "multiply" or intent.outer_cone_deg.operation == "multiply":
            raise LightProposalError("spot cone angles support only set or add")
        if not 0.0 <= inner <= 89.0 or not 0.0 < outer <= 89.0 or inner > outer:
            raise LightProposalError("spot cone angles are outside bounds or incorrectly ordered")
        if intent.inner_cone_deg.operation != "preserve":
            record("inner_cone_deg", intent.inner_cone_deg.operation, inner)
        if intent.outer_cone_deg.operation != "preserve":
            record("outer_cone_deg", intent.outer_cone_deg.operation, outer)

    if intent.rotation.operation != "preserve":
        rotation = _apply_rotation(before.rotation_deg, intent.rotation)
        rotation_value = rotation.model_dump(mode="python")
        if _changed(values["rotation_deg"], rotation_value):
            values["rotation_deg"] = rotation_value
            changes.append(LightPropertyChange(
                property="rotation",
                operation=intent.rotation.operation,
            ))

    after = EditorLightSnapshot.model_validate(values)
    if not changes:
        raise LightProposalError("light request produces no observable change")
    return after, changes


def _parse_intent(response: StructuredResponse) -> LightEditIntent:
    return LightEditIntent.model_validate_json(response.content)


def propose_light_change(
    *,
    prompt: str,
    context: UnrealLightContext,
    client: StructuredLightClient,
    model: str,
    proposal_id: str,
) -> LightProposalResult:
    request = prompt.strip()
    if not request:
        raise LightProposalError("light request cannot be empty")
    context_text = context.model_dump_json(indent=2)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Editor-captured light evidence (read-only):\n"
                f"{context_text}\n\nUser request:\n{request}"
            ),
        },
    ]
    schema = ollama_model_schema(LightEditIntent)
    response = client.chat_structured(model=model, messages=messages, json_schema=schema)
    attempt_count = 1
    try:
        intent = _parse_intent(response)
        after, changes = (
            compile_light_intent(context, intent)
            if intent.outcome == "proposed"
            else (None, [])
        )
    except (ValidationError, LightProposalError) as first_error:
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
                compile_light_intent(context, intent)
                if intent.outcome == "proposed"
                else (None, [])
            )
        except (ValidationError, LightProposalError) as exc:
            raise LightProposalError(
                f"model returned an unsafe light intent after one retry: {exc}",
                response=response,
                attempt_count=attempt_count,
            ) from exc

    proposal = AssistantLightProposal(
        proposal_id=proposal_id,
        status=intent.outcome,
        request=request,
        target=context,
        proposed_by=ModelIdentity(provider="ollama", model=response.model),
        before=context.light,
        after=after,
        changes=changes,
        rationale=intent.rationale,
        missing_capabilities=intent.missing_capabilities,
    )
    return LightProposalResult(
        proposal=proposal,
        response=response,
        attempt_count=attempt_count,
    )
