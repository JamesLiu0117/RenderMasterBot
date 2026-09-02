"""Evidence-constrained reviews of repeatable selected-Actor GPU studies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.contracts import (
    ActorGpuImpactStudyAggregate,
    ActorGpuImpactStudyReviewIntent,
    AssistantActorGpuImpactStudyReport,
    ModelIdentity,
    UnrealActorGpuImpactStudyCaptureSet,
    build_actor_gpu_impact_study_aggregates,
)
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema


SYSTEM_PROMPT = """You review a host-controlled repeatable Unreal Editor GPU study for
one selected Actor. The host recorded two Actor-visible A/A noise-control captures and
three counterbalanced visible/hidden pairs in AB, BA, AB order. It restored the runtime
Actor before analysis and deterministically computed every aggregate from raw trace scopes.
Return exactly one ActorGpuImpactStudyReviewIntent JSON object matching the supplied schema.

You may cite only exact aggregate_id values from available_aggregates. A stable_candidate
means at least two of three paired deltas share a direction and the absolute median delta
exceeds the A/A control difference. It remains an association, not proof that the Actor
owns or directly caused a pass, draw call, shader, material, or asset cost. noise_limited
and inconsistent_direction results must be described as uncertain. GPU scope timings are
nested and inclusive; do not add them together. Do not recompute, replace, or contradict
host statistics. Do not invent settings or measurements. Recommend only bounded repeats or
follow-up measurements. If the aggregates cannot answer the request, return unresolved with
no findings and name the missing capability."""


FORMAT_RETRY_PROMPT = """Your prior response failed deterministic validation:
{validation_error}

