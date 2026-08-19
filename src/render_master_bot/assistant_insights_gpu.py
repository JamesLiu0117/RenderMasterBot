"""Evidence-constrained reviews of Unreal Insights GPU scope captures."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.contracts import (
    AssistantInsightsGpuReport,
    InsightsGpuReviewIntent,
    ModelIdentity,
    UnrealInsightsGpuCapture,
)
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema


SYSTEM_PROMPT = """You review one host-captured and Unreal-TraceServices-parsed GPU
scope capture. Return exactly one InsightsGpuReviewIntent JSON object matching the supplied
schema. The host owns every scope name and numeric measurement. Never invent a scope, timing,
queue, Actor, asset, material, shader, render setting, or causal explanation.

Every finding must cite only scope_id values present in available_scopes. A primary_scope_id must
also be one of those IDs. Interpret total_inclusive_ms as accumulated inclusive scope duration over
the complete capture, not as one-frame cost. GPU scopes are nested, so their totals can overlap and
must not be added together. A high total can come from frequent cheap instances, a few expensive
instances, or both; use instance_count, mean_inclusive_ms, and max_inclusive_ms to distinguish them.

The trace came from Unreal Editor PIE/SIE. It does not prove packaged-build performance. A GPU
scope identifies render work but does not identify the Actor, asset, material, shader, or setting
that caused the work. Recommendations must be diagnostic next steps or bounded experiments, not
claims that a specific content change is already justified. Never imply that an Editor change was
executed.

Use outcome=review_complete for a bounded scope-hotspot review supported by this evidence. Use
outcome=unresolved only if the user's requested conclusion requires absent evidence, such as
per-Actor, per-asset, per-material, per-shader, per-draw attribution, packaged-build parity, or a
representative comparison trace. Unresolved output must contain no findings, no primary_scope_id,
and at least one concrete missing capability. Always include every top-level field. Output JSON
only; no Markdown, commands, or commentary.
"""


FORMAT_RETRY_PROMPT = """The previous InsightsGpuReviewIntent was rejected:
{validation_error}

Return one corrected complete JSON object grounded only in available_scopes. If the requested
conclusion cannot be supported, return unresolved with no findings, no primary_scope_id, and at
least one concrete missing capability. No Markdown or commentary.
"""


class StructuredInsightsGpuClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict,
    ) -> StructuredResponse: ...


class InsightsGpuReviewError(RuntimeError):
    """Raised when trace evidence or a model response is unsafe."""

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
class InsightsGpuReviewResult:
    report: AssistantInsightsGpuReport
    response: StructuredResponse
    attempt_count: int
    recovery_reason: str | None = None


def load_insights_gpu_capture(path: str | Path) -> UnrealInsightsGpuCapture:
    try:
        return UnrealInsightsGpuCapture.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise InsightsGpuReviewError(
            f"invalid Unreal Insights GPU capture: {exc}"
        ) from exc


def _canonical_capture_sha256(capture: UnrealInsightsGpuCapture) -> str:
    canonical = capture.model_dump_json().encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def hash_insights_gpu_capture_file(path: str | Path) -> str:
    """Hash the exact host-written capture artifact consumed by the reviewer."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _model_evidence_view(capture: UnrealInsightsGpuCapture) -> dict[str, Any]:
    queue_names = {queue.queue_id: queue.display_name for queue in capture.queues}
    return {
        "capture_id": capture.capture_id,
        "capture_mode": capture.capture_mode,
        "captured_duration_seconds": capture.captured_duration_seconds,
        "viewport_width_px": capture.viewport_width_px,
        "viewport_height_px": capture.viewport_height_px,
        "gpu_name": capture.gpu_name,
        "total_gpu_event_count": capture.total_gpu_event_count,
        "available_scopes": [
            {
                "scope_id": scope.scope_id,
                "queue_id": scope.queue_id,
                "queue_name": queue_names[scope.queue_id],
                "name": scope.name,
                "instance_count": scope.instance_count,
                "total_inclusive_ms": scope.total_inclusive_ms,
                "mean_inclusive_ms": scope.mean_inclusive_ms,
                "max_inclusive_ms": scope.max_inclusive_ms,
                "min_depth": scope.min_depth,
                "max_depth": scope.max_depth,
            }
            for scope in capture.scopes
        ],
        "measurement_notes": capture.measurement_notes,
        "unavailable_attribution": [
            "Actor",
            "asset",
            "material",
            "shader",
            "draw call",
            "packaged-build parity",
        ],
    }


def _validate_intent_against_capture(
    capture: UnrealInsightsGpuCapture,
    intent: InsightsGpuReviewIntent,
) -> None:
    known_scope_ids = {scope.scope_id for scope in capture.scopes}
    if (
        intent.primary_scope_id is not None
        and intent.primary_scope_id not in known_scope_ids
    ):
        raise InsightsGpuReviewError(
            f"primary scope {intent.primary_scope_id} is not in the capture"
        )
    for finding in intent.findings:
        unknown = set(finding.evidence_scope_ids) - known_scope_ids
        if unknown:
            raise InsightsGpuReviewError(
                "GPU finding cites unavailable scope IDs: " + ", ".join(sorted(unknown))
            )


def _parse_intent(response: StructuredResponse) -> InsightsGpuReviewIntent:
    return InsightsGpuReviewIntent.model_validate_json(response.content)


def review_insights_gpu_capture(
    *,
    prompt: str,
    capture: UnrealInsightsGpuCapture,
    client: StructuredInsightsGpuClient,
    model: str,
    report_id: str,
    source_file_sha256: str | None = None,
) -> InsightsGpuReviewResult:
    request = prompt.strip()
    if not request:
        raise InsightsGpuReviewError("GPU scope review request cannot be empty")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Unreal host-validated GPU scope review view (read-only). Cite only exact "
                "scope_id values from available_scopes:\n"
                f"{json.dumps(_model_evidence_view(capture), indent=2)}"
                f"\n\nUser request:\n{request}"
            ),
        },
    ]
    schema = ollama_model_schema(InsightsGpuReviewIntent)
    response = client.chat_structured(
        model=model,
        messages=messages,
        json_schema=schema,
    )
    attempt_count = 1
    recovery_reason = None
    try:
        intent = _parse_intent(response)
        _validate_intent_against_capture(capture, intent)
    except (ValidationError, InsightsGpuReviewError) as first_error:
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
            _validate_intent_against_capture(capture, intent)
        except (ValidationError, InsightsGpuReviewError) as exc:
            raise InsightsGpuReviewError(
                f"model returned an unsafe GPU scope review after one retry: {exc}",
                response=response,
                attempt_count=attempt_count,
            ) from exc

    report = AssistantInsightsGpuReport(
        report_id=report_id,
        status=intent.outcome,
        request=request,
        capture=capture,
        capture_sha256=_canonical_capture_sha256(capture),
        capture_file_sha256=source_file_sha256 or _canonical_capture_sha256(capture),
        analyzed_by=ModelIdentity(provider="ollama", model=response.model),
        summary=intent.summary,
        primary_scope_id=intent.primary_scope_id,
        findings=intent.findings,
        missing_capabilities=intent.missing_capabilities,
        modifies_editor_scene=False,
    )
    return InsightsGpuReviewResult(
        report=report,
        response=response,
        attempt_count=attempt_count,
        recovery_reason=recovery_reason,
    )
