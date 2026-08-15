# Bounded end-to-end orchestration

`render-master run` connects the existing local AI and Unreal boundaries into
one auditable workflow:

```text
prompt
  -> Chroma retrieval
  -> strict RenderSpec planning
  -> deterministic studio calibration, camera framing, and semantic preflight
  -> transient Unreal preview
  -> deterministic image statistics and Qwen3-VL evaluation
  -> bounded correction decision
  -> corrected preview plus regression comparison, or a terminal stop reason
```

The orchestrator does not give either model direct Unreal access. The text
model can emit only a validated `RenderSpec` or allowed replacement-only patch.
The vision model can emit only subjective findings. Host code resolves asset
IDs, applies patches, launches Unreal, hashes artifacts, and decides whether the
next stage is permitted.

## Files and immutability

Every workflow requires a new or empty directory:

```text
workflow/
|-- workflow_manifest.json
|-- inputs/
|   |-- request.json
|   `-- asset_cards.json
|-- planning/
|   |-- retrieval.json
|   |-- retrieved_asset_cards.json
|   |-- planner_raw.json
|   |-- planner_metrics.json
|   |-- render_spec.json
|   |-- studio_calibration.patch.json
|   `-- camera_framing.patch.json
|-- specs/
|   |-- iteration-001.json
|   `-- iteration-001.preflight.json
`-- iterations/
    |-- iteration-001/
    `-- iteration-002/
```

Each iteration directory is a normal standalone Unreal preview run containing
its own frozen inputs, preview PNG, `RunManifest`, evaluation, model metrics,
Unreal process log, `image_statistics.json`, and correction artifacts when
applicable. Iterations after the first also contain `image_comparison.json`.
A later attempt never reuses or overwrites an earlier iteration directory.

## Retrieval boundary

The workflow searches static meshes and materials separately. Every returned
Chroma ID must also exist in the explicitly supplied `AssetCard` catalog. A
stale or unrelated index result is a terminal stage failure, not an asset the
planner is allowed to invent or resolve from a raw Unreal path.

The workflow freezes only the retrieved cards into
`planning/retrieved_asset_cards.json` and passes that bounded catalog to Unreal
and correction planning. The full source catalog remains frozen under
`inputs/` for provenance, but it cannot silently widen the iteration's asset
allowlist or consume the correction model's context window.

## Stop conditions

The `RenderWorkflowManifest.stop_reason` is always explicit for terminal runs:

| Stop reason | Meaning |
| --- | --- |
| `evaluator_passed` | the last verified preview received a pass verdict |
| `preflight_rejected` | deterministic checks rejected the spec before Unreal |
| `correction_unresolved` | the planner named a capability gap instead of a patch |
| `max_iterations_reached` | the configured render-attempt budget was exhausted |
| `correction_cycle_detected` | a correction returned to a previously rendered spec hash |
| `image_quality_regressed` | deterministic pixel evidence detected a large correction regression |
| `stage_failed` | retrieval, planning, rendering, evaluation, or persistence failed |

One to five preview attempts are allowed. The default is two. On the last
allowed attempt the workflow evaluates the preview but does not request a patch
that it cannot render, so model work is not wasted.

## Command

```powershell
render-master run `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --prompt "Create a material-focused preview of a dark weathered wood door" `
  --assets "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\full-scan-003\asset_cards.json" `
  --workflow-dir "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\workflows\door-001" `
  --workflow-id "door_001" `
  --retrieve-assets 8 `
  --retrieve-materials 5 `
  --max-iterations 3
```

The complete workflow defaults to `--view-axis auto-product`, `--margin 0.02`,
and deterministic first-preview studio calibration. Use
`--no-studio-calibration` only for an intentional comparison with the planner's
original exposure and light intensity. Deterministic framing paths are
host-owned during correction and are not exposed to the text model.

Exit code `0` means `evaluator_passed`. Exit code `2` means the workflow safely
stopped without a pass. Exit code `1` means a stage failed. In all cases where
the workflow directory was initialized successfully, inspect
`workflow_manifest.json` for the final state and available evidence.

Use `--fail-on-warning` when any semantic preflight warning must prevent an
Unreal launch. Use a new workflow directory for every retry or configuration
change.

## First passing local loop

`e2e-door-009` passed the complete workflow on the real UE 5.7 project in one
iteration. It retrieved `SM_Door` and `M_WeatheredPlanks`, applied fixed EV100 9
and 20,000 directional lux, framed the subject from the planned negative-Y side,
rendered a hashed PNG, extracted deterministic pixel evidence, and received a
zero-issue `pass` from `qwen3-vl:8b-instruct`.

The PNG SHA-256 matched the statistics record. Catalog and runtime Unreal bounds
also matched exactly at approximately 200 x 200 x 200 cm with a zero pivot. The
image measured mean luminance 0.0913, center luminance 0.1686, foreground
fraction 0.2959, zero clipping, and no blank, underexposed, or overexposed flag.

`e2e-door-008` is retained as an important negative trace. Its first model
correction tried to change EV100 in a direction unsupported by healthy pixel
statistics and rotate a deterministically framed camera away from the subject.
That run motivated the evidence-guided retry and protected framing boundary now
used by the passing loop.
