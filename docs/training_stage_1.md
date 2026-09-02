# Training Stage 1: gated corpus campaigns and frozen baselines

## Outcome

Training Stage 1 turns the Stage 0 dataset builder into a repeatable evidence
campaign. It does **not** update model weights. It supplies the automation and
acceptance boundary needed to decide when a QLoRA experiment is justified.

```text
frozen case suite + frozen project/catalog hashes
    -> resumable workflow campaign
    -> planner + preflight + Unreal preview + pixel sanity + vision review
    -> host-owned asset/material/count expectations
    -> eligible workflow allow-list
    -> final-RenderSpec SFT dataset + independent audit
    -> frozen unchanged-base-model baseline
```

Workflow success and training eligibility are deliberately separate. A workflow
can finish while selecting the wrong asset, adding an unsolicited material,
producing a black frame, or failing a visual requirement. Such evidence remains
available for diagnosis, but it is not positive SFT supervision.

## Frozen real-project suite

`examples/training_campaign_suite_optimization_v1.json` defines 24 requests
against `OptimizationPlugin`:

| Category | Cases | Purpose |
| --- | ---: | --- |
| single asset | 8 | asset selection, complete framing, and neutral presentation |
| material assignment | 4 | exact mesh, exact material, and exactly one assignment |
| multi-asset composition | 4 | asset completeness and bounded object counts |
| camera view | 4 | explicit view-direction and framing behavior |
| lighting | 4 | requested lighting intent without changing scene identity |

The suite freezes the `.uproject` SHA-256 and complete AssetCard-catalog
SHA-256. Planning or running fails before creating mutable evidence if either
source drifts. Every case forbids the dimensionless `SM_Plane` fallback. Cases
that do not request a material allow zero material overrides; explicit material
cases require exactly one known assignment.

## Contracts

Stage 1 adds three public boundaries:

- `TrainingCampaignSuite`: immutable cases and host-owned expectations;
- `TrainingCampaignRun`: selected cases, lifecycle, isolated attempts, hashes,
  and eligibility decisions;
- `TrainingBaselineReport`: recomputable base-model totals, per-category
  metrics, failure reasons, and a self-hash.

Campaign attempts retain both model-facing and host-facing results:

- terminal workflow outcome and stop reason;
- hash-pinned workflow manifest;
- visual evaluator verdict;
- observed scene and material asset IDs;
- observed object count;
- expectation failures;
- final `training_eligible` decision.

## Plan without executing

Create an empty campaign plan for the complete suite:

```powershell
$PrivateRoot = "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project"
$Suite = "E:\RenderBot\RenderMasterBot\examples\training_campaign_suite_optimization_v1.json"
$Catalog = "$PrivateRoot\data\assets\optimization-plugin\full-scan-003\asset_cards.json"
$Campaign = "$PrivateRoot\data\training\campaigns\optimization-training-v1"

render-master training-campaign plan `
  --suite $Suite `
  --campaign-dir $Campaign `
  --campaign-id optimization_training_v1 `
  --path "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --assets $Catalog `
  --max-attempts 2 `
  --output "$Campaign\plan_summary.json"
```

Use repeatable `--case-id` for a precise ordered pilot, or `--max-cases` for the
first N suite cases. The two selectors are mutually exclusive.

## Run, resume, and inspect

```powershell
render-master training-campaign run `
  --campaign-dir $Campaign `
  --path "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --assets $Catalog `
  --output "$Campaign\run_summary.json"

