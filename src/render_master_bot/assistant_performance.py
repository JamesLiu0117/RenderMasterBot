"""Evidence-backed performance reviews for selected Unreal StaticMeshActors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.contracts import (
    AssistantPerformanceProposal,
    ModelIdentity,
    PerformanceActorAction,
    PerformancePropertyChange,
    PerformanceReviewIntent,
    StaticMeshPerformanceSnapshot,
    UnrealPerformanceSelectionContext,
)
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema


SYSTEM_PROMPT = """You review measured performance evidence for an ordered selection of 1-32
Unreal StaticMeshActors. Return exactly one PerformanceReviewIntent JSON object matching the
supplied schema. Never invent an Actor path, measurement, or property. Every finding must cite
one or more evidence_fields that exist in the supplied Actor context.

Use outcome=review_only for diagnosis and recommendations that should not change the level.
Use outcome=proposed only when the user explicitly asks to change component settings and the
request can be expressed entirely as cast_shadow and/or max_draw_distance_cm. Use 0 cm to disable
distance culling; otherwise max_draw_distance_cm must be 500-1000000 cm. Actions may name only the
Actors that need a change; the host will construct complete ordered Before/After evidence for the
whole selection. Do not propose disabling shadows merely because an Actor has many triangles, and
do not invent a useful cull distance without an explicit user distance or shot-specific distance
evidence. Preserve visual quality when the user's intent is ambiguous.

Nanite, LOD generation, mesh simplification, material merging, collision changes, component Tick,
Mobility, asset edits, spawning, deletion, and saving are review-only recommendations in this
milestone. If the user requires one of those changes to be executed, use outcome=unresolved and
name the concrete missing capability. Use outcome=unresolved for runtime GPU/CPU/frame timings,
overdraw, occlusion, memory, or whole-level claims because this context contains static selected-
Actor evidence, not a runtime profiler capture.

Always include every top-level field. Example review-only shape:
{"schema_version":"0.1","outcome":"review_only","summary":"...","findings":[
{"actor_path":"exact supplied path","severity":"warning","category":"geometry",
"evidence_fields":["lod0_triangles","lod_count","nanite_enabled"],
"recommendation":"..."}],"actions":[],"missing_capabilities":[]}

Example executable shape:
{"schema_version":"0.1","outcome":"proposed","summary":"...","findings":[],
"actions":[{"actor_path":"exact supplied path","cast_shadow":false,
"max_draw_distance_cm":10000,"rationale":"..."}],"missing_capabilities":[]}

For unresolved, use empty findings/actions and at least one missing capability. Do not output
Unreal commands, code, Markdown, or prose outside the JSON.
"""


FORMAT_RETRY_PROMPT = """The previous PerformanceReviewIntent was rejected:
{validation_error}

