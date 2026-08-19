"""Visual review and bounded intensity correction for an applied Unreal lighting rig."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.contracts import (
    AssistantLightingRigReviewProposal,
    EditorLightSnapshot,
    LightingRigLightAction,
    LightingRigLightSnapshot,
    LightingRigPreviewEvidence,
    LightingRigPropertyChange,
    LightingRigReviewIntent,
    ModelIdentity,
    UnrealLightingRigReviewContext,
)
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema
from render_master_bot.visual_benchmark import analyze_preview_png


MAX_PREVIEW_BYTES = 20 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
GLOBAL_FACTORS = {"too_dark": 1.2, "balanced": 1.0, "too_bright": 1.0 / 1.2}
ROLE_FACTORS = {"too_weak": 1.2, "balanced": 1.0, "too_strong": 1.0 / 1.2}
MAX_LOCAL_INTENSITY = 1_000_000_000.0

SYSTEM_PROMPT = """You review one camera-view PNG of an already applied Unreal three-point rig.
Return exactly one LightingRigReviewIntent JSON object matching the supplied schema.
Judge only visible evidence in the PNG, the user's source request, and the deterministic statistics.
The three exact lights are already assigned to Key, Fill, and Rim. Do not invent Actors or roles.
Classify only three bounded questions:
- exposure: is the visible subject too dark, balanced, or too bright overall?
- fill_balance: are visible subject shadows too weak, balanced, or too strong?
- rim_separation: is subject/background edge separation too weak, balanced, or too strong?
Use outcome=pass only when all three classifications are balanced and the requested lighting is met.
Use outcome=proposed when at least one classification needs correction.
Use outcome=unresolved with balanced classifications and a concrete missing capability when the image
cannot support a trustworthy judgment, the requested look needs movement/color/camera/material edits,
or the preview is blank-like. A deterministic underexposed-like preview requires exposure=too_dark;
an overexposed-like preview requires exposure=too_bright. Do not return numeric values or commands.
The host—not you—computes a small intensity-only correction and requires explicit approval.
Do not propose position, rotation, attenuation, cone, color, temperature, shadow, camera, or subject
changes. Do not output Markdown or prose outside the JSON.
"""

FORMAT_RETRY_PROMPT = """The previous LightingRigReviewIntent was rejected:
{validation_error}

