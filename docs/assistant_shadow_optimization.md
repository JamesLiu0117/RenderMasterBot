# Profiling-Gated Cast Shadow Optimization

This is the first connected data-driven optimization loop in the Unreal
Assistant. It does not ask a language or vision model to guess whether a visual
change improves performance. Unreal records eight GPU traces, the host
recomputes the result from raw scope evidence, and only a repeatable win can
become an operator-reviewable proposal.

## What the experiment changes

The experiment targets one native `StaticMeshActor` selected automatically from
the latest completed **Profiling-First Level Audit**. Its formal Static Mesh
Component must still have `Cast Shadow` enabled.

During PIE/SIE only, the Assistant temporarily compares:

- baseline A: `Cast Shadow = true`;
- candidate B: `Cast Shadow = false`.

The temporary runtime component is always restored to its original shadow state
before analysis, on cancellation, on failure, and during plugin shutdown. The
experiment itself never edits or saves the formal Editor level.

## Fixed eight-capture schedule

The schedule is `AA_AB_BA_AB`:

1. two independent shadow-on A/A control captures;
2. trial 1: shadow on, then shadow off;
3. trial 2: shadow off, then shadow on;
4. trial 3: shadow on, then shadow off.

Every capture has a one-second warmup and a five-second CPU/GPU/frame/bookmark
trace. Project, world, PIE/SIE mode, viewport, GPU adapter, trace channels, and
duration must remain identical. Only queue-local scope identities present in
all eight Top-64 scope sets are eligible for comparison.

## Deterministic decision

For every common scope the host recomputes A/A noise, all three paired
baseline-minus-candidate deltas, median, MAD, direction votes, relative
reduction, and signal excess over control noise. A proposal requires one scope
that satisfies all fixed conditions:

- classification is `stable_candidate`;
- disabling shadows decreases measured scope time;
- signal above A/A noise is at least 1.0 ms/s;
- relative reduction is at least 1%;
- at least two of three paired trials vote decreased;
- MAD is no greater than the absolute median delta.

If no scope qualifies, the candidate is rejected or marked unresolved. No
formal property is changed. The report is deliberately model-free, hash-bound,
and recomputable from its embedded captures.

## Approval and formal Editor action

When a candidate qualifies, the result card shows **Approve & Apply Shadow
Optimization** and **Reject Shadow Optimization**. The operator must stop
PIE/SIE before applying. Approval then recaptures and compares the exact formal
Actor path, component path, mesh identity, and complete Before evidence. Any
stale value rejects the action.

The only formal edit is `Cast Shadow: true -> false` on that exact component.
It is applied as one Unreal transaction, is never saved automatically, and one
Ctrl+Z restores the original setting. Rejecting leaves the formal level
unchanged.

Images are not performance evidence. They may be inspected by the operator as
an auxiliary visual-regression check before approval, but cannot award a win.

## Editor workflow

1. Start a representative PIE/SIE workload and keep camera, viewport,
   resolution, and content state fixed.
2. Run **Profiling-First Level Audit** and wait for its composite report.
3. Press **Test Best Shadow Optimization**. The Assistant chooses the first
   stable profiling candidate that still has formal shadows enabled.
4. Keep the workload fixed while all eight traces complete.
5. Review the deterministic aggregates and, if proposed, inspect the scene for
   unacceptable visual change.
6. Stop PIE/SIE. Approve the exact formal edit or reject it.
7. Restart the same representative PIE/SIE workload and press **Run Post-Apply
   Verification**. Review the whole-level result, then explicitly keep the
   optimization or approve the exact rollback.

## CLI analysis

The Editor calls the same deterministic analyzer available from the CLI:

```powershell
render-master assistant-shadow-optimization `
  --capture-set shadow_optimization_capture_set.json `
  --report-id shadow_optimization_report_001 `
  --output shadow_optimization_report.json
```

The public contracts are `unreal-shadow-optimization-capture-set` and
`assistant-shadow-optimization-report`.

## Connected post-apply verification

The approved unsaved edit now arms a second phase. It freezes this report and
the original profiling report, then requires a fresh run of the complete
profiling-first workload under the same project, world, PIE/SIE mode, viewport,
GPU, camera, and content state. The deterministic comparator returns `retain`,
`rollback`, or `unresolved`; no model can relabel the result. Rollback is a new
exact `Cast Shadow: false -> true` Unreal transaction after explicit approval
and stale-state revalidation, rather than a global Undo that could consume an
unrelated edit. Nothing is saved automatically.

See
[assistant_shadow_optimization_verification.md](assistant_shadow_optimization_verification.md).
Direct draw, material, shader, and packaged-build attribution remain outside
this milestone.
