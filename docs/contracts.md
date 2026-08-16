# Shared contracts: version 0.1

The AI core and engine adapters communicate through versioned JSON contracts.
Model output never calls Unreal directly; it must first validate against one of
these strict models. Unknown fields are rejected.

| Contract | Producer | Consumer | Purpose |
| --- | --- | --- | --- |
| `TechniqueCard` | knowledge ingestion | retriever/planner | cited graphics knowledge |
| `AssetCard` | Unreal asset scanner | retriever/planner/adapter | resolvable project assets |
| `UnrealSelectionContext` | Unreal Editor assistant | proposal builder | captured Actor, component, mesh, and slot evidence |
| `AssistantMaterialProposal` | catalog-backed proposal builder | Unreal Editor assistant/operator | approval-gated selected-Actor material change |
| `RenderSpec` | planner or human | validator/engine adapter | complete requested scene |
| `RenderSpecPatch` | evaluator or human | patch validator | bounded correction proposal |
| `EvaluationReport` | visual/rule evaluator | repair planner/benchmark | findings and verdict |
| `CorrectionDecision` | repair planner | orchestrator/auditor | bounded patch or capability gap |
| `VisualBenchmarkSuite` | human/dataset curator | benchmark runner | frozen evaluator ground truth |
| `VisualBenchmarkReport` | benchmark runner | auditor/dataset builder | accuracy, stability, and contradictions |
| `RenderWorkflowManifest` | orchestrator | user/auditor/dataset builder | bounded lifecycle and stop evidence |
| `CapabilityManifest` | Unreal project probe | planner/adapter | observed engine capabilities |
| `RunManifest` | orchestration layer | dataset builder/auditor | reproducible run evidence |

Although the early planning note called these "six contracts," the actual
boundary now contains thirteen top-level contracts. `CorrectionDecision` is kept
separate from `EvaluationReport` so visual observation and executable repair
remain independently testable.

## Safety properties

- Every model uses `extra="forbid"`; invented keys fail validation.
- `RenderSpecPatch` can modify only scene content (`objects`, `camera`,
  `lights`, `render`, and `notes`). It cannot rewrite contract metadata.
- Object material assignments reference catalog asset IDs and named mesh slots;
  raw Unreal paths never cross the public planner boundary.
- An assistant material proposal preserves the exact Editor selection evidence,
  can select only a slot observed in that evidence, and distinguishes a valid
  proposal from an explicit unresolved capability gap.
- When `target_slot_index` is present, it must identify an observed slot and the
  proposal's selected slot must match it exactly.
- The host rechecks retrieved material IDs against the supplied catalog. Unreal
  revalidates the captured Actor, component, mesh, slot, and current material
  immediately before applying an approved transaction.
- External-material operational records freeze provider metadata, four local
  map hashes, the exact five planned Unreal paths, and the canonical proposal
  SHA-256. A mismatched approval hash stops before Unreal is launched.
- Imported external cards retain both Unreal Asset Registry evidence and their
  CC0 provider source. Catalog replacement is atomic and keeps a byte-for-byte
  pre-import backup; Chroma embeds only inserted or changed records.
- A `pass` evaluation cannot contain an error or blocking issue.
- Preflight evaluation can run before an image exists; preview and final
  evaluation must point to at least one rendered image.
- Suggested patches must target the exact SHA-256 identity of the evaluated
  `RenderSpec`.
- Every correction decision also targets the exact canonical SHA-256 identity
  of its source `EvaluationReport`.
- A `patch` correction requires a validated patch; an `unresolved` correction
  forbids one and must name at least one missing capability.
- For local vision evaluation, the model produces subjective findings only;
  the host owns the model identity, RenderSpec hash, stage, and evidence paths.
- Visual benchmark labels, suite identity, PNG statistics, and model-match
  decisions are host-owned; the vision model cannot write its own score.
- Workflow iteration limits, stage transitions, artifact identities, and stop
  reasons are host-owned; terminal manifests cannot hide why execution ended.
- Patch application rechecks the base hash, applies only the bounded JSON Patch
  subset, and validates the complete resulting `RenderSpec` before use.
- Artifact paths are run-relative and portable; absolute and parent paths are
  rejected.
- Terminal runs require an end timestamp, and failed runs require an error.
- Capability assertions record whether they were confirmed, inferred, not
  detected, or contradicted, together with the evidence source.

## CLI

```powershell
render-master schema technique-card
render-master schema capability-manifest -o capability.schema.json
render-master validate examples/asset_card.json --contract asset-card
render-master validate examples/run_manifest.json --contract run-manifest
render-master validate correction.json --contract correction-decision
render-master validate visual_suite.json --contract visual-benchmark-suite
render-master validate workflow_manifest.json --contract render-workflow-manifest
render-master validate selection_context.json --contract unreal-selection-context
render-master validate material_proposal.json --contract assistant-material-proposal
```

`render-master schema` and `render-master validate` default to `render-spec`,
so the version 0.1 command line remains backward compatible.