render-master training-campaign status "$Campaign\campaign_manifest.json"
```

Each logical case has a stable workflow ID. Attempts use separate
`attempt-001`, `attempt-002`, and `attempt-003` directories; no retry overwrites
earlier evidence. The campaign atomically records the active case before a
runner starts. On restart it analyzes a terminal attempt that finished before
the campaign manifest was updated, then continues without rerunning it.

`--retry-failed` retries only non-eligible cases that still have planned budget.
One failed case does not prevent later selected cases from running.

## Preview integrity and Smart App Control

Headless previews pass `-DisablePlugins=RenderMasterBot`. The automation script
does not need the Slate editor panel, and this prevents Windows 11 Smart App
Control from blocking an unsigned local editor-plugin DLL during the headless
process. Normal interactive Unreal Editor sessions still load the plugin.

The host validates more than file existence. It decodes the 8-bit PNG, reverses
PNG scanline filters, and rejects images with no meaningful visible pixel
range. This catches a real failure in which Movie Render Pipeline returned a
nominally successful but completely black frame.

Smart App Control has no supported per-file exception for an unsigned DLL.
Interactive local plugin development therefore requires an accepted code-signing
workflow or an operator decision about Smart App Control; the campaign does not
silently weaken Windows security.

## Freeze an unchanged-base-model baseline

```powershell
render-master training-campaign baseline `
  "$Campaign\campaign_manifest.json" `
  --report-id optimization_training_baseline_v1 `
  --output "$Campaign\baseline_report.json"
```

The report is valid only for a completed campaign. `completed` means every
selected case has a terminal attempt; it does not mean every case succeeded or
became eligible. The baseline records workflow success, expectation pass, and
training eligibility as separate rates.

## Build the campaign-gated dataset

```powershell
$Dataset = "$PrivateRoot\data\training\datasets\optimization-training-v1"

render-master training-data build `
  --source-root $Campaign `
  --output-dir $Dataset `
  --dataset-id optimization_training_dataset_v1 `
  --campaign-run "$Campaign\campaign_manifest.json" `
  --output "$Dataset\build_summary.json"

render-master training-data audit "$Dataset\dataset_manifest.json"
```

Builder `0.2` changes campaign-gated planner supervision in two important ways:

1. the assistant target is the hash-verified `final_render_spec`, not an earlier
   draft that may have needed correction;
2. non-executable `notes` are normalized to an empty list so contradictory
   planner commentary cannot become a positive label.

Without `--campaign-run`, Stage 0 remains backward compatible and exports the
validated initial planner response.

## Current real baseline: 2026-08-24

The complete 24-case suite ran once with `gpt-oss:20b`,
`qwen3-vl:8b-instruct`, 549 frozen AssetCards, and zero catalog drift. Every
case used one campaign attempt; its internal workflow could still use the
case's bounded correction iterations.

| Category | Eligible | Total | Rate |
| --- | ---: | ---: | ---: |
| single asset | 1 | 8 | 12.5% |
| material assignment | 1 | 4 | 25.0% |
| multi-asset composition | 0 | 4 | 0% |
| camera view | 0 | 4 | 0% |
| lighting | 0 | 4 | 0% |
| **overall** | **2** | **24** | **8.33%** |

The two eligible cases are `single_chamfer_cube` and
`material_door_weathered`. Their visual and host expectations passed, but they
remain baseline-quality rather than polished product renders; the door is
notably dark.

The host now emits an actionable failure taxonomy directly in
`TrainingBaselineReport`:

| Primary root cause | Count |
| --- | ---: |
| black or near-blank preview | 6 |
| invalid correction patch | 4 |
| Ollama length stop with empty JSON content | 3 |
| correction unresolved | 3 |
| unsupported exposure correction | 2 |
| dimensionless forbidden asset selected | 2 |
| maximum iterations reached | 2 |

Eleven cases also attempted an unsolicited material override. Host expectations
rejected it for prompts that did not request a material.

The campaign-gated dataset contains exactly the two eligible final RenderSpecs.
Its integrity audit has zero hash, membership, invalid-example, duplicate, or
cross-split leakage errors. The split is train 2, validation 0, test 0.

## Gate before QLoRA

The existing minimum gates remain unchanged:

| Gate | Train | Validation | Test |
| --- | ---: | ---: | ---: |
| QLoRA smoke | 20 | 2 | 2 |
| narrow product candidate | 500 | 50 | 50 |

The current real Stage 1 dataset is 2/0/0, so `qlora_smoke_ready=false`. The
next stage is not weight training. It is a verified reference-target campaign:
author or deterministically compile known-good RenderSpecs for the failed suite
cases, render and gate them, then collect enough diverse positive supervision
to reach at least 20/2/2 without relabeling base-model failures as successes.
