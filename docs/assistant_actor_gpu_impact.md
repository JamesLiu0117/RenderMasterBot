# Selected Actor GPU impact experiment

RenderMasterBot can run one controlled, read-only GPU experiment for exactly
one selected level Actor while Play In Editor (PIE) or Simulate In Editor
(SIE) is active. It records an Actor-visible baseline, temporarily hides only
the runtime-world counterpart of that Actor, warms the renderer for one second,
records a second trace, and restores the Actor before analysis begins.

The result is evidence that GPU scope timing changed *with* the Actor hidden.
It is an impact candidate, not proof that the Actor directly owns a render
pass, draw call, material, shader, or asset cost.

## Editor workflow

1. Start PIE or SIE and establish a representative, stable workload.
2. Select exactly one visible Actor that has at least one registered primitive
   component. The selection may be its Editor or runtime counterpart.
3. Enter a bounded question such as `Which measured GPU scopes changed when
   this selected Actor was hidden?`
4. Press **Measure Selected Actor GPU Impact**.
5. Keep the camera and workload stable while the Assistant records two
   five-second traces. Between them it hides only the selected runtime Actor
   and waits one second for renderer state to settle.
6. Review the **Selected Actor GPU Impact Experiment** card. Use **Open Baseline
   Trace** and **Open Actor-Hidden Trace** to inspect both authoritative files
   in Unreal Insights.
7. Press **Dismiss Review** when finished. Both traces and the JSON evidence
   remain in the local workflow folder.

The formal Editor-world Actor is never edited, transacted, or saved. The
runtime Actor's original hidden state is restored immediately after the second
capture and also on cancellation, failure, tab dismissal, Editor shutdown, or
plugin unload. A report is not written unless restoration has been verified.

## Deterministic comparison

Each trace is parsed through UE 5.7 `TraceServices`. The host compares only
queue-local scopes that appear in both Top-64 scope sets with the same queue ID
and timer name. Raw totals are normalized by the actual duration of each trace:

```text
total_ms_per_second = total_inclusive_ms / captured_duration_seconds
instances_per_second = instance_count / captured_duration_seconds
impact_delta = baseline_total_ms_per_second - hidden_total_ms_per_second
```

A positive impact delta means the inclusive scope became smaller while the
Actor was hidden. Deltas are ordered by absolute change. Scopes that appear in
only one Top-64 set are preserved as unmatched IDs; they are not silently
treated as zero. The baseline and variant must also match on project, world,
PIE/SIE mode, viewport size, GPU, and trace channels.

## Interpretation boundary

This first version is one sequential trial. Camera drift, animation, streaming,
temporal rendering, occlusion changes, and scene-wide side effects can affect
the result. GPU scopes are nested and inclusive, so their totals may overlap
and must not be summed.

The local model may cite only exact host-computed `delta_id` values. It can
describe a measured association, flag measurement risk, or recommend a bounded
repeat/follow-up experiment. It cannot claim that the Actor caused a specific
pass, draw, material, shader, asset, or packaged-build result. Requests that
need those missing links resolve as `unresolved` rather than being guessed.

## Artifacts and CLI

Each experiment preserves:

- `request.txt`
- `baseline_gpu_scope_capture.utrace`
- `baseline_insights_gpu_capture.json`
- `actor_hidden_gpu_scope_capture.utrace`
- `actor_hidden_insights_gpu_capture.json`
- `actor_gpu_impact_experiment.json`
- `actor_gpu_impact_report.json`

```powershell
render-master validate examples/unreal_actor_gpu_impact_experiment.json `
  --contract unreal-actor-gpu-impact-experiment

render-master assistant-actor-gpu-impact-review `
  --prompt "Which measured scopes changed most when this Actor was hidden?" `
  --experiment examples/unreal_actor_gpu_impact_experiment.json `
  --report-id actor_gpu_review_001 `
  --output actor_gpu_impact_report.json
```

`AssistantActorGpuImpactReport` embeds the complete validated experiment,
records both its canonical SHA-256 and the exact host-written file SHA-256,
identifies the local model, and fixes `modifies_editor_scene` to `false`.
Unreal rechecks the experiment ID, source-file hash, and every cited delta ID
before accepting the report.
