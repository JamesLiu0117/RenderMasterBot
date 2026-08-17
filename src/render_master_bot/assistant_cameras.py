"""Approval-gated natural-language property proposals for one Unreal camera."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.contracts import (
    AssistantCameraProposal,
    CameraEditIntent,
    CameraPropertyChange,
    CameraScalarEdit,
    EditorCameraSnapshot,
    ModelIdentity,
    TransformAxisEdit,
    UnrealCameraContext,
)
from render_master_bot.models import Vector3
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema


SYSTEM_PROMPT = """You interpret one user's requested property edit for one selected Unreal camera.
Return exactly one CameraEditIntent JSON object matching the supplied schema.
The Editor has already selected the camera. Never choose, rename, spawn, or delete an Actor.
Use Unreal world space: +X forward, +Y right, +Z up; location is centimeters; rotation axes are
x=roll, y=pitch, z=yaw in degrees. Location and rotation support only set or add.
A standard camera uses field_of_view_deg and must preserve focal_length_mm.
A Cine Camera uses focal_length_mm and must preserve field_of_view_deg. A larger focal length is a
narrower view; a smaller focal length is a wider view. Never change filmback or lens presets.
aperture_fstop and focus_distance_cm are available for both kinds. A focus-distance request should
use focus_mode=manual. Use focus_mode=disabled only when the user explicitly disables depth of field.
Exposure compensation is in EV stops: positive is brighter, negative is darker. A numeric exposure
request should enable exposure_compensation_enabled unless the user explicitly disables it.
Never change aspect ratio, projection mode, Post Process blend weight, tracking-focus targets, or
camera scale. If the request requires framing an unknown Actor, tracking focus, filmback changes,
look-at geometry reasoning, multiple cameras, unsupported settings, or is ambiguous, return
outcome=unresolved and name the concrete missing capability.
Always include every top-level field. Use this exact shape and replace only requested values:
{"schema_version":"0.1","outcome":"proposed",
"location":{"operation":"preserve","x":null,"y":null,"z":null},
"rotation":{"operation":"preserve","x":null,"y":null,"z":null},
"field_of_view_deg":{"operation":"preserve","value":null},
"focal_length_mm":{"operation":"preserve","value":null},
"aperture_fstop":{"operation":"preserve","value":null},"focus_mode":"preserve",
"focus_distance_cm":{"operation":"preserve","value":null},
"exposure_compensation_enabled":null,
"exposure_compensation_ev":{"operation":"preserve","value":null},
"rationale":"...","missing_capabilities":[]}
For unresolved, keep every edit preserved/null and provide at least one missing capability.
Do not output Unreal commands, code, Markdown, or prose outside the JSON.
"""

FORMAT_RETRY_PROMPT = """The previous CameraEditIntent was rejected:
{validation_error}

