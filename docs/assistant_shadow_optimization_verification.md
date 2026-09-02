# Post-Apply Shadow Optimization Verification

This workflow closes the first measured optimization loop. A promising
Cast Shadow A/B result is not treated as the final result: after the formal
unsaved edit, the Assistant repeats the complete profiling-first workload and
compares fresh whole-level evidence with the frozen pre-change baseline.

No language or vision model decides whether to retain or roll back the change.
The Python contract and Unreal parser independently recompute the key metrics
and fixed decision guards.

## Frozen evidence

Immediately after **Approve & Apply Shadow Optimization**, Unreal freezes:

- the original profiling-first whole-level report and its file hash;
- the qualifying eight-trace shadow experiment report and its file hash;
- the exact Actor, component, mesh, and `Cast Shadow: true -> false` action;
- project, world, PIE/SIE mode, viewport size, GPU adapter, and capture settings.

If the formal state or either source report cannot be frozen exactly, the
Assistant does not arm verification. The already applied change remains
unsaved and can be restored with Ctrl+Z.

## Fresh workload

1. Restart the same representative PIE/SIE workload after the formal edit.
2. Press **Run Post-Apply Verification**.
3. Keep camera, viewport, resolution, content state, and workload unchanged.
4. The Assistant repeats the complete Full Level Audit, 30-frame warmup,
   120-sample runtime capture, five-second GPU trace, and deterministic Top-2
   Actor-study sequence.
5. The fresh report must have distinct report/run IDs and file hashes while its
   workload identity matches both frozen sources.

The target must also prove the exact `Cast Shadow: true -> false` transition.
Missing, stale, reused, or mismatched evidence produces `unresolved`, not an
assumed success.

## Fixed retain and rollback guards

The candidate is recommended for rollback when any available metric regresses
beyond its fixed guard:

| Metric | Maximum allowed regression |
| --- | --- |
| Whole-level frame P95 | larger of 0.5 ms or 2% |
| Whole-level GPU P95 | larger of 0.5 ms or 2% |
| Frame-budget miss fraction | 0.02 absolute increase |
| Qualified target GPU scope | larger of 1.0 ms/s or 2% |

When workload and source validation pass and no guard is exceeded, the result
is `retain`. When identity, freshness, or target-transition validation fails,
the result is `unresolved`. The report preserves explicit decision reasons.

## Operator actions and safety

- **Keep Optimization** records acceptance and makes no additional edit.
- **Keep Anyway (Override Recommendation)** is available for an unresolved or
  rollback recommendation, but does not relabel the measured result as a pass.
- **Approve Rollback** requires a stopped PIE/SIE session, explicit approval,
  and exact revalidation of the Actor, component, mesh, and current
  `Cast Shadow = false` state.
- Rollback applies a new bounded `false -> true` Unreal transaction. It does not
  invoke global Undo, so unrelated Editor edits made after the optimization are
  not consumed.
- Retain and rollback never save the level automatically.

Images remain optional operator evidence for visible regressions. Profiler
evidence is authoritative for performance.

## CLI comparison

The Editor calls the same deterministic comparator available from the CLI:

```powershell
render-master assistant-shadow-optimization-verification `
  --shadow-report shadow_optimization_report.json `
  --baseline-level-report level_profiling_before.json `
  --candidate-level-report level_profiling_after.json `
  --report-id shadow_verification_001 `
  --output shadow_verification_report.json
```

The public contract is
`assistant-shadow-optimization-verification-report`.

## Remaining boundary

This verifies one bounded component property under a repeatable Editor
workload. It does not yet provide direct per-draw, per-material, per-shader, or
per-asset attribution; packaged-build parity; unattended saving; or automatic
Nanite, LOD, mesh, material, and collision mutation.
