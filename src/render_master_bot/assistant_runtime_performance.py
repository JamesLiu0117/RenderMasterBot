"""Evidence-constrained reviews of Unreal PIE/SIE runtime captures."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from render_master_bot.contracts import (
    AssistantRuntimePerformanceReport,
    ModelIdentity,
    RuntimePerformanceReviewIntent,
    UnrealRuntimePerformanceCapture,
)
from render_master_bot.ollama import StructuredResponse
from render_master_bot.schemas import ollama_model_schema


SYSTEM_PROMPT = """You review one host-measured Unreal Editor PIE/SIE runtime performance
capture. Return exactly one RuntimePerformanceReviewIntent JSON object matching the supplied
schema. The host already validated raw consecutive samples against recomputed nearest-rank
summaries, then projected exact citation keys into available_evidence. Never invent a timing,
memory value, Actor, asset, render pass, or causal explanation. Every finding must cite only exact
keys present in available_evidence and must never cite unavailable_evidence_fields.

Prioritize p95 frame pacing against target_frame_ms. The largest_measured_component is only the
largest available p95 timing component; it is not proof of root cause. CPU and GPU pipelines can
overlap. Editor overhead, PIE behavior, VSync, background compilation, and an unrepresentative
camera path can affect this short capture. A zero or unavailable RHI/GPU series is missing evidence,
not evidence of zero cost.

Any observation that compares a metric with the target or frame budget must cite target_frame_ms
plus the compared metric. Any observation that calls a metric stable, spiky, or variable must cite
that metric's p50_ms, p95_ms, and max_ms fields. Category-specific findings must cite at least one
field from that same timing or memory category.

process_working_set_mb is the whole Unreal Editor process. Texture streaming and non-streaming
memory are RHI texture allocations, not total GPU memory. dedicated_video_memory_mb is hardware
capacity, not usage. Do not claim an out-of-memory risk from capacity alone.

Use outcome=review_complete when the supplied capture is enough for a bounded frame-level review.
It is valid to return no findings when the capture is within budget and stable. Use
outcome=unresolved only when the user's requested conclusion requires evidence absent here, such as
per-pass GPU timings, per-Actor cost, draw calls, shader complexity, overdraw, Unreal Insights event
traces, packaged-build parity, or a longer representative benchmark. Unresolved output must contain
no findings and must name concrete missing capabilities.

The supplied capture is sufficient for a general frame-level review against its target budget.
Missing per-pass or per-Actor attribution is a caveat, not a reason for unresolved, unless the user
explicitly asks which pass, Actor, asset, or shader caused the measured cost.

primary_bottleneck may be game_thread, render_thread, rhi_thread, gpu, mixed, or inconclusive. Do
not select an unavailable component. Do not propose or imply that any Editor change was executed.
Always include every top-level field. Output JSON only; no Markdown, commands, or commentary.
"""


FORMAT_RETRY_PROMPT = """The previous RuntimePerformanceReviewIntent was rejected:
{validation_error}