Return one corrected complete JSON object grounded only in the supplied selection evidence. If a
safe result cannot be represented, return unresolved with empty findings/actions and at least one
missing capability. No Markdown or commentary.
"""


class StructuredPerformanceClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict,
    ) -> StructuredResponse: ...


class PerformanceProposalError(RuntimeError):
    """Raised when performance evidence or a model response is unsafe."""

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
class PerformanceProposalResult:
    proposal: AssistantPerformanceProposal
    response: StructuredResponse
    attempt_count: int


def load_performance_selection_context(
    path: str | Path,
) -> UnrealPerformanceSelectionContext:
    try:
        return UnrealPerformanceSelectionContext.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise PerformanceProposalError(
            f"invalid Unreal performance selection context: {exc}"
        ) from exc


def compile_performance_intent(
    selection: UnrealPerformanceSelectionContext,
    intent: PerformanceReviewIntent,
) -> list[PerformanceActorAction]:
    if intent.outcome != "proposed":
        return []

    actors_by_path = {actor.actor_path: actor for actor in selection.actors}
    edits_by_path = {}
    for edit in intent.actions:
        if edit.actor_path not in actors_by_path:
            raise PerformanceProposalError(
                f"performance action references an unselected Actor: {edit.actor_path}"
            )
        edits_by_path[edit.actor_path] = edit

    actions: list[PerformanceActorAction] = []
    for actor in selection.actors:
        before = actor.performance
        after_values = before.model_dump()
        changes: list[PerformancePropertyChange] = []
        edit = edits_by_path.get(actor.actor_path)
        rationale = (
            edit.rationale
            if edit is not None
            else "Preserve this selected Actor; the requested optimization does not change it."
        )
        if edit is not None and edit.cast_shadow is not None:
            after_values["cast_shadow"] = edit.cast_shadow
        if edit is not None and edit.max_draw_distance_cm is not None:
            after_values["max_draw_distance_cm"] = edit.max_draw_distance_cm
        after = StaticMeshPerformanceSnapshot.model_validate(after_values)

        if before.cast_shadow != after.cast_shadow:
            changes.append(
                PerformancePropertyChange(
                    property="cast_shadow",
                    before=before.cast_shadow,
                    after=after.cast_shadow,
                )
            )
        if before.max_draw_distance_cm != after.max_draw_distance_cm:
            changes.append(
                PerformancePropertyChange(
                    property="max_draw_distance_cm",
                    before=before.max_draw_distance_cm,
                    after=after.max_draw_distance_cm,
                )
            )
        actions.append(
            PerformanceActorAction(
                target=actor,
                before=before,
                after=after,
                changes=changes,
                rationale=rationale,
            )
        )

    if not any(action.changes for action in actions):
        raise PerformanceProposalError(
            "performance request produces no observable component change"
        )
    return actions


def _validate_intent_against_selection(
    selection: UnrealPerformanceSelectionContext,
    intent: PerformanceReviewIntent,
) -> list[PerformanceActorAction]:
    selected_paths = {actor.actor_path for actor in selection.actors}
    for finding in intent.findings:
        if finding.actor_path not in selected_paths:
            raise PerformanceProposalError(
                f"performance finding references an unselected Actor: {finding.actor_path}"
            )
    return compile_performance_intent(selection, intent)


def _parse_intent(response: StructuredResponse) -> PerformanceReviewIntent:
    return PerformanceReviewIntent.model_validate_json(response.content)


def propose_performance_review(
    *,
    prompt: str,
    selection: UnrealPerformanceSelectionContext,
    client: StructuredPerformanceClient,
    model: str,
    proposal_id: str,
) -> PerformanceProposalResult:
    request = prompt.strip()
    if not request:
        raise PerformanceProposalError("performance review request cannot be empty")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Editor-captured ordered StaticMeshActor evidence (read-only):\n"
                f"{selection.model_dump_json(indent=2)}\n\nUser request:\n{request}"
            ),
        },
    ]
    schema = ollama_model_schema(PerformanceReviewIntent)
    response = client.chat_structured(
        model=model,
        messages=messages,
        json_schema=schema,
    )
    attempt_count = 1
    try:
        intent = _parse_intent(response)
        actions = _validate_intent_against_selection(selection, intent)
    except (ValidationError, PerformanceProposalError) as first_error:
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
            actions = _validate_intent_against_selection(selection, intent)
        except (ValidationError, PerformanceProposalError) as exc:
            raise PerformanceProposalError(
                f"model returned an unsafe performance intent after one retry: {exc}",
                response=response,
                attempt_count=attempt_count,
            ) from exc

    proposal = AssistantPerformanceProposal(
        proposal_id=proposal_id,
        status=intent.outcome,
        request=request,
        selection=selection,
        proposed_by=ModelIdentity(provider="ollama", model=response.model),
        summary=intent.summary,
        findings=intent.findings,
        actions=actions,
        missing_capabilities=intent.missing_capabilities,
        modifies_editor_scene=intent.outcome == "proposed",
    )
    return PerformanceProposalResult(
        proposal=proposal,
        response=response,
        attempt_count=attempt_count,
    )
