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
| `UnrealActorTransformContext` | Unreal Editor assistant | Transform proposal builder | frozen Actor identity and world Transform evidence |
| `AssistantTransformProposal` | bounded Transform proposal builder | Unreal Editor assistant/operator | approval-gated selected-Actor world Transform change |
| `UnrealTransformSelectionContext` | Unreal Editor assistant | batch Transform proposal builder | ordered evidence for one to 32 selected Actors |
| `AssistantTransformBatchProposal` | bounded Transform proposal builder | Unreal Editor assistant/operator | approval-gated world/local Transform action for the complete selection |
| `UnrealLightContext` | Unreal Editor assistant | light proposal builder | frozen Light identity, type, unit, and editable property evidence |
| `AssistantLightProposal` | bounded light proposal builder | Unreal Editor assistant/operator | approval-gated selected-Light property change |
| `UnrealLightSelectionContext` | Unreal Editor assistant | batch light proposal builder | ordered evidence for one to 16 selected lights |
| `AssistantLightBatchProposal` | bounded light proposal builder | Unreal Editor assistant/operator | approval-gated compatible group property action |
| `UnrealCameraContext` | Unreal Editor assistant | camera proposal builder | frozen Camera/Cine Camera identity, lens bounds, and editable property evidence |
| `AssistantCameraProposal` | bounded camera proposal builder | Unreal Editor assistant/operator | approval-gated selected-Camera property change |
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
boundary now contains twenty-three top-level contracts. `CorrectionDecision` is kept
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
- The Transform model can express only per-axis `set`/`add` operations for
  world location and rotation, local-space `add`, or `set`/`multiply` for scale.
  It cannot select Actors or author trusted Before/After evidence.
- Batch Transform selection identity and Before values come from the Editor.
  The host applies one intent uniformly to the ordered selection, rotates local
  translation by each Actor basis, composes local rotation as quaternions, and
  emits final world-space evidence for every Actor.
- Unreal rechecks every captured path, class, GUID, root component, mobility,
  editability, lock state, and unchanged Transform. One stale Actor rejects the
  complete batch; successful approval uses one Undo-backed transaction.
- Light selection identity, kind, intensity unit, and Before values come from
  the Editor. The model can request only one uniform bounded intent. The host
  computes every After snapshot and rejects properties not supported by the
  complete Directional/Point/Spot/Rect selection.
- Percentage intensity edits may span mixed non-EV units; absolute intensity
  edits require one shared unit. Attenuation requires all-local lights, cone
  edits require all Spot Lights, and rotation rejects any Point Light.
- Unreal rechecks every Light Actor, component, Mobility, type, unit,
  editability, lock state, and complete snapshot immediately before one grouped
  Undo transaction. One stale light rejects the group. A proposal cannot spawn,
  delete, rename, retype, or change intensity units.
- Camera target identity, kind, lens bounds, and complete Before values come
  from the Editor. The model can request only bounded operations; the host
  computes the final standard-Camera FOV or Cine-Camera focal length, aperture,
  focus, exposure compensation, and world Transform.
- Unreal rechecks the Camera Actor, component, kind, projection mode,
  editability, lock state, lens bounds, and unchanged property snapshot before
  one Undo-backed transaction. Camera proposals cannot change Filmback, lens
  presets, aspect ratio, projection mode, scale, tracked focus targets, or Post
  Process blend weight.
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
render-master validate actor_transform.json --contract unreal-actor-transform-context
render-master validate transform_proposal.json --contract assistant-transform-proposal
render-master validate examples/unreal_transform_selection_context.json --contract unreal-transform-selection-context
render-master schema assistant-transform-batch-proposal -o assistant_transform_batch_proposal.schema.json
render-master validate examples/unreal_light_context.json --contract unreal-light-context
render-master schema assistant-light-proposal -o assistant_light_proposal.schema.json
render-master validate examples/unreal_light_selection_context.json --contract unreal-light-selection-context
render-master schema assistant-light-batch-proposal -o assistant_light_batch_proposal.schema.json
render-master validate examples/unreal_camera_context.json --contract unreal-camera-context
render-master schema assistant-camera-proposal -o assistant_camera_proposal.schema.json
```

`render-master schema` and `render-master validate` default to `render-spec`,
so the version 0.1 command line remains backward compatible.
