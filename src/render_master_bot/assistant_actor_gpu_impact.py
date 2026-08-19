"""Evidence-constrained reviews of selected-Actor GPU A/B experiments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.contracts import (
    ActorGpuImpactReviewIntent,
    AssistantActorGpuImpactReport,
    ModelIdentity,
    UnrealActorGpuImpactExperiment,
)
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema


SYSTEM_PROMPT = """You review one host-controlled sequential Unreal Editor A/B
experiment. The baseline kept one selected Actor visible. The variant temporarily hid only
that Actor in the PIE/SIE runtime world, then the host restored its original state. Return
exactly one ActorGpuImpactReviewIntent JSON object matching the supplied schema.

You may cite only exact delta_id values from available_deltas. Positive
baseline_minus_variant_ms_per_second means the measured queue-local inclusive scope became
smaller while the Actor was hidden. Describe that as an association or impact candidate,
never as proof that the Actor directly caused a render pass, draw call, shader, material, or
asset cost. The captures are sequential, single-trial, nested inclusive evidence and can be
affected by workload drift, occlusion, temporal rendering, streaming, and scene-wide side
effects. Do not sum scope totals. Do not invent settings or measurements. Recommend only a
bounded repeat or follow-up measurement. If the available deltas cannot answer the request,
return unresolved with no findings and name the missing capability."""


FORMAT_RETRY_PROMPT = """Your prior response failed deterministic validation:
{validation_error}

Return a corrected ActorGpuImpactReviewIntent JSON object. Cite only delta_id values in
available_deltas. A complete result has findings and no capability gaps. An unresolved result
has no primary delta or findings and at least one capability gap. Do not claim direct Actor,
asset, material, shader, or draw-call causation."""


class StructuredActorGpuImpactClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
    ) -> StructuredResponse: ...


class ActorGpuImpactReviewError(RuntimeError):
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
class ActorGpuImpactReviewResult:
    report: AssistantActorGpuImpactReport
    response: StructuredResponse
    attempt_count: int
    recovery_reason: str | None = None


def load_actor_gpu_impact_experiment(
    path: str | Path,
) -> UnrealActorGpuImpactExperiment:
    try:
        return UnrealActorGpuImpactExperiment.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise ActorGpuImpactReviewError(
            f"invalid Actor GPU impact experiment: {exc}"
        ) from exc


def hash_actor_gpu_impact_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical_experiment_sha256(
    experiment: UnrealActorGpuImpactExperiment,
) -> str:
    return hashlib.sha256(experiment.model_dump_json().encode("utf-8")).hexdigest()


def _model_evidence_view(experiment: UnrealActorGpuImpactExperiment) -> dict[str, Any]:
    return {
        "experiment_id": experiment.experiment_id,
        "method": experiment.method,
        "target": {
            "actor_label": experiment.target.actor_label,
            "actor_class": experiment.target.actor_class,
            "primitive_component_count": experiment.target.primitive_component_count,
        },
        "capture_context": {
            "capture_mode": experiment.baseline.capture_mode,
            "captured_duration_seconds": {
                "baseline": experiment.baseline.captured_duration_seconds,
                "actor_hidden": experiment.variant.captured_duration_seconds,
            },
            "viewport_px": [
                experiment.baseline.viewport_width_px,
                experiment.baseline.viewport_height_px,
            ],
            "gpu_name": experiment.baseline.gpu_name,
            "variant_warmup_seconds": experiment.variant_warmup_seconds,
            "trial_count": experiment.trial_count,
            "repeatability": experiment.repeatability,
        },
        "available_deltas": [
            {
                "delta_id": delta.delta_id,
                "queue_name": delta.queue_name,
                "scope_name": delta.scope_name,
                "baseline_total_ms_per_second": delta.baseline_total_ms_per_second,
                "actor_hidden_total_ms_per_second": delta.variant_total_ms_per_second,
                "baseline_minus_hidden_ms_per_second": (
                    delta.baseline_minus_variant_ms_per_second
                ),
                "relative_reduction_percent": delta.relative_reduction_percent,
                "direction_when_hidden": delta.direction_when_hidden,
                "baseline_instances_per_second": delta.baseline_instances_per_second,
                "actor_hidden_instances_per_second": delta.variant_instances_per_second,
            }
            for delta in experiment.deltas
        ],
        "unmatched_scope_counts": {
            "baseline": len(experiment.unmatched_baseline_scope_ids),
            "actor_hidden": len(experiment.unmatched_variant_scope_ids),
        },
        "measurement_notes": experiment.measurement_notes,
    }


def _parse_intent(response: StructuredResponse) -> ActorGpuImpactReviewIntent:
    return ActorGpuImpactReviewIntent.model_validate_json(response.content)


def _validate_intent(
    experiment: UnrealActorGpuImpactExperiment,
    intent: ActorGpuImpactReviewIntent,
) -> None:
    known_delta_ids = {delta.delta_id for delta in experiment.deltas}
    if intent.primary_delta_id is not None and intent.primary_delta_id not in known_delta_ids:
        raise ActorGpuImpactReviewError(
            f"primary delta {intent.primary_delta_id} is not in the experiment"
        )
    for finding in intent.findings:
        unknown = set(finding.evidence_delta_ids) - known_delta_ids
        if unknown:
            raise ActorGpuImpactReviewError(
                "Actor GPU finding cites unavailable delta IDs: "
                + ", ".join(sorted(unknown))
            )


def review_actor_gpu_impact(
    *,
    prompt: str,
    experiment: UnrealActorGpuImpactExperiment,
    client: StructuredActorGpuImpactClient,
    model: str,
    report_id: str,
    source_file_sha256: str | None = None,
) -> ActorGpuImpactReviewResult:
    request = prompt.strip()
    if not request:
        raise ActorGpuImpactReviewError("Actor GPU impact request cannot be empty")
    evidence = json.dumps(_model_evidence_view(experiment), indent=2)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Host-validated selected-Actor GPU A/B evidence (read-only). "
                "Cite only exact delta_id values from available_deltas:\n"
                f"{evidence}\n\nUser request:\n{request}"
            ),
        },
    ]
    schema = ollama_model_schema(ActorGpuImpactReviewIntent)
    response = client.chat_structured(
        model=model,
        messages=messages,
        json_schema=schema,
    )
    attempt_count = 1
    recovery_reason = None
    try:
        intent = _parse_intent(response)
        _validate_intent(experiment, intent)
    except (ValidationError, ActorGpuImpactReviewError) as first_error:
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
            _validate_intent(experiment, intent)
        except (ValidationError, ActorGpuImpactReviewError) as exc:
            raise ActorGpuImpactReviewError(
                f"model returned an unsafe Actor GPU review after one retry: {exc}",
                response=response,
                attempt_count=attempt_count,
            ) from exc

    report = AssistantActorGpuImpactReport(
        report_id=report_id,
        status=intent.outcome,
        request=request,
        experiment=experiment,
        experiment_sha256=_canonical_experiment_sha256(experiment),
        experiment_file_sha256=(
            source_file_sha256 or _canonical_experiment_sha256(experiment)
        ),
        analyzed_by=ModelIdentity(provider="ollama", model=response.model),
        summary=intent.summary,
        primary_delta_id=intent.primary_delta_id,
        findings=intent.findings,
        missing_capabilities=intent.missing_capabilities,
        modifies_editor_scene=False,
    )
    return ActorGpuImpactReviewResult(
        report=report,
        response=response,
        attempt_count=attempt_count,
        recovery_reason=recovery_reason,
    )