Return one corrected complete JSON object with every top-level field. If unresolved, keep every
edit preserved/null and include at least one concrete missing capability. No Markdown or commentary.
"""

MAX_LOCATION_CM = 10_000_000.0
MAX_LOCATION_DELTA_CM = 1_000_000.0
MAX_ROTATION_EDIT_DEG = 3_600.0
MIN_FOV_DEG = 5.0
MAX_FOV_DEG = 170.0
MAX_FOCUS_DISTANCE_CM = 10_000_000.0
MIN_EXPOSURE_EV = -15.0
MAX_EXPOSURE_EV = 15.0
MAX_MULTIPLIER = 100.0
EPSILON = 1e-6


class StructuredCameraClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict,
    ) -> StructuredResponse: ...


class CameraProposalError(RuntimeError):
    """Raised when a camera request or model response is unsafe or invalid."""

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
class CameraProposalResult:
    proposal: AssistantCameraProposal
    response: StructuredResponse
    attempt_count: int


def load_camera_context(path: str | Path) -> UnrealCameraContext:
    try:
        return UnrealCameraContext.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise CameraProposalError(f"invalid Unreal camera context: {exc}") from exc


def _normalize_degrees(value: float) -> float:
    normalized = (value + 180.0) % 360.0 - 180.0
    return 180.0 if math.isclose(normalized, -180.0, abs_tol=EPSILON) else normalized


def _apply_transform_axes(
    before: Vector3,
    edit: TransformAxisEdit,
    *,
    property_name: str,
) -> Vector3:
    if edit.operation == "preserve":
        return before
    if edit.operation not in {"set", "add"}:
        raise CameraProposalError(f"camera {property_name} supports only set or add")
    values = before.model_dump()
    for axis in ("x", "y", "z"):
        requested = getattr(edit, axis)
        if requested is None:
            continue
        original = float(getattr(before, axis))
        if property_name == "location":
            if edit.operation == "add" and abs(requested) > MAX_LOCATION_DELTA_CM:
                raise CameraProposalError(
                    f"camera location delta on {axis} exceeds {MAX_LOCATION_DELTA_CM:g} cm"
                )
            updated = requested if edit.operation == "set" else original + requested
            if abs(updated) > MAX_LOCATION_CM:
                raise CameraProposalError(
                    f"camera location on {axis} exceeds {MAX_LOCATION_CM:g} cm"
                )
        else:
            if abs(requested) > MAX_ROTATION_EDIT_DEG:
                raise CameraProposalError(
                    f"camera rotation edit on {axis} exceeds {MAX_ROTATION_EDIT_DEG:g} degrees"
                )
            updated = _normalize_degrees(
                requested if edit.operation == "set" else original + requested
            )
        values[axis] = updated
    return Vector3.model_validate(values)


def _apply_scalar(before: float, edit: CameraScalarEdit, *, name: str) -> float:
    if edit.operation == "preserve":
        return before
    assert edit.value is not None
    if edit.operation == "set":
        return edit.value
    if edit.operation == "add":
        return before + edit.value
    if not 0.0 <= edit.value <= MAX_MULTIPLIER:
        raise CameraProposalError(
            f"{name} multiplier must be between 0 and {MAX_MULTIPLIER:g}"
        )
    return before * edit.value


def _different(before: object, after: object) -> bool:
    if isinstance(before, float) and isinstance(after, float):
        return not math.isclose(before, after, rel_tol=0.0, abs_tol=EPSILON)
    return before != after


def compile_camera_intent(
    context: UnrealCameraContext,
    intent: CameraEditIntent,
) -> tuple[EditorCameraSnapshot, list[CameraPropertyChange]]:
    if intent.outcome != "proposed":
        raise CameraProposalError("cannot compile an unresolved camera intent")
    if not context.is_editable or context.is_locked:
        raise CameraProposalError("captured camera Actor is not editable or is locked")

    cine = context.camera_kind == "cine_camera"
    if cine and intent.field_of_view_deg.operation != "preserve":
        raise CameraProposalError("Cine Cameras use focal length instead of direct FOV")
    if not cine and intent.focal_length_mm.operation != "preserve":
        raise CameraProposalError("standard Cameras use FOV instead of focal length")
    if intent.exposure_compensation_ev.operation == "multiply":
        raise CameraProposalError("exposure compensation does not support multiply")
    if (
        intent.exposure_compensation_ev.operation != "preserve"
        and intent.exposure_compensation_enabled is False
    ):
        raise CameraProposalError(
            "an exposure compensation edit cannot explicitly disable its override"
        )
    if intent.focus_distance_cm.operation != "preserve" and intent.focus_mode == "disabled":
        raise CameraProposalError("a focus-distance edit cannot explicitly disable focus")

    needs_post_process = any((
        intent.exposure_compensation_enabled is not None,
        intent.exposure_compensation_ev.operation != "preserve",
        intent.focus_mode != "preserve",
        intent.focus_distance_cm.operation != "preserve",
        not cine and intent.aperture_fstop.operation != "preserve",
    ))
    if needs_post_process and context.camera.post_process_blend_weight <= EPSILON:
        raise CameraProposalError(
            "camera Post Process blend weight is zero; the requested exposure or focus edit would be inactive"
        )

    before = context.camera
    values = before.model_dump(mode="python")
    values["location_cm"] = _apply_transform_axes(
        before.location_cm, intent.location, property_name="location"
    )
    values["rotation_deg"] = _apply_transform_axes(
        before.rotation_deg, intent.rotation, property_name="rotation"
    )
    if not cine:
        assert before.field_of_view_deg is not None
        fov = _apply_scalar(
            before.field_of_view_deg,
            intent.field_of_view_deg,
            name="field of view",
        )
        if not MIN_FOV_DEG <= fov <= MAX_FOV_DEG:
            raise CameraProposalError(
                f"field of view must be between {MIN_FOV_DEG:g} and {MAX_FOV_DEG:g} degrees"
            )
        values["field_of_view_deg"] = fov
    else:
        assert before.focal_length_mm is not None
        focal = _apply_scalar(
            before.focal_length_mm,
            intent.focal_length_mm,
            name="focal length",
        )
        assert context.min_focal_length_mm is not None
        assert context.max_focal_length_mm is not None
        if not context.min_focal_length_mm <= focal <= context.max_focal_length_mm:
            raise CameraProposalError(
                "focal length must remain within the captured Cine lens bounds "
                f"{context.min_focal_length_mm:g}-{context.max_focal_length_mm:g} mm"
            )
        values["focal_length_mm"] = focal

    aperture = _apply_scalar(
        before.aperture_fstop,
        intent.aperture_fstop,
        name="aperture",
    )
    if not context.min_aperture_fstop <= aperture <= context.max_aperture_fstop:
        raise CameraProposalError(
            "aperture must remain within the captured lens bounds "
            f"f/{context.min_aperture_fstop:g}-f/{context.max_aperture_fstop:g}"
        )
    values["aperture_fstop"] = aperture

    focus_distance = _apply_scalar(
        before.focus_distance_cm,
        intent.focus_distance_cm,
        name="focus distance",
    )
    if not context.minimum_focus_distance_cm <= focus_distance <= MAX_FOCUS_DISTANCE_CM:
        raise CameraProposalError(
            "focus distance must be between the captured lens minimum "
            f"{context.minimum_focus_distance_cm:g} cm and {MAX_FOCUS_DISTANCE_CM:g} cm"
        )
    values["focus_distance_cm"] = focus_distance
    if intent.focus_mode != "preserve":
        values["focus_mode"] = (
            intent.focus_mode
            if cine or intent.focus_mode == "manual"
            else "project_default"
        )
    elif intent.focus_distance_cm.operation != "preserve":
        values["focus_mode"] = "manual"
    elif not cine and intent.aperture_fstop.operation != "preserve":
        values["focus_mode"] = "manual"

    exposure = _apply_scalar(
        before.exposure_compensation_ev,
        intent.exposure_compensation_ev,
        name="exposure compensation",
    )
    if not MIN_EXPOSURE_EV <= exposure <= MAX_EXPOSURE_EV:
        raise CameraProposalError(
            f"exposure compensation must be between {MIN_EXPOSURE_EV:g} and {MAX_EXPOSURE_EV:g} EV"
        )
    values["exposure_compensation_ev"] = exposure
    if intent.exposure_compensation_enabled is not None:
        values["exposure_compensation_enabled"] = intent.exposure_compensation_enabled
    elif intent.exposure_compensation_ev.operation != "preserve":
        values["exposure_compensation_enabled"] = True

    after = EditorCameraSnapshot.model_validate(values)
    operations = {
        "location": intent.location.operation,
        "rotation": intent.rotation.operation,
        "field_of_view_deg": intent.field_of_view_deg.operation,
        "focal_length_mm": intent.focal_length_mm.operation,
        "aperture_fstop": intent.aperture_fstop.operation,
        "focus_mode": "set",
        "focus_distance_cm": intent.focus_distance_cm.operation,
        "exposure_compensation_enabled": "set",
        "exposure_compensation_ev": intent.exposure_compensation_ev.operation,
    }
    attributes = {
        "location": "location_cm",
        "rotation": "rotation_deg",
    }
    changes = []
    for property_name in (
        "location",
        "rotation",
        "field_of_view_deg",
        "focal_length_mm",
        "aperture_fstop",
        "focus_mode",
        "focus_distance_cm",
        "exposure_compensation_enabled",
        "exposure_compensation_ev",
    ):
        attribute = attributes.get(property_name, property_name)
        if _different(getattr(before, attribute), getattr(after, attribute)):
            operation = operations[property_name]
            if operation == "preserve":
                operation = "set"
            changes.append(CameraPropertyChange(
                property=property_name,
                operation=operation,
            ))
    if not changes:
        raise CameraProposalError("camera request produces no observable change")
    return after, changes


def _parse_intent(response: StructuredResponse) -> CameraEditIntent:
    return CameraEditIntent.model_validate_json(response.content)


def propose_camera_change(
    *,
    prompt: str,
    context: UnrealCameraContext,
    client: StructuredCameraClient,
    model: str,
    proposal_id: str,
) -> CameraProposalResult:
    request = prompt.strip()
    if not request:
        raise CameraProposalError("camera request cannot be empty")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Editor-captured camera evidence and lens bounds (read-only):\n"
                f"{context.model_dump_json(indent=2)}\n\nUser request:\n{request}"
            ),
        },
    ]
    schema = ollama_model_schema(CameraEditIntent)
    response = client.chat_structured(
        model=model,
        messages=messages,
        json_schema=schema,
    )
    attempt_count = 1
    try:
        intent = _parse_intent(response)
        after, changes = (
            compile_camera_intent(context, intent)
            if intent.outcome == "proposed"
            else (None, [])
        )
    except (ValidationError, CameraProposalError) as first_error:
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
                compile_camera_intent(context, intent)
                if intent.outcome == "proposed"
                else (None, [])
            )
        except (ValidationError, CameraProposalError) as exc:
            raise CameraProposalError(
                f"model returned an unsafe camera intent after one retry: {exc}",
                response=response,
                attempt_count=attempt_count,
            ) from exc

    proposal = AssistantCameraProposal(
        proposal_id=proposal_id,
        status=intent.outcome,
        request=request,
        target=context,
        proposed_by=ModelIdentity(provider="ollama", model=response.model),
        before=context.camera,
        after=after,
        changes=changes,
        rationale=intent.rationale,
        missing_capabilities=intent.missing_capabilities,
    )
    return CameraProposalResult(
        proposal=proposal,
        response=response,
        attempt_count=attempt_count,
    )
