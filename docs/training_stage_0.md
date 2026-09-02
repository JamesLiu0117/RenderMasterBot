# Training Stage 0: auditable data foundation

## Outcome

Training Stage 0 converts immutable RenderMasterBot workflow evidence into a
private, deterministic chat-style dataset. It does **not** update model weights.
The purpose is to establish trustworthy supervision and evaluation boundaries
before a QLoRA experiment can consume GPU time.

Stage 1 adds a resumable, host-expectation-gated corpus campaign and frozen
base-model baseline. See [training_stage_1.md](training_stage_1.md). Campaign-
gated builder 0.2 uses the verified final RenderSpec and removes non-executable
notes; the ungated Stage 0 path remains compatible with initial planner output.

The builder currently exports one task:

```text
production planner system prompt
    + retrieved asset context
    + user graphics request
    -> validated initial RenderSpec
```

The production planner and dataset builder call the same
`build_scene_planner_messages()` function. This prevents the training prompt
from silently drifting away from the prompt used by the real assistant.

## Private data boundary

Generated JSONL, model weights, checkpoints, optimizer state, and experiment
logs stay outside this Git repository. The current local convention is:

```text
<private-data-root>/training/stage0-v1/
  dataset_manifest.json
  audit_report.json
  build_summary.json
  splits/
    train.jsonl
    validation.jsonl
    test.jsonl
```

The repository stores only the contracts, builder, auditor, CLI, tests, and
documentation required to reproduce the dataset. `.gitignore` already excludes
repository-local `data/`, model, checkpoint, and binary weight paths as a
second line of defense.

## Acceptance rules

A workflow becomes a positive planner SFT example only when all of these are
true:

1. `workflow_manifest.json` validates as `RenderWorkflowManifest`.
2. The terminal workflow status is `succeeded` with a passing final evaluation.
3. Exactly one hash-pinned retrieval result, retrieved AssetCard catalog,
   planner raw response, and initial RenderSpec are present.
4. Every required file still matches the SHA-256 recorded by the workflow.
5. Retrieved IDs agree across the manifest, retrieval records, and AssetCards.
6. The raw planner response and saved initial RenderSpec validate and represent
   the same canonical `RenderSpec`.
7. The resulting prompt/response content is not a duplicate of an accepted
   example.

Failed and stopped workflows are not discarded silently. They are listed in
`dataset_manifest.json` with a machine-readable reason. A stopped correction
trajectory may later become preference or correction-planning data, but it is
not treated as a known-good positive answer.

Arbitrary JSON files found elsewhere under the data root are never ingested.
Only evidence reachable through a validated workflow manifest is eligible.

## Example identity and provenance

Each JSONL line is a strict `TrainingExample` containing:

- ordered `system`, `user`, and `assistant` messages;
- the source workflow ID, manifest-relative path, and manifest file hash;
- portable paths and hashes for every source artifact;
- the response contract and canonical response hash;
- a hash of the complete example payload.

The assistant message is canonical JSON and must validate as `RenderSpec` when
the dataset is built and every time it is audited.

## Leakage-resistant split strategy

Splitting is deterministic and grouped by `source_group_id`, which is currently
the workflow ID. All examples derived from one workflow must remain in the same
split. Assignment uses a stable SHA-256 rank of the configured seed and group
ID; it never applies a random row-level split.

When fewer than three accepted workflow groups exist, all examples remain in
`train`. Empty validation and test splits are explicit and prevent the project
from reporting an unsupported evaluation result.

## Build and audit commands

Create a new dataset directory:

```powershell
$PrivateDataRoot = "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data"

render-master training-data build `
  --source-root $PrivateDataRoot `
  --output-dir "$PrivateDataRoot\training\stage0-v1" `
  --output "$PrivateDataRoot\training\stage0-v1\build_summary.json"
```

The output directory must be new or empty. The builder will not overwrite a
prior dataset.

Re-audit an existing dataset without rebuilding it:

```powershell
render-master training-data audit `
  "$PrivateDataRoot\training\stage0-v1\dataset_manifest.json" `
  --output "$PrivateDataRoot\training\stage0-v1\audit_report.recheck.json"
```

The audit independently checks split-file hashes, every example contract and
self-hash, manifest membership, duplicate IDs, cross-split example overlap, and
cross-split workflow-group overlap.

## Readiness gates

Passing integrity does not mean enough data exists to fine-tune responsibly.
Stage 0 records two explicit minimum gates:

| Gate | Train | Validation | Test | Meaning |
| --- | ---: | ---: | ---: | --- |
| QLoRA smoke | 20 | 2 | 2 | pipeline-only adapter experiment; not a product claim |
| Product fine-tune | 500 | 50 | 50 | minimum starting point for a narrow planner candidate |

These are project gates, not universal guarantees. Before any product adapter
is accepted, the fine-tuned model must also beat the unchanged base model on a
frozen held-out suite and must not regress schema validity, asset allow-list
compliance, preflight verdicts, or complete-loop success.

## Current snapshot: 2026-08-24

The first local build discovered nine workflow manifests:

- one succeeded workflow was accepted as one training example;
- seven failed workflows were excluded;
- one workflow stopped at the maximum iteration limit and was excluded;
- the resulting split is train 1, validation 0, test 0;
- all integrity, hash, duplicate, and leakage checks passed;
- neither readiness gate is met yet.

This small result is intentional. It proves the data pipeline with real
evidence without relabeling failed runs as successful supervision.

## Path to actual weight training

1. Capture at least 24 diverse successful workflows to unlock a meaningful
   three-way smoke split, with varied assets, materials, lighting, cameras, and
   composition requests.
2. Freeze an unchanged-base-model evaluation report before training.
3. Create a separate Python 3.12 CUDA environment for PyTorch, Transformers,
   PEFT, TRL, and bitsandbytes-compatible tooling. The current application
   environment remains independent.
4. Run a short QLoRA smoke experiment on a Hugging Face training checkpoint,
   not an Ollama runtime blob.
5. Compare the adapter and base model on the same held-out workflows. Keep the
   adapter only if measured workflow outcomes improve.
6. Expand later datasets deliberately: accepted corrections, human-approved
   before/after Unreal actions, profiling diagnoses with deterministic targets,
   and preference pairs from rejected versus approved proposals.

SIGGRAPH papers, engine documentation, and graphics references remain valuable,
but most should first enter cited retrieval knowledge and evaluation design.
They become weight-training material only when licensing permits it and a
specific input/output supervision task has been defined.
