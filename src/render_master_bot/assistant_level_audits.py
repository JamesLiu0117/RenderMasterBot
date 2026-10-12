"""Evidence-constrained whole-level static audits with bounded safe actions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.contracts import (
    AssistantLevelAuditReport,
    LevelAuditActorAction,
    LevelAuditActorAssessment,
    LevelAuditReviewIntent,
    ModelIdentity,
    PerformancePropertyChange,
    StaticMeshPerformanceSnapshot,
    UnrealLevelAuditContext,
    build_level_audit_assessments,
    build_level_audit_statistics,
)
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema


SYSTEM_PROMPT = """You review a host-captured static audit of every eligible native
StaticMeshActor in one Unreal Editor level. Return exactly one LevelAuditReviewIntent JSON
object matching the supplied schema. The host, not the model, computed every statistic,
risk_score, risk_band, reason, and assessment_id. Risk is a transparent prioritization
heuristic; it is not measured runtime cost and must never be described as proof of a
bottleneck. Cite only exact assessment_id values in available_assessments.

Use review_only for a whole-level diagnosis and prioritized next measurements. Use proposed
only when the user explicitly requests an executable Cast Shadow and/or Max Draw Distance
change that can be represented by the schema. A non-zero draw distance requires an explicit
user distance because this static audit has no shot-specific visibility evidence. Do not
disable shadows merely because an Actor is large or dense. Use no more actions than necessary.

Nanite changes, LOD generation, mesh simplification, material merging, collision changes,
Tick changes, asset edits, spawning, deletion, saving, runtime timings, direct GPU causation,
and packaged-build comparisons are unavailable in this static stage. If execution of an
unsupported operation is required, return unresolved with no primary assessment, findings,
or actions and name the concrete missing capability. Recommend repeatable Actor GPU studies
only for high/critical candidates that need runtime confirmation. Never invent an Actor,
assessment, property, measurement, or causal claim. Always include every top-level field."""


FORMAT_RETRY_PROMPT = """The prior LevelAuditReviewIntent failed deterministic validation:
{validation_error}

