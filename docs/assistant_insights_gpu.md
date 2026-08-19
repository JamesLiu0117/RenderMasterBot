# Unreal Insights GPU scope review

RenderMasterBot can record a bounded Unreal trace from the active Play In
Editor (PIE) or Simulate In Editor (SIE) workload, parse its GPU queue timelines
with UE 5.7 `TraceServices`, preserve the original `.utrace`, and ask the local
planning model for a read-only scope-hotspot review.

This is the first trace-backed profiler loop in the Assistant. It identifies
measured GPU scopes and their accumulated inclusive time. It does not claim
per-Actor, per-asset, per-material, per-shader, or per-draw attribution.

## Editor workflow

1. Start PIE or SIE and establish the representative camera, effects, motion,
   and workload.
2. Enter a question such as `Which measured GPU scopes deserve the next
   optimization experiment?`
3. Press **Capture GPU Scope Trace**.
4. Keep the workload representative for the five-second capture.
5. Review the **Unreal Insights GPU Scope Review** card. It shows host-parsed
   scope evidence before and alongside the local-model interpretation.
6. Press **Open Trace in Unreal Insights** to inspect the authoritative raw GPU
   timelines, or **Dismiss Review** when finished.

The Assistant refuses to start while any other trace connection is active. It
never stops or replaces an externally owned Unreal Insights session. Ending
PIE/SIE before the five-second capture completes fails the operation instead of
silently reviewing a partial workload.

## Capture and parsing

The Editor records these trace channels to a local file:

- `cpu`
- `gpu`
- `frame`
- `bookmark`

The trace is written below the configured workflow root, not the source
repository. After recording, the plugin uses Unreal's own `TraceServices`
analysis service and `ITimingProfilerProvider` rather than parsing log text. It
enumerates every GPU queue with timed events, reads each queue timeline, and
aggregates queue-local scopes by timer name.

The host records, for each of the 64 highest accumulated scopes:

- stable `scope_id` and `queue_id` values;
- queue display name and scope name;
- instance count;
- total inclusive milliseconds over the complete capture;
- mean and maximum inclusive milliseconds per instance;
- minimum and maximum observed nesting depth.

The capture also records project, world, PIE/SIE mode, viewport size, GPU
adapter, requested and actual durations, queue/event counts, trace file size,
trace SHA-256, and the clean trace filename. The raw `.utrace` remains the
authoritative artifact for manual verification.

## Interpretation boundary

GPU scopes are nested. Their inclusive totals can overlap, so totals must not
be added together or treated as complete frame time. A large total can mean a
frequent cheap scope, a few expensive instances, or both; the model receives
count, mean, and maximum values to keep those cases distinct.

The model may cite only exact `scope_id` values present in the validated
capture. Python rejects unknown scope citations and retries once with the
validation error. It is explicitly prohibited from inventing an Actor, asset,
material, shader, draw call, setting, or causal explanation.

Requests such as `Which Actor caused this Lumen cost?` still resolve as
`unresolved` in this scope-only workflow because one trace cannot answer them.
For one selected Actor, the separate controlled A/B workflow can measure which
matched scopes changed when that Actor was temporarily hidden, but it reports
association rather than direct pass/draw causation. See
[assistant_actor_gpu_impact.md](assistant_actor_gpu_impact.md). PIE/SIE evidence
also does not prove packaged-build performance.

## Artifacts and CLI

Each run contains:

- `request.txt`
- `gpu_scope_capture.utrace`
- `insights_gpu_capture.json`
- `insights_gpu_report.json`

```powershell
render-master validate examples/unreal_insights_gpu_capture.json `
  --contract unreal-insights-gpu-capture

render-master assistant-insights-gpu-review `
  --prompt "Which measured scopes deserve the next experiment?" `
  --capture examples/unreal_insights_gpu_capture.json `
  --report-id insights_gpu_review_001 `
  --output insights_gpu_report.json
```

`AssistantInsightsGpuReport` embeds the exact validated capture, includes its
canonical SHA-256 and the SHA-256 of the exact host-written capture JSON,
identifies the local model, and fixes `modifies_editor_scene` to `false`.
Unreal recomputes the source-file hash before it accepts the report.
