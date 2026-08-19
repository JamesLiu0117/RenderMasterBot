# Runtime performance capture

RenderMasterBot can capture one short, recomputable runtime sample from the
active Unreal Editor Play In Editor (PIE) or Simulate In Editor (SIE) session
and ask the local planning model for a read-only, evidence-cited diagnosis.

This workflow measures the complete active frame. It does not attribute cost
to one Actor, asset, material, shader, or GPU pass, and it does not modify the
Editor scene.

## Editor workflow

1. Start PIE or SIE and establish the camera, movement, effects, and workload
   that should be measured.
2. Enter a question such as `Diagnose this gameplay view against a 60 FPS
   target.`
3. Press **Capture Runtime Performance**.
4. Keep the workload representative while the host discards 30 warmup frames
   and records 120 consecutive frames.
5. Review the **Runtime Performance Capture** card. It shows the host-owned
   measurements before the model interpretation.
6. Press **Dismiss Review** when finished. There is no Apply action because the
   report is read-only.

Stopping PIE/SIE before the 120-frame sample completes fails the capture rather
than silently reviewing partial evidence.

## Measured evidence

Every raw frame records:

- total frame time from the engine's current frame delta;
- Game Thread time from `GGameThreadTime`;
- Render Thread time from `GRenderThreadTime`;
- RHI Thread time when `GRHIThreadTime` is available;
- total GPU frame cycles from `RHIGetGPUFrameCycles()` when available.

The host computes nearest-rank P50, P95, maximum, and mean values. It also
records the number and fraction of frames above the 16.67 ms default budget,
the largest available P95 component, viewport dimensions, GPU adapter name,
whole-Editor process working set, RHI streaming/non-streaming texture memory,
and reported texture-pool/dedicated-memory capacity when available.

The JSON retains all 120 raw samples. Python recomputes every distribution,
frame-budget miss, and largest-component field during validation. A summary
that does not match the raw samples is rejected before model inference.

## Interpretation boundary

The local model may cite only the enumerated fields in the validated capture.
It must treat unavailable RHI/GPU data as missing evidence. It is also told:

- the largest measured component is not automatic proof of root cause because
  CPU and GPU work can overlap;
- the process working set belongs to the complete Unreal Editor process;
- RHI texture allocations are not total GPU memory usage;
- dedicated video memory is capacity, not current usage;
- PIE/SIE and Editor overhead do not prove packaged-build performance.

Requests for per-pass GPU timings, per-Actor cost, draw calls, overdraw, shader
complexity, Unreal Insights event attribution, packaged-build parity, or a
longer benchmark resolve as `unresolved` with the missing evidence named.

For GPU scope timing, use **Capture GPU Scope Trace**. That separate workflow
preserves a `.utrace` and parses GPU queues with UE 5.7 TraceServices. Per-Actor,
per-asset, per-material, per-shader, and per-draw attribution remain outside
both capture types.

## CLI

```powershell
render-master validate examples/unreal_runtime_performance_capture.json `
  --contract unreal-runtime-performance-capture

render-master assistant-runtime-performance-review `
  --prompt "Diagnose this capture against the 60 FPS target" `
  --capture examples/unreal_runtime_performance_capture.json `
  --report-id runtime_review_001 `
  --output runtime_performance_report.json
```

The public output is `AssistantRuntimePerformanceReport`. It embeds the exact
validated capture, includes its canonical SHA-256, identifies the model, and
sets `modifies_editor_scene` permanently to `false`.