Return a corrected ActorGpuImpactStudyReviewIntent JSON object. Cite only aggregate_id values
from available_aggregates. A complete result has findings and no capability gaps. An
unresolved result has no primary aggregate or findings and at least one capability gap.
Never claim direct Actor, pass, draw-call, shader, material, or asset causation."""


class StructuredActorGpuImpactStudyClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> StructuredResponse: ...


class ActorGpuImpactStudyReviewError(RuntimeError):
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
class ActorGpuImpactStudyReviewResult:
    report: AssistantActorGpuImpactStudyReport
    response: StructuredResponse
    attempt_count: int
    recovery_reason: str | None = None


def load_actor_gpu_impact_study_capture_set(
    path: str | Path,
) -> UnrealActorGpuImpactStudyCaptureSet:
    try:
        return UnrealActorGpuImpactStudyCaptureSet.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise ActorGpuImpactStudyReviewError(
            f"invalid Actor GPU impact study capture set: {exc}"
        ) from exc


def hash_actor_gpu_impact_study_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical_capture_set_sha256(
    capture_set: UnrealActorGpuImpactStudyCaptureSet,
) -> str:
    return hashlib.sha256(capture_set.model_dump_json().encode("utf-8")).hexdigest()


def _model_evidence_view(
    capture_set: UnrealActorGpuImpactStudyCaptureSet,
    aggregates: list[ActorGpuImpactStudyAggregate],
) -> dict[str, Any]:
    return {
        "study_id": capture_set.study_id,
        "method": capture_set.method,
        "schedule": capture_set.schedule,
        "target": {
            "actor_label": capture_set.target.actor_label,
            "actor_class": capture_set.target.actor_class,
            "primitive_component_count": capture_set.target.primitive_component_count,
        },
        "capture_context": {
            "capture_mode": capture_set.noise_control_visible[0].capture_mode,
            "capture_count": len(capture_set.sequence_capture_ids),
            "requested_capture_duration_seconds": (
                capture_set.requested_capture_duration_seconds
            ),
            "warmup_seconds": capture_set.warmup_seconds,
            "trial_count": capture_set.trial_count,
            "repeatability": capture_set.repeatability,
            "gpu_name": capture_set.noise_control_visible[0].gpu_name,
        },
        "available_aggregates": [
            {
                "aggregate_id": item.aggregate_id,
                "queue_name": item.queue_name,
                "scope_name": item.scope_name,
                "classification": item.classification,
                "median_visible_total_ms_per_second": (
                    item.median_visible_total_ms_per_second
                ),
                "median_hidden_total_ms_per_second": (
                    item.median_hidden_total_ms_per_second
                ),
                "median_baseline_minus_hidden_ms_per_second": (
                    item.median_baseline_minus_hidden_ms_per_second
                ),
                "delta_mad_ms_per_second": item.delta_mad_ms_per_second,
                "control_noise_abs_ms_per_second": (
                    item.control_noise_abs_ms_per_second
                ),
                "signal_excess_over_control_noise_ms_per_second": (
                    item.signal_excess_over_control_noise_ms_per_second
                ),
                "relative_reduction_percent": item.relative_reduction_percent,
                "direction_votes": {
                    "decreased": item.decreased_vote_count,
                    "increased": item.increased_vote_count,
                    "unchanged": item.unchanged_vote_count,
                },
                "dominant_direction_when_hidden": (
                    item.dominant_direction_when_hidden
                ),
            }
            for item in aggregates
        ],
        "measurement_notes": capture_set.measurement_notes,
    }


def _parse_intent(response: StructuredResponse) -> ActorGpuImpactStudyReviewIntent:
    return ActorGpuImpactStudyReviewIntent.model_validate_json(response.content)


def _validate_intent(
    aggregates: list[ActorGpuImpactStudyAggregate],
    intent: ActorGpuImpactStudyReviewIntent,
) -> None:
    aggregates_by_id = {item.aggregate_id: item for item in aggregates}
    known_ids = set(aggregates_by_id)
    if intent.outcome == "review_complete" and not known_ids:
        raise ActorGpuImpactStudyReviewError(
            "a complete Actor GPU study review requires common measured aggregates"
        )
    if (
        intent.primary_aggregate_id is not None
        and intent.primary_aggregate_id not in known_ids
    ):
        raise ActorGpuImpactStudyReviewError(
            f"primary aggregate {intent.primary_aggregate_id} is unavailable"
        )
    for finding in intent.findings:
        unknown = set(finding.evidence_aggregate_ids) - known_ids
        if unknown:
            raise ActorGpuImpactStudyReviewError(
                "Actor GPU study finding cites unavailable aggregate IDs: "
                + ", ".join(sorted(unknown))
            )
        if finding.category in {
            "stable_candidate",
            "noise_limited",
            "inconsistent_direction",
        }:
            cited_classifications = {
                aggregates_by_id[aggregate_id].classification
                for aggregate_id in finding.evidence_aggregate_ids
            }
            if finding.category not in cited_classifications:
                raise ActorGpuImpactStudyReviewError(
                    "Actor GPU study finding category contradicts its cited "
                    "host-computed aggregate classification"
                )


def review_actor_gpu_impact_study(
    *,
    prompt: str,
    capture_set: UnrealActorGpuImpactStudyCaptureSet,
    client: StructuredActorGpuImpactStudyClient,
    model: str,
    report_id: str,
    source_file_sha256: str | None = None,
) -> ActorGpuImpactStudyReviewResult:
    request = prompt.strip()
    if not request:
        raise ActorGpuImpactStudyReviewError(
            "Actor GPU impact study request cannot be empty"
        )
    aggregates = build_actor_gpu_impact_study_aggregates(capture_set)
    evidence = json.dumps(
        _model_evidence_view(capture_set, aggregates), indent=2
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Host-validated repeatable selected-Actor GPU evidence (read-only). "
                "Cite only aggregate_id values from available_aggregates:\n"
                f"{evidence}\n\nUser request:\n{request}"
            ),
        },
    ]
    schema = ollama_model_schema(ActorGpuImpactStudyReviewIntent)
    response = client.chat_structured(
        model=model, messages=messages, json_schema=schema
    )
    attempt_count = 1
    recovery_reason = None
    try:
        intent = _parse_intent(response)
        _validate_intent(aggregates, intent)
    except (ValidationError, ActorGpuImpactStudyReviewError) as first_error:
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
            intent = _parse_intent(response)
            _validate_intent(aggregates, intent)
        except (ValidationError, ActorGpuImpactStudyReviewError) as exc:
            raise ActorGpuImpactStudyReviewError(
                f"model returned an unsafe Actor GPU study review after one retry: {exc}",
                response=response,
                attempt_count=attempt_count,
            ) from exc

    report = AssistantActorGpuImpactStudyReport(
        report_id=report_id,
        status=intent.outcome,
        request=request,
        capture_set=capture_set,
        capture_set_sha256=_canonical_capture_set_sha256(capture_set),
        capture_set_file_sha256=(
            source_file_sha256 or _canonical_capture_set_sha256(capture_set)
        ),
        analyzed_by=ModelIdentity(provider="ollama", model=response.model),
        aggregates=aggregates,
        summary=intent.summary,
        primary_aggregate_id=intent.primary_aggregate_id,
        findings=intent.findings,
        missing_capabilities=intent.missing_capabilities,
        modifies_editor_scene=False,
    )
    return ActorGpuImpactStudyReviewResult(
        report=report,
        response=response,
        attempt_count=attempt_count,
        recovery_reason=recovery_reason,
    )
