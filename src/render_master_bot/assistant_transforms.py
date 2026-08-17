"""Approval-gated natural-language Transform proposals for one Unreal Actor."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.contracts import (
    ActorTransformSnapshot,
    AssistantTransformProposal,
    ModelIdentity,
    TransformAxisEdit,
    TransformChange,
    TransformEditIntent,
    UnrealActorTransformContext,
)
from render_master_bot.models import Vector3
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema


SYSTEM_PROMPT = """You interpret one user's requested transform edit for one selected Unreal Actor.
Return exactly one TransformEditIntent JSON object matching the supplied schema.
Use Unreal world space only: +X forward, +Y right, +Z up. Locations are centimeters.
Rotation axes map to Unreal as x=roll, y=pitch, z=yaw, in degrees.
For each channel, omitted axes are preserved. Use:
- location: preserve, set, or add;
- rotation: preserve, set, or add;
- scale: preserve, set, or multiply.
Never use multiply for location or rotation. Never use add for scale.
Examples: "move up 50 cm" means location add z=50; "rotate right 30 degrees" means
rotation add z=30; "double its height" means scale multiply z=2.
Do not choose or rename the Actor. Do not output Unreal commands, code, Markdown, or prose.
If the request is not a transform edit, requires local/relative space, depends on unknown geometry,
or is too ambiguous to map safely, return outcome=unresolved with all channels preserved and name
the missing capability.
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


def load_transform_context(path: str | Path) -> UnrealActorTransformContext:
    try:
        return UnrealActorTransformContext.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise TransformProposalError(f"invalid Unreal transform context: {exc}") from exc


def _channel_values(snapshot: ActorTransformSnapshot, channel: str) -> Vector3:
    return {
        "location": snapshot.location_cm,
        "rotation": snapshot.rotation_deg,
        "scale": snapshot.scale,
    }[channel]


def _normalize_degrees(value: float) -> float:
    normalized = (value + 180.0) % 360.0 - 180.0
    return 180.0 if math.isclose(normalized, -180.0, abs_tol=EPSILON) else normalized


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
) -> tuple[ActorTransformSnapshot, list[TransformChange]]:
    if intent.outcome != "proposed":
        raise TransformProposalError("cannot compile an unresolved transform intent")
    if not context.is_editable or context.is_locked:
        raise TransformProposalError("captured Actor is not editable or is locked")

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
    if not changes:
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