Return one corrected complete JSON object grounded only in the supplied runtime capture. If the
requested conclusion cannot be supported, return unresolved with empty findings and at least one
missing capability. No Markdown or commentary.
"""


class StructuredRuntimePerformanceClient(Protocol):
    def chat_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        json_schema: dict,
    ) -> StructuredResponse: ...


class RuntimePerformanceReviewError(RuntimeError):
    """Raised when captured evidence or a model response is unsafe."""

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
class RuntimePerformanceReviewResult:
    report: AssistantRuntimePerformanceReport
    response: StructuredResponse
    attempt_count: int
    recovery_reason: str | None = None


def load_runtime_performance_capture(
    path: str | Path,
) -> UnrealRuntimePerformanceCapture:
    try:
        return UnrealRuntimePerformanceCapture.model_validate_json(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise RuntimePerformanceReviewError(
            f"invalid Unreal runtime performance capture: {exc}"
        ) from exc


def _canonical_capture_sha256(capture: UnrealRuntimePerformanceCapture) -> str:
    canonical = capture.model_dump_json().encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _model_evidence_view(
    capture: UnrealRuntimePerformanceCapture,
) -> dict[str, Any]:
    """Project a validated raw capture into exact citation keys for the model."""

    available: dict[str, Any] = {
        "target_frame_ms": capture.target_frame_ms,
        "frame_budget_miss_count": capture.frame_budget_miss_count,
        "frame_budget_miss_fraction": capture.frame_budget_miss_fraction,
        "largest_measured_component": capture.largest_measured_component,
        "process_working_set_mb": capture.process_working_set_mb,
        "process_peak_working_set_mb": capture.process_peak_working_set_mb,
        "texture_streaming_memory_mb": capture.texture_streaming_memory_mb,
        "texture_non_streaming_memory_mb": capture.texture_non_streaming_memory_mb,
        "viewport_width_px": capture.viewport_width_px,
        "viewport_height_px": capture.viewport_height_px,
    }
    unavailable: list[str] = []
    for name in ("frame_time", "game_thread", "render_thread", "rhi_thread", "gpu"):
        summary = getattr(capture, name)
        fields = {
            f"{name}.mean_ms": summary.mean_ms,
            f"{name}.p50_ms": summary.p50_ms,
            f"{name}.p95_ms": summary.p95_ms,
            f"{name}.max_ms": summary.max_ms,
        }
        if summary.available:
            available.update(fields)
        else:
            unavailable.extend(fields)
    for name in ("texture_pool_mb", "dedicated_video_memory_mb"):
        value = getattr(capture, name)
        if value is None:
            unavailable.append(name)
        else:
            available[name] = value
    return {
        "capture_id": capture.capture_id,
        "capture_mode": capture.capture_mode,
        "sample_count": capture.sample_count,
        "warmup_frames": capture.warmup_frames,
        "target_fps": capture.target_fps,
        "gpu_name": capture.gpu_name,
        "available_evidence": available,
        "unavailable_evidence_fields": unavailable,
        "measurement_notes": capture.measurement_notes,
    }


def _validate_intent_against_capture(
    capture: UnrealRuntimePerformanceCapture,
    intent: RuntimePerformanceReviewIntent,
) -> None:
    allowed_fields = set(_model_evidence_view(capture)["available_evidence"])
    category_prefixes = {
        "frame_pacing": ("frame_time.", "frame_budget_", "target_frame_ms"),
        "game_thread": ("game_thread.",),
        "render_thread": ("render_thread.",),
        "rhi_thread": ("rhi_thread.",),
        "gpu": ("gpu.", "largest_measured_component"),
        "memory": (
            "process_",
            "texture_",
            "dedicated_video_memory_mb",
        ),
    }
    for finding in intent.findings:
        prefixes = category_prefixes.get(finding.category)
        if prefixes is not None and not any(
            field.startswith(prefix)
            for field in finding.evidence_fields
            for prefix in prefixes
        ):
            raise RuntimePerformanceReviewError(
                f"{finding.category} finding does not cite same-category evidence"
            )
        for field in finding.evidence_fields:
            if field not in allowed_fields:
                raise RuntimePerformanceReviewError(
                    f"runtime finding cites unavailable {field} evidence"
                )
    if intent.primary_bottleneck in {"rhi_thread", "gpu"} and not getattr(
        capture, intent.primary_bottleneck
    ).available:
        raise RuntimePerformanceReviewError(
            f"primary bottleneck names unavailable {intent.primary_bottleneck} evidence"
        )


def _parse_intent(response: StructuredResponse) -> RuntimePerformanceReviewIntent:
    return RuntimePerformanceReviewIntent.model_validate_json(response.content)


def review_runtime_performance(
    *,
    prompt: str,
    capture: UnrealRuntimePerformanceCapture,
    client: StructuredRuntimePerformanceClient,
    model: str,
    report_id: str,
) -> RuntimePerformanceReviewResult:
    request = prompt.strip()
    if not request:
        raise RuntimePerformanceReviewError(
            "runtime performance review request cannot be empty"
        )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Unreal host-validated runtime review view (read-only). Cite only exact keys in "
                "available_evidence; never cite unavailable_evidence_fields:\n"
                f"{json.dumps(_model_evidence_view(capture), indent=2)}"
                f"\n\nUser request:\n{request}"
            ),
        },
    ]
    schema = ollama_model_schema(RuntimePerformanceReviewIntent)
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
    except (ValidationError, RuntimePerformanceReviewError) as first_error:
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
        except (ValidationError, RuntimePerformanceReviewError) as exc:
            raise RuntimePerformanceReviewError(
                f"model returned an unsafe runtime review after one retry: {exc}",
                response=response,
                attempt_count=attempt_count,
            ) from exc

    report = AssistantRuntimePerformanceReport(
        report_id=report_id,
        status=intent.outcome,
        request=request,
        capture=capture,
        capture_sha256=_canonical_capture_sha256(capture),
        analyzed_by=ModelIdentity(provider="ollama", model=response.model),
        summary=intent.summary,
        primary_bottleneck=intent.primary_bottleneck,
        findings=intent.findings,
        missing_capabilities=intent.missing_capabilities,
        modifies_editor_scene=False,
    )
    return RuntimePerformanceReviewResult(
        report=report,
        response=response,
        attempt_count=attempt_count,
        recovery_reason=recovery_reason,
    )
