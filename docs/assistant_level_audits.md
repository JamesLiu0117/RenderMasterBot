# Full Level Audit

The Unreal Assistant can audit every eligible native `StaticMeshActor` in the
current Editor level without requiring an Actor selection. This is Stage 1 of
the automatic whole-level analysis and optimization loop: a deterministic
static funnel that decides where deeper runtime measurement should start.

## Evidence funnel

1. Unreal enumerates every exact native `StaticMeshActor` in the Editor world.
2. Editable, unlocked Actors with a valid Static Mesh and bounds are captured;
   unsupported Actors are counted as excluded rather than silently omitted.
3. The host freezes Actor/component identity, mesh, LOD0 triangles, LOD count,
   material slots, Nanite, collision, Mobility, Tick, bounds, Cast Shadow, and
   Max Draw Distance evidence.
4. Python recomputes whole-level statistics and a transparent 0-100 static
   priority score for every captured Actor.
5. The full report preserves all evidence and assessments. The local model sees
   only aggregate statistics and the highest 64 deterministic priorities.
6. High-priority Actors may be recommended for the repeatable selected-Actor
   GPU study. The static score is never represented as measured runtime cost.

The audit supports up to 4,096 eligible Actors in one level and fails explicitly
above that limit instead of truncating the level.

## Deterministic priority

The score uses visible thresholds for LOD0 triangles, missing Nanite on dense
meshes, single-LOD meshes, material-slot count, component Tick, query-and-
physics collision, large shadow-casting bounds, and compact Actors without a
draw-distance limit. Scores are capped at 100 and mapped to:

- `low`: 0-24
- `medium`: 25-49
- `high`: 50-74
- `critical`: 75-100

This is a triage signal. It does not measure draw calls, overdraw, occlusion,
frame time, GPU cost, memory residency, or packaged-build performance.

## Optional safe actions

An ordinary audit is read-only. If the operator explicitly requests a supported
setting change, the report may propose at most 32 Actor actions using only:

- `Cast Shadow`
- `Max Draw Distance` between 500 and 1,000,000 cm, or 0 to disable culling

A non-zero draw distance requires an explicit operator value because Stage 1
has no shot-specific visibility evidence. Density or risk score alone never
authorizes disabling shadows.

Before approval, Unreal recaptures every action target and compares identity,
mesh, static evidence, and mutable Before state with the frozen audit. One stale
target rejects the whole action set. Successful approval is one grouped Undo
transaction, marks the level dirty, and never saves automatically.

## Editor workflow

1. Open the level to audit. PIE/SIE is not required for Stage 1.
2. Optionally enter a question or explicit bounded setting request. An empty
   prompt uses the default whole-level audit request.
3. Press **Run Full Level Audit**.
4. Review captured/excluded counts, total LOD0 triangles, high-priority count,
   the top five Actors, findings, and proposed safe settings.
5. If actions are present, press **Approve & Apply Audit Actions** or dismiss
   the report. One Ctrl+Z restores an approved batch.
6. Use the recommended repeatable GPU study on high-priority Actors before
   making asset-level or visually risky changes.

## Current and next stages

Stage 1 connects complete static scanning, deterministic prioritization,
local-model interpretation, approval-time revalidation, and bounded reversible
changes. Stage 2 now orchestrates the read-only static funnel, a fixed PIE/SIE
runtime baseline, a GPU-scope trace, and deterministic Top-2 repeatable Actor
studies into one cross-validated profiling report.

Stage 3 is connected through post-apply verification: the best eligible stable candidate can run
a separate eight-trace Cast Shadow on/off experiment, restore runtime state, and
produce at most one stale-checked, approval-gated, unsaved formal action. After
approval, the Assistant repeats the complete profiling workload and offers a
fixed-threshold retain or explicit stale-checked rollback decision. Images may guard against visible
regressions, but they cannot establish rendering-performance gains; frame,
thread, GPU-scope, and repeatable Actor measurements remain authoritative.

Direct per-draw/per-shader/per-material attribution, Nanite/LOD/collision asset
mutation, packaged-build benchmarking, and unattended saving remain explicit
future capabilities.

See [assistant_level_profiling.md](assistant_level_profiling.md) for the Stage 2
workload lock, report synthesis, skip behavior, and evidence limits.
See [assistant_shadow_optimization.md](assistant_shadow_optimization.md) for the
first Stage 3 experiment, thresholds, approval boundary, and Undo behavior.
See
[assistant_shadow_optimization_verification.md](assistant_shadow_optimization_verification.md)
for the post-apply workload lock and retain/rollback guards.

## CLI

```powershell
render-master validate examples/unreal_level_audit_context.json `
  --contract unreal-level-audit-context

render-master assistant-level-audit `
  --prompt "Audit this complete level and prioritize the next measurements." `
  --context examples/unreal_level_audit_context.json `
  --output level_audit_report.json
```

`AssistantLevelAuditReport` embeds the complete context, canonical context
SHA-256, recomputable statistics, all deterministic assessments, model identity,
assessment-cited findings, and exact host-computed Before/After actions.
