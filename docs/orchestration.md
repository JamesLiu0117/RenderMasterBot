# Bounded end-to-end orchestration

`render-master run` connects the existing local AI and Unreal boundaries into
one auditable workflow:

```text
prompt
  -> Chroma retrieval
  -> strict RenderSpec planning
  -> deterministic camera framing and semantic preflight
  -> transient Unreal preview
  -> Qwen3-VL evaluation
  -> bounded correction decision
  -> corrected preview, or a terminal stop reason
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
Unreal process log, and correction artifacts when applicable. A later attempt
never reuses or overwrites an earlier iteration directory.

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
  --max-iterations 2 `
  --view-axis preserve
```

Exit code `0` means `evaluator_passed`. Exit code `2` means the workflow safely
stopped without a pass. Exit code `1` means a stage failed. In all cases where
the workflow directory was initialized successfully, inspect
`workflow_manifest.json` for the final state and available evidence.

Use `--fail-on-warning` when any semantic preflight warning must prevent an
Unreal launch. Use a new workflow directory for every retry or configuration
change.

## First complete local loop

`e2e-door-007` completed two full preview/evaluation iterations on the real UE
5.7 project and stopped with `max_iterations_reached`. This is a successful
orchestration test, not a visual-quality pass: the first correction produced a
valid patch and a second verified render, but it made exposure worse.

Freezing the 13 retrieved cards instead of exposing the 544-card source catalog
reduced correction input from 7,778 to 3,325 tokens. Deterministic image evidence
confirmed that mean luminance fell from 0.1110 to 0.0122 and dark-pixel fraction
rose from 0.8058 to 0.8807. The next milestone is therefore Unreal bounds,
lighting, and exposure calibration; the workflow no longer needs structural
orchestration work to produce that training and evaluation evidence.