Return one corrected complete JSON object. Preserve the categorical safety boundary. If the preview
cannot be judged or needs a forbidden edit, return outcome=unresolved with balanced classifications
and a concrete missing capability. No Markdown or commentary.
"""


class StructuredLightingReviewClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any],
        think: bool | str | None = None,
    ) -> StructuredResponse: ...


class LightingRigReviewError(RuntimeError):
    """Raised when preview evidence or a proposed correction cannot be trusted."""

    def __init__(
        self,
        message: str,
        response: StructuredResponse | None = None,
        attempt_count: int = 1,
    ):
        super().__init__(message)
        self.response = response
        self.attempt_count = attempt_count


@dataclass(frozen=True, slots=True)
class LightingRigReviewResult:
    proposal: AssistantLightingRigReviewProposal
    response: StructuredResponse
    attempt_count: int


def load_lighting_rig_review_context(
    path: str | Path,
) -> UnrealLightingRigReviewContext:
    try:
        return UnrealLightingRigReviewContext.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise LightingRigReviewError(
            f"invalid Unreal lighting-rig review context: {exc}"
        ) from exc


def load_review_preview(path: str | Path) -> bytes:
    preview_path = Path(path).expanduser().resolve()
    try:
        size = preview_path.stat().st_size
    except OSError as exc:
        raise LightingRigReviewError(f"cannot inspect lighting-rig preview: {exc}") from exc
    if size <= 0 or size > MAX_PREVIEW_BYTES:
        raise LightingRigReviewError(
            f"lighting-rig preview must be between 1 and {MAX_PREVIEW_BYTES} bytes"
        )
    try:
        image_bytes = preview_path.read_bytes()
    except OSError as exc:
        raise LightingRigReviewError(f"cannot read lighting-rig preview: {exc}") from exc
    if not image_bytes.startswith(PNG_SIGNATURE):
        raise LightingRigReviewError("lighting-rig preview is not a PNG file")
    return image_bytes


def _preview_evidence(image_bytes: bytes) -> LightingRigPreviewEvidence:
    try:
        statistics = analyze_preview_png(image_bytes)
    except Exception as exc:
        raise LightingRigReviewError(
            f"cannot extract deterministic lighting-rig preview statistics: {exc}"
        ) from exc
    return LightingRigPreviewEvidence(
        sha256=statistics.sha256,
        width_px=statistics.width_px,
        height_px=statistics.height_px,
        sampled_pixels=statistics.sampled_pixels,
        mean_luminance=statistics.mean_luminance,
        luminance_stddev=statistics.luminance_stddev,
        dark_pixel_fraction=statistics.dark_pixel_fraction,
        clipped_pixel_fraction=statistics.clipped_pixel_fraction,
        foreground_fraction=statistics.foreground_fraction,
        center_luminance=statistics.center_luminance,
        border_luminance=statistics.border_luminance,
        blank_like=statistics.blank_like,
        underexposed_like=statistics.underexposed_like,
        overexposed_like=statistics.overexposed_like,
    )


def _intent_matches_pixels(
    intent: LightingRigReviewIntent,
    preview: LightingRigPreviewEvidence,
) -> None:
    if preview.blank_like and intent.outcome != "unresolved":
        raise LightingRigReviewError("a blank-like preview must resolve as unresolved")
    if preview.underexposed_like and intent.outcome == "proposed":
        if intent.exposure != "too_dark":
            raise LightingRigReviewError(
                "an underexposed-like preview requires exposure=too_dark"
            )
    if preview.overexposed_like and intent.outcome == "proposed":
        if intent.exposure != "too_bright":
            raise LightingRigReviewError(
                "an overexposed-like preview requires exposure=too_bright"
            )
    if intent.outcome == "pass" and (
        preview.blank_like or preview.underexposed_like or preview.overexposed_like
    ):
        raise LightingRigReviewError(
            "a deterministically invalid preview cannot pass visual review"
        )


def _complete_but_inconsistent_intent(
    content: str,
) -> LightingRigReviewIntent | None:
    """Turn a complete categorical contradiction into a safe capability gap."""

    try:
        value = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    required = {
        "outcome",
        "exposure",
        "fill_balance",
        "rim_separation",
        "confidence",
        "summary",
        "rationale",
        "missing_capabilities",
    }
    allowed = required | {"schema_version"}
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or not set(value).issubset(allowed)
    ):
        return None
    if value["outcome"] not in {"pass", "proposed", "unresolved"}:
        return None
    if value["exposure"] not in {"too_dark", "balanced", "too_bright"}:
        return None
    if value["fill_balance"] not in {"too_weak", "balanced", "too_strong"}:
        return None
    if value["rim_separation"] not in {"too_weak", "balanced", "too_strong"}:
        return None
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    if not 0.0 <= float(confidence) <= 1.0:
        return None
    if not isinstance(value["summary"], str) or not value["summary"].strip():
        return None
    if not isinstance(value["rationale"], str) or not value["rationale"].strip():
        return None
    if not isinstance(value["missing_capabilities"], list) or not all(
        isinstance(item, str) for item in value["missing_capabilities"]
    ):
        return None
    return LightingRigReviewIntent(
        outcome="unresolved",
        exposure="balanced",
        fill_balance="balanced",
        rim_separation="balanced",
        confidence=float(confidence),
        summary="The visual review did not produce a consistent categorical diagnosis.",
        rationale=(
            "The local vision model returned complete, individually valid fields "
            "that still contradicted one another after the bounded retry."
        ),
        missing_capabilities=[
            "A consistent categorical lighting diagnosis is required."
        ],
    )


def compile_lighting_rig_review(
    context: UnrealLightingRigReviewContext,
    intent: LightingRigReviewIntent,
) -> list[LightingRigLightAction]:
    if intent.outcome != "proposed":
        raise LightingRigReviewError("only a proposed visual review can be compiled")

    global_factor = GLOBAL_FACTORS[intent.exposure]
    actions: list[LightingRigLightAction] = []
    for selected, assignment in zip(
        context.rig.lights,
        context.assignments,
        strict=True,
    ):
        role_factor = 1.0
        if assignment.role == "fill":
            role_factor = ROLE_FACTORS[intent.fill_balance]
        elif assignment.role == "rim":
            role_factor = ROLE_FACTORS[intent.rim_separation]
        factor = global_factor * role_factor
        before = LightingRigLightSnapshot(
            location_cm=selected.location_cm,
            light=selected.target.light,
        )
        intensity = min(
            MAX_LOCAL_INTENSITY,
            max(0.0, selected.target.light.intensity * factor),
        )
        light_values = selected.target.light.model_dump(mode="python")
        light_values["intensity"] = intensity
        after = LightingRigLightSnapshot(
            location_cm=selected.location_cm,
            light=EditorLightSnapshot.model_validate(light_values),
        )
        changes = (
            [LightingRigPropertyChange(property="intensity")]
            if before.light.intensity != after.light.intensity
            else []
        )
        actions.append(
            LightingRigLightAction(
                role=assignment.role,
                target=selected,
                before=before,
                after=after,
                changes=changes,
            )
        )
    if not any(action.changes for action in actions):
        raise LightingRigReviewError(
            "lighting-rig visual diagnosis produces no observable intensity change"
        )
    return actions


def propose_lighting_rig_review(
    *,
    request: str,
    context: UnrealLightingRigReviewContext,
    preview_path: str | Path,
    client: StructuredLightingReviewClient,
    model: str,
    proposal_id: str,
) -> LightingRigReviewResult:
    clean_request = request.strip()
    if not clean_request:
        raise LightingRigReviewError("lighting-rig review request cannot be empty")
    image_bytes = load_review_preview(preview_path)
    preview = _preview_evidence(image_bytes)
    schema = ollama_model_schema(LightingRigReviewIntent)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Review this exact applied lighting rig from its selected camera.\n"
                f"Review request:\n{clean_request}\n\n"
                "Editor-owned context:\n"
                f"{context.model_dump_json(indent=2)}\n\n"
                "Deterministic statistics for the exact PNG:\n"
                f"{preview.model_dump_json(indent=2)}\n\n"
                "Output JSON Schema:\n"
                f"{json.dumps(schema, ensure_ascii=False)}"
            ),
            "images": [base64.b64encode(image_bytes).decode("ascii")],
        },
    ]
    response = client.chat_structured(
        model=model,
        messages=messages,
        json_schema=schema,
        think=False,
    )
    attempt_count = 1

    def compile_response(
        value: StructuredResponse,
    ) -> tuple[LightingRigReviewIntent, list[LightingRigLightAction]]:
        parsed = LightingRigReviewIntent.model_validate_json(value.content)
        _intent_matches_pixels(parsed, preview)
        actions = (
            compile_lighting_rig_review(context, parsed)
            if parsed.outcome == "proposed"
            else []
        )
        return parsed, actions

    try:
        intent, actions = compile_response(response)
    except (ValidationError, LightingRigReviewError) as first_error:
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
            think=False,
        )
        attempt_count = 2
        try:
            intent, actions = compile_response(response)
        except (ValidationError, LightingRigReviewError) as exc:
            intent = _complete_but_inconsistent_intent(response.content)
            if intent is None:
                raise LightingRigReviewError(
                    "vision model returned an unsafe lighting-rig review after one retry: "
                    f"{exc}",
                    response=response,
                    attempt_count=attempt_count,
                ) from exc
            actions = []

    proposal = AssistantLightingRigReviewProposal(
        proposal_id=proposal_id,
        status=intent.outcome,
        request=clean_request,
        context=context,
        proposed_by=ModelIdentity(provider="ollama", model=response.model),
        preview=preview,
        exposure=intent.exposure,
        fill_balance=intent.fill_balance,
        rim_separation=intent.rim_separation,
        confidence=intent.confidence,
        summary=intent.summary,
        actions=actions,
        rationale=intent.rationale,
        missing_capabilities=intent.missing_capabilities,
    )
    return LightingRigReviewResult(
        proposal=proposal,
        response=response,
        attempt_count=attempt_count,
    )