Return one corrected JSON object. Cite and edit only assessment_id values present in
available_assessments. Complete reviews contain no capability gaps. Unresolved reviews
contain only capability gaps. Do not contradict host statistics or claim measured runtime
cost from this static evidence."""


class StructuredLevelAuditClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> StructuredResponse: ...


class LevelAuditError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        response: StructuredResponse | None = None,
        attempt_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.response = response
        self.attempt_count = attempt_count


@dataclass(frozen=True)
class LevelAuditResult:
    report: AssistantLevelAuditReport
    response: StructuredResponse
    attempt_count: int
    recovery_reason: str | None = None


def load_level_audit_context(path: str | Path) -> UnrealLevelAuditContext:
    try:
        return UnrealLevelAuditContext.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise LevelAuditError(f"invalid Unreal level audit context: {exc}") from exc


def _canonical_context_sha256(context: UnrealLevelAuditContext) -> str:
    return hashlib.sha256(context.model_dump_json().encode("utf-8")).hexdigest()


def _model_evidence_view(
    context: UnrealLevelAuditContext,
    assessments: list[LevelAuditActorAssessment],
) -> dict[str, Any]:
    actors_by_path = {actor.actor_path: actor for actor in context.actors}
    available = []
    for assessment in assessments[:64]:
        actor = actors_by_path[assessment.actor_path]
        available.append(
            {
                "assessment_id": assessment.assessment_id,
                "actor_label": actor.actor_name,
                "actor_class": actor.actor_class,
                "mesh_path": actor.mesh_path,
                "risk_score": assessment.risk_score,
                "risk_band": assessment.risk_band,
                "evidence_fields": assessment.evidence_fields,
                "reasons": assessment.reasons,
                "recommended_next_test": assessment.recommended_next_test,
                "captured_evidence": {
                    "lod0_triangles": actor.lod0_triangles,
                    "lod_count": actor.lod_count,
                    "material_slot_count": actor.material_slot_count,
                    "nanite_enabled": actor.nanite_enabled,
                    "collision_mode": actor.collision_mode,
                    "component_mobility": actor.component_mobility,
                    "component_tick_enabled": actor.component_tick_enabled,
                    "bounds_radius_cm": actor.bounds_radius_cm,
                    "cast_shadow": actor.performance.cast_shadow,
                    "max_draw_distance_cm": (
                        actor.performance.max_draw_distance_cm
                    ),
                },
            }
        )
    return {
        "audit_id": context.audit_id,
        "project_name": context.project_name,
        "level_path": context.level_path,
        "statistics": build_level_audit_statistics(context).model_dump(
            mode="json"
        ),
        "assessment_count": len(assessments),
        "available_assessments": available,
        "evidence_limit_note": (
            "The full report preserves every assessment; the model receives the "
            "64 highest deterministic priorities."
        ),
    }


def compile_level_audit_intent(
    context: UnrealLevelAuditContext,
    assessments: list[LevelAuditActorAssessment],
    intent: LevelAuditReviewIntent,
) -> list[LevelAuditActorAction]:
    if intent.outcome != "proposed":
        return []
    actors_by_path = {actor.actor_path: actor for actor in context.actors}
    assessments_by_id = {item.assessment_id: item for item in assessments}
    actions: list[LevelAuditActorAction] = []
    for edit in intent.actions:
        assessment = assessments_by_id.get(edit.assessment_id)
        if assessment is None:
            raise LevelAuditError(
                f"level audit action references unavailable assessment {edit.assessment_id}"
            )
        actor = actors_by_path[assessment.actor_path]
        before = actor.performance
        after_values = before.model_dump()
        if edit.cast_shadow is not None:
            after_values["cast_shadow"] = edit.cast_shadow
        if edit.max_draw_distance_cm is not None:
            after_values["max_draw_distance_cm"] = edit.max_draw_distance_cm
        after = StaticMeshPerformanceSnapshot.model_validate(after_values)
        changes: list[PerformancePropertyChange] = []
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
        if not changes:
            raise LevelAuditError(
                f"level audit action {edit.assessment_id} produces no observable change"
            )
        actions.append(
            LevelAuditActorAction(
                assessment_id=edit.assessment_id,
                target=actor,
                before=before,
                after=after,
                changes=changes,
                rationale=edit.rationale,
            )
        )
    return actions


def _validate_intent(
    context: UnrealLevelAuditContext,
    assessments: list[LevelAuditActorAssessment],
    intent: LevelAuditReviewIntent,
) -> list[LevelAuditActorAction]:
    available_ids = {item.assessment_id for item in assessments[:64]}
    referenced_ids = {
        assessment_id
        for finding in intent.findings
        for assessment_id in finding.evidence_assessment_ids
    }
    referenced_ids.update(action.assessment_id for action in intent.actions)
    if intent.primary_assessment_id is not None:
        referenced_ids.add(intent.primary_assessment_id)
    unavailable = referenced_ids - available_ids
    if unavailable:
        raise LevelAuditError(
            "level audit intent references assessments not shown to the model: "
            + ", ".join(sorted(unavailable))
        )
    return compile_level_audit_intent(context, assessments, intent)


def _parse_intent_conservatively(
    content: str,
) -> tuple[LevelAuditReviewIntent, str | None]:
    raw = json.loads(content)
    recovery_reason = None
    if (
        isinstance(raw, dict)
        and raw.get("outcome") == "review_only"
        and raw.get("actions")
        and not raw.get("missing_capabilities")
    ):
        raw["actions"] = []
        recovery_reason = (
            "discarded model-authored actions because the model declared a "
            "read-only level audit"
        )
    return LevelAuditReviewIntent.model_validate(raw), recovery_reason


def build_read_only_static_triage(
    *, prompt: str, context: UnrealLevelAuditContext,
) -> LevelAuditResult:
    """Profiling candidates are deterministic evidence, not an LLM decision."""
    if not prompt.strip():
        raise LevelAuditError("level audit request cannot be empty")
    assessments = build_level_audit_assessments(context)
    report = AssistantLevelAuditReport(
        audit_id=context.audit_id, status="review_only", request=prompt.strip(),
        context=context, context_sha256=_canonical_context_sha256(context),
        statistics=build_level_audit_statistics(context), assessments=assessments,
        analyzed_by=ModelIdentity(provider="local", model="host-static-triage:v1"),
        summary="Deterministic static triage completed. Risk scores prioritize measurements; they do not prove runtime bottlenecks.",
        primary_assessment_id=assessments[0].assessment_id if assessments else None,
        modifies_editor_scene=False,
    )
    return LevelAuditResult(
        report=report,
        response=StructuredResponse(content=report.model_dump_json(), model="host-static-triage:v1"),
        attempt_count=1,
    )


def review_level_audit(
    *,
    prompt: str,
    context: UnrealLevelAuditContext,
    client: StructuredLevelAuditClient,
    model: str,
) -> LevelAuditResult:
    request = prompt.strip()
    if not request:
        raise LevelAuditError("level audit request cannot be empty")
    assessments = build_level_audit_assessments(context)
    evidence = json.dumps(
        _model_evidence_view(context, assessments), indent=2
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Host-validated whole-level static audit evidence (read-only):\n"
                f"{evidence}\n\nUser request:\n{request}"
            ),
        },
    ]
    schema = ollama_model_schema(LevelAuditReviewIntent)
    response = client.chat_structured(
        model=model, messages=messages, json_schema=schema
    )
    attempt_count = 1
    recovery_reason = None
    try:
        intent, recovery_reason = _parse_intent_conservatively(response.content)
        actions = _validate_intent(context, assessments, intent)
    except (ValidationError, LevelAuditError, ValueError) as first_error:
        recovery_reason = str(first_error)
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
            intent, conservative_recovery = _parse_intent_conservatively(
                response.content
            )
            if conservative_recovery:
                recovery_reason = (
                    f"{recovery_reason}; {conservative_recovery}"
                    if recovery_reason
                    else conservative_recovery
                )
            actions = _validate_intent(context, assessments, intent)
        except (ValidationError, LevelAuditError, ValueError) as exc:
            raise LevelAuditError(
                f"model returned an unsafe level audit after one retry: {exc}",
                response=response,
                attempt_count=attempt_count,
            ) from exc

    report = AssistantLevelAuditReport(
        audit_id=context.audit_id,
        status=intent.outcome,
        request=request,
        context=context,
        context_sha256=_canonical_context_sha256(context),
        statistics=build_level_audit_statistics(context),
        assessments=assessments,
        analyzed_by=ModelIdentity(provider="ollama", model=response.model),
        summary=intent.summary,
        primary_assessment_id=intent.primary_assessment_id,
        findings=intent.findings,
        actions=actions,
        missing_capabilities=intent.missing_capabilities,
        modifies_editor_scene=intent.outcome == "proposed",
    )
    return LevelAuditResult(
        report=report,
        response=response,
        attempt_count=attempt_count,
        recovery_reason=recovery_reason,
    )
