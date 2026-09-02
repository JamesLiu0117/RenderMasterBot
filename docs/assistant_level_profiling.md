# Profiling-First Level Audit

The Unreal Assistant can now run the first complete read-only profiling chain
for a level. It combines existing analyzers under one fixed Play In Editor (PIE)
or Simulate In Editor (SIE) workload and produces one cross-validated report.

This is the authoritative performance path. A screenshot can expose a visual
regression, but it cannot measure frame time, GPU duration, control noise, or
whether an optimization improved rendering cost.

## What one run does

1. Captures the complete Full Level Audit static funnel without allowing level
   actions.
2. Discards exactly 30 warmup frames and records exactly 120 consecutive
   runtime frame/thread/GPU samples.
3. Records and preserves one five-second Unreal Insights CPU/GPU/frame/bookmark
   trace, then ranks inclusive queue-local GPU scopes.
4. Selects at most the first two deterministic high/critical static assessments
   that recommend a repeatable Actor GPU study.
5. Runs the fixed eight-capture `AA_AB_BA_AB` study for each safely mapped Actor,
   including one-second warmups and runtime-visibility restoration.
6. Produces a model-free composite summary from the already validated child
   reports. The final synthesis cannot invent findings or hide missing stages.

## Fixed workload contract

The runtime and GPU reports must have the same project, world, PIE/SIE mode,
viewport dimensions, and GPU adapter. The static audit must identify the same
project. Capture durations and sample counts are fixed, not prompt-controlled.

The operator must keep the camera, resolution, content state, and workload
representative and unchanged. If PIE/SIE ends before the profiling stages
finish, the orchestrator fails instead of combining evidence from different
worlds.

## Actor selection and bounded skips

The Actor candidates come from deterministic static-audit order, not another
model decision. Only the first two eligible recommendations are attempted. If
an Editor Actor cannot be mapped safely to a registered runtime Actor, the run
records its assessment ID and reason as an explicit skip. It cannot replace the
Actor with another candidate or conceal the missing measurement.

Every completed study must preserve all eight traces and prove that temporary
runtime visibility was restored. Hiding an Actor can change shadows, occlusion,
and downstream work, so a measured delta is an impact association rather than
direct draw, material, shader, or asset causation.

## Report contents

`AssistantLevelProfilingReport` embeds the complete run and exposes:

- frame P95 and frame-budget miss fraction;
- optional GPU-frame P95 when the RHI sample is available;
- the leading inclusive GPU scope and normalized milliseconds per second;
- measured and skipped Actor counts;
- repeatable Actor findings compared with A/A control noise;
- stable-candidate count;
- child report IDs, a canonical run SHA-256, and explicit limitations.

Python and Unreal both reject mismatched IDs, hashes, workloads, parameters,
candidate order, count summaries, or unrestored runtime state.

## What it does not claim

- PIE/SIE results do not prove packaged-build parity.
- Inclusive nested scopes can overlap.
- Actor hiding is controlled association evidence, not direct attribution.
- Images are auxiliary visual-regression guards, not performance evidence.
- No production level property is changed or saved.

## Editor workflow

1. Start a representative PIE/SIE session.
2. Set the desired camera and workload, then leave them unchanged.
3. Press **Run Profiling-First Level Audit**.
4. Let the static audit, baseline, GPU trace, and up to two Actor studies finish.
5. Review the composite result and open preserved child traces when deeper
   inspection is needed.
6. Use **Cancel Profiling Audit** if necessary; temporary Actor state is restored.

Two Actor studies contain sixteen five-second traces plus warmups and analysis,
so a complete run normally takes materially longer than a single trace.

## CLI synthesis

The Editor calls the same deterministic synthesis available from the CLI:

```powershell
render-master assistant-level-profiling `
  --prompt "Profile this complete level and identify measured bottlenecks." `
  --run-id level_profile_001 `
  --created-at-utc 2026-08-19T20:00:00Z `
  --level-audit-report level_audit_report.json `
  --runtime-report runtime_report.json `
  --gpu-report gpu_report.json `
  --actor-study-report actor_01_study.json `
  --actor-study-report actor_02_study.json `
  --output level_profiling_report.json
```

When a deterministic candidate cannot be measured, `--skip-manifest` accepts a
strict JSON object containing at most two `{assessment_id, reason}` entries.

## Connected optimization handoff

The completed report now feeds **Test Best Shadow Optimization**. That workflow
selects the first stable Actor candidate whose formal component still has Cast
Shadow enabled, runs a separate fixed eight-trace shadow-on/off experiment,
restores runtime state, and uses fixed host thresholds to reject or propose the
exact change. A proposal still requires explicit approval, complete formal-state
revalidation, and a stopped PIE/SIE session. It creates one unsaved Undo
transaction; the experiment itself changes no production content.

See [assistant_shadow_optimization.md](assistant_shadow_optimization.md).

Post-apply whole-level confirmation is now connected. After the approved formal
change, the Assistant freezes this report, repeats the complete profiling
workload, and compares the fresh result with fixed retain/rollback guards.
Images remain auxiliary visual-regression guards and cannot award a performance
win. See
[assistant_shadow_optimization_verification.md](assistant_shadow_optimization_verification.md).
