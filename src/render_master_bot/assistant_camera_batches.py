"""Approval-gated coordinated property proposals for selected Unreal cameras."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.assistant_cameras import (
    CameraProposalError,
    compile_camera_intent,
)
from render_master_bot.contracts import (
    AssistantCameraBatchProposal,
    CameraActorAction,
    CameraEditIntent,
    ModelIdentity,
    UnrealCameraSelectionContext,
)
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema


SYSTEM_PROMPT = """You interpret one shared property-edit request for an ordered selection of
2-16 Unreal Camera or Cine Camera Actors. Return exactly one CameraEditIntent JSON object matching
the supplied schema. The same restricted operation will be computed independently from every
camera's frozen Before state. Never choose, reorder, rename, spawn, delete, or omit a camera.
Use Unreal world space: +X forward, +Y right, +Z up; location is centimeters; rotation axes are
x=roll, y=pitch, z=yaw in degrees. Location and rotation support only set or add. Prefer add for
requests such as "move every camera up" so the existing shot offsets remain coordinated.
A standard Camera uses field_of_view_deg and must preserve focal_length_mm. A Cine Camera uses
focal_length_mm and must preserve field_of_view_deg. For a mixed Camera/Cine Camera selection,
direct FOV or focal-length requests are unsupported; return outcome=unresolved instead of editing
only part of the selection. Aperture, focus, exposure, and Transform requests may apply to both
kinds when every captured bound permits the result. Never change filmback, lens presets, aspect
ratio, projection mode, Post Process blend weight, tracking-focus targets, or camera scale.
If the request requires per-camera semantic framing, look-at targets, geometry or occlusion
reasoning, different edits for named cameras, camera creation, or any unsupported setting, return
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

FORMAT_RETRY_PROMPT = """The previous shared CameraEditIntent was rejected:
{validation_error}

Return one corrected complete JSON object. It must be safe for every selected camera. If the
selection cannot support one shared edit, return unresolved with every edit preserved/null and at
least one concrete missing capability. No Markdown or commentary.
"""


class StructuredCameraBatchClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict,
    ) -> StructuredResponse: ...


class CameraBatchProposalError(RuntimeError):
    """Raised when a coordinated camera request or response is unsafe."""

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
class CameraBatchProposalResult:
    proposal: AssistantCameraBatchProposal
    response: StructuredResponse
    attempt_count: int


def load_camera_selection_context(
    path: str | Path,
) -> UnrealCameraSelectionContext:
    try:
        return UnrealCameraSelectionContext.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise CameraBatchProposalError(
            f"invalid Unreal camera selection context: {exc}"
        ) from exc


def compile_camera_batch_intent(
    selection: UnrealCameraSelectionContext,
    intent: CameraEditIntent,
) -> list[CameraActorAction]:
    if intent.outcome != "proposed":
        raise CameraBatchProposalError("cannot compile an unresolved camera batch intent")

    actions: list[CameraActorAction] = []
    try:
        for camera in selection.cameras:
            after, changes = compile_camera_intent(
                camera,
                intent,
                require_change=False,
            )
            actions.append(
                CameraActorAction(
                    target=camera,
                    before=camera.camera,
                    after=after,
                    changes=changes,
                )
            )
    except CameraProposalError as exc:
        raise CameraBatchProposalError(
            f"camera {camera.actor_name!r} cannot accept the shared edit: {exc}"
        ) from exc

    if not any(action.changes for action in actions):
        raise CameraBatchProposalError(
            "shared camera request produces no observable change"
        )
    return actions


def _parse_intent(response: StructuredResponse) -> CameraEditIntent:
    return CameraEditIntent.model_validate_json(response.content)


def propose_camera_batch_change(
    *,
    prompt: str,
    selection: UnrealCameraSelectionContext,
    client: StructuredCameraBatchClient,
    model: str,
    proposal_id: str,
) -> CameraBatchProposalResult:
    request = prompt.strip()
    if not request:
        raise CameraBatchProposalError("camera batch request cannot be empty")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Editor-captured ordered camera evidence and lens bounds (read-only):\n"
                f"{selection.model_dump_json(indent=2)}\n\nUser request:\n{request}"
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
        actions = (
            compile_camera_batch_intent(selection, intent)
            if intent.outcome == "proposed"
            else []
        )
    except (ValidationError, CameraBatchProposalError) as first_error:
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
            actions = (
                compile_camera_batch_intent(selection, intent)
                if intent.outcome == "proposed"
                else []
            )
        except (ValidationError, CameraBatchProposalError) as exc:
            raise CameraBatchProposalError(
                f"model returned an unsafe camera batch intent after one retry: {exc}",
                response=response,
                attempt_count=attempt_count,
            ) from exc

    proposal = AssistantCameraBatchProposal(
        proposal_id=proposal_id,
        status=intent.outcome,
        request=request,
        selection=selection,
        proposed_by=ModelIdentity(provider="ollama", model=response.model),
        actions=actions,
        rationale=intent.rationale,
        missing_capabilities=intent.missing_capabilities,
    )
    return CameraBatchProposalResult(
        proposal=proposal,
        response=response,
        attempt_count=attempt_count,
    )
