# Repeatable selected-Actor GPU impact study

The Unreal Assistant can run a repeatable, read-only GPU study for exactly one
selected level Actor during Play In Editor (PIE) or Simulate In Editor (SIE).
It records eight UE trace captures under a fixed schedule, restores the runtime
Actor, computes deterministic cross-capture statistics, and only then asks the
local model to interpret the evidence.

The formal Editor-world Actor is never edited, transacted, or saved.

## Fixed schedule

`A` means the runtime Actor is visible. `B` means only its PIE/SIE counterpart
is hidden in game. The schedule is fixed to `AA_AB_BA_AB`:

| Capture | Role | Runtime state |
| --- | --- | --- |
| 1 | A/A control 1 | visible |
| 2 | A/A control 2 | visible |
| 3 | trial 1 A | visible |
| 4 | trial 1 B | hidden |
| 5 | trial 2 B | hidden |
| 6 | trial 2 A | visible |
| 7 | trial 3 A | visible |
| 8 | trial 3 B | hidden |

Every capture has a one-second warmup followed by a five-second CPU/GPU/frame/
bookmark trace. AB/BA/AB counterbalancing reduces simple order bias: one trial
measures the visible condition after the hidden condition instead of always
measuring it first.

The Actor is restored immediately after the last hidden trace stops and before
its analysis begins. Restoration also runs on cancellation, trace failure,
analysis failure, model failure, dismissal, Editor shutdown, and plugin unload.
A capture set cannot be written unless all eight artifacts exist and the
original hidden state has been verified.

## Deterministic statistics

UE 5.7 `TraceServices` parses each `.utrace` independently. Python then
intersects scopes by queue ID and timer name across all eight Top-64 sets. A
scope missing from any capture does not receive an aggregate.

For each common scope the host computes:

- the absolute difference between the two visible A/A control rates;
- three paired `visible - hidden` deltas, regardless of chronological pair
  order;
- the median visible rate and median hidden rate;
- the median paired delta;
- median absolute deviation (MAD) of the three paired deltas;
- decreased, increased, and unchanged direction votes;
- absolute signal excess over the A/A control difference.

Classification is deterministic:

- `stable_candidate`: at least two of three paired deltas share a decreased or
  increased direction, and the absolute median delta exceeds A/A control noise;
- `noise_limited`: the absolute median delta does not exceed A/A control noise;
- `inconsistent_direction`: the median exceeds control noise but the three
  trials do not produce a majority decreased/increased direction.

The A/A value is a single no-change control difference, not a confidence
interval. MAD describes the three paired Actor trials. Both limitations remain
visible to the model and operator.

## Editor workflow

1. Start PIE or SIE and establish a representative, stable camera and workload.
2. Select exactly one visible Actor with at least one registered primitive
   component. An Editor or runtime counterpart may be selected.
3. Enter a question such as `Is this Actor's measured GPU impact repeatable?`
4. Press **Run Repeatable Selected Actor GPU Study**.
5. Keep the camera, effects, animation, and workload stable for approximately
   one minute while the eight captures and trace analyses complete.
6. Review the **Repeatable Selected Actor GPU Study** card. It shows the largest
   common-scope median deltas, MAD, A/A noise, direction votes, and deterministic
   classification.
7. Use **Open First A/A Control Trace** or **Open Final Hidden Trace** for quick
   Unreal Insights inspection. All eight traces remain in the workflow folder.

## Model boundary

The model receives only the host-computed aggregates, target label/type,
schedule metadata, and measurement notes. It does not receive raw Actor paths,
trace hashes, or permission to alter statistics.

It may cite only exact `aggregate_id` values. A `stable_candidate` is still an
association: hiding an Actor can change occlusion, streaming, shadows, temporal
history, and scene-wide render work. The model cannot claim direct render-pass,
draw-call, material, shader, asset, or packaged-build causation.

## Artifacts and CLI

Each run preserves the request, eight `.utrace` files, eight capture JSON files,
one capture-set JSON, and one report JSON outside the source repository.

```powershell
render-master validate examples/unreal_actor_gpu_impact_study_capture_set.json `
  --contract unreal-actor-gpu-impact-study-capture-set

render-master assistant-actor-gpu-impact-study-review `
  --prompt "Is this selected Actor's measured GPU impact repeatable?" `
  --capture-set examples/unreal_actor_gpu_impact_study_capture_set.json `
  --report-id actor_gpu_study_review_001 `
  --output actor_gpu_impact_study_report.json
```

`AssistantActorGpuImpactStudyReport` embeds all eight validated captures,
preserves canonical and exact source-file SHA-256 identities, and includes the
deterministically recomputed aggregate list. Unreal rechecks the study ID,
capture-set file hash, primary aggregate, and every cited aggregate before it
accepts the report.
