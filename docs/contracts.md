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
| `UnrealLightingRigContext` | Unreal Editor assistant | lighting-rig proposal builder | frozen subject, perspective camera, and exactly three compatible local lights |
| `AssistantLightingRigProposal` | bounded lighting-rig proposal builder | Unreal Editor assistant/operator | approval-gated camera-relative Key/Fill/Rim action |
| `UnrealLightingRigReviewContext` | Unreal Editor assistant | local visual reviewer | frozen applied rig, ordered Key/Fill/Rim roles, and source request |
| `AssistantLightingRigReviewProposal` | bounded visual reviewer and host compiler | Unreal Editor assistant/operator | PNG-bound pass, capability gap, or approval-gated intensity-only correction |
| `UnrealCameraContext` | Unreal Editor assistant | camera proposal builder | frozen Camera/Cine Camera identity, lens bounds, and editable property evidence |
| `AssistantCameraProposal` | bounded camera proposal builder | Unreal Editor assistant/operator | approval-gated selected-Camera property change |
| `UnrealCameraSelectionContext` | Unreal Editor assistant | camera batch proposal builder | ordered evidence for 2-16 selected Camera/Cine Camera Actors |
| `AssistantCameraBatchProposal` | bounded camera batch proposal builder | Unreal Editor assistant/operator | approval-gated coordinated property action for the complete camera selection |
| `UnrealPerformanceSelectionContext` | Unreal Editor assistant | performance review builder | ordered measured evidence for 1-32 selected native StaticMeshActors |
| `AssistantPerformanceProposal` | bounded performance review builder | Unreal Editor assistant/operator | evidence-backed read-only findings or approval-gated shadow/culling action |
| `UnrealRuntimePerformanceCapture` | Unreal Editor PIE/SIE sampler | runtime review builder/auditor | raw consecutive frame/thread/GPU samples plus recomputable summaries and memory evidence |
| `AssistantRuntimePerformanceReport` | bounded runtime review builder | Unreal Editor assistant/operator | read-only evidence-cited diagnosis or explicit profiler capability gap |
| `UnrealInsightsGpuCapture` | Unreal Editor trace recorder and UE TraceServices parser | GPU scope review builder/auditor | hash-bound trace identity, GPU queues, and ranked inclusive scope evidence |
| `AssistantInsightsGpuReport` | bounded GPU scope review builder | Unreal Editor assistant/operator | read-only scope-cited diagnosis or explicit attribution capability gap |
| `UnrealActorGpuImpactExperiment` | Unreal Editor controlled A/B recorder and deterministic comparator | Actor GPU review builder/auditor | one restored runtime Actor target, two trace-backed captures, and normalized matched-scope deltas |
| `AssistantActorGpuImpactReport` | bounded Actor GPU impact review builder | Unreal Editor assistant/operator | read-only delta-cited impact candidates or explicit direct-attribution capability gap |
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
boundary now contains thirty-seven top-level contracts. `CorrectionDecision` is kept
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
- A lighting-rig context freezes one bounded subject, one perspective Camera or
  Cine Camera, and exactly three editable, unlocked, Movable Point, Spot, or
  Rect Lights sharing one non-EV intensity unit. The model assigns the three
  exact Actor paths to Key, Fill, and Rim once each and selects only bounded
  semantic style controls.
- The host computes the final camera-relative locations, aim rotations,
  intensity ratios, attenuation, Spot cone coverage, and exact changed-property
  evidence. Unreal rechecks all five Actors immediately before applying only
  the three lights in one Undo transaction. The subject and camera never change;
  spawn, delete, rename, retype, unit conversion, tint, and shadow changes are
  forbidden.
- Lighting-rig review freezes the already applied subject, camera, three lights,
  and exact ordered Key/Fill/Rim roles. The Editor captures one Lit PNG from the
  frozen camera, restores the user's viewport, and binds the proposal to the
  PNG SHA-256 plus deterministic luminance statistics.
- The vision model returns only categorical exposure, Fill, and Rim diagnoses.
  The host computes fixed-factor intensity changes: at most 1.2 globally and
  1.2 for the affected Fill or Rim role, for a maximum combined factor of 1.44.
  It cannot move lights or change rotation, color, temperature, attenuation,
  cones, shadows, camera, or subject. Unreal revalidates all five Actors before
  a separate approval-gated Undo transaction and never saves automatically.
- Camera target identity, kind, lens bounds, and complete Before values come
  from the Editor. The model can request only bounded operations; the host
  computes the final standard-Camera FOV or Cine-Camera focal length, aperture,
  focus, exposure compensation, and world Transform.
- Unreal rechecks the Camera Actor, component, kind, projection mode,
  editability, lock state, lens bounds, and unchanged property snapshot before
  one Undo-backed transaction. Camera proposals cannot change Filmback, lens
  presets, aspect ratio, projection mode, scale, tracked focus targets, or Post
  Process blend weight.
- Coordinated camera proposals contain one action for every frozen camera in
  selection order, including explicit no-op evidence for cameras already at an
  absolute target. One stale camera rejects the entire batch; successful
  approval is one grouped Undo transaction and never saves the level.
- Performance selection evidence is measured by Unreal and freezes Actor,
  component, mesh, LOD0 triangle, LOD, material-slot, Nanite, collision,
  Mobility, Tick, bounds, shadow, and cull-distance state. Findings may cite
  only those fields and only selected Actor paths.
- Executable performance proposals can change only component Cast Shadow and
  Max Draw Distance. Python emits ordered Before/After evidence for every
  selected Actor, including no-ops. Unreal revalidates the complete evidence;
  one stale Actor rejects the batch, one grouped transaction applies it, and
  the level is never saved automatically.
- Runtime performance captures preserve every raw consecutive PIE/SIE sample.
  Python recomputes nearest-rank summaries, frame-budget misses, and the largest
  available P95 component; tampered host summaries fail before inference.
- Runtime findings can cite only enumerated, available measurements. The final
  report embeds the exact validated capture and its canonical SHA-256, is
  permanently read-only, and cannot claim per-pass/per-Actor attribution or
  packaged-build parity from the short Editor sample.
- Insights GPU captures bind a preserved `.utrace` by filename, byte size, and
  SHA-256, then retain every non-empty GPU queue plus the 64 highest accumulated
  queue-local inclusive scopes. Scope totals are sorted but explicitly nested
  and potentially overlapping.
- Insights GPU findings may cite only captured `scope_id` values. The final
  report embeds the exact validated capture, preserves both its canonical hash
  and exact source-file hash, is permanently read-only, and
  cannot infer Actor, asset, material, shader, draw-call, or packaged-build
  attribution from scope timing alone.
- Actor GPU experiments require distinct Actor-visible and runtime-hidden
  captures with matching project, world, PIE/SIE mode, viewport, GPU, and trace
  channels. Only queue-local scopes present in both Top-64 sets are compared;
  totals and instance counts are normalized by each capture's actual duration.
- Every Actor GPU delta is recomputable from embedded capture evidence, sorted
  by absolute change, and accompanied by complete unmatched-scope sets. The
  runtime Actor must be restored before the experiment can claim completion.
- Actor GPU findings may cite only measured `delta_id` values. The final report
  embeds the exact experiment, preserves canonical and source-file hashes, is
  permanently read-only, and may describe association or an impact candidate
  but never direct pass/draw/material/shader/asset causation.
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
render-master validate examples/unreal_lighting_rig_context.json --contract unreal-lighting-rig-context
render-master schema assistant-lighting-rig-proposal -o assistant_lighting_rig_proposal.schema.json
render-master schema unreal-lighting-rig-review-context -o unreal_lighting_rig_review_context.schema.json
render-master schema assistant-lighting-rig-review-proposal -o assistant_lighting_rig_review_proposal.schema.json
render-master validate examples/unreal_lighting_rig_review_context.json --contract unreal-lighting-rig-review-context
render-master validate examples/unreal_camera_context.json --contract unreal-camera-context
render-master schema assistant-camera-proposal -o assistant_camera_proposal.schema.json
render-master validate examples/unreal_camera_selection_context.json --contract unreal-camera-selection-context
render-master schema assistant-camera-batch-proposal -o assistant_camera_batch_proposal.schema.json
render-master validate examples/unreal_performance_selection_context.json --contract unreal-performance-selection-context
render-master schema assistant-performance-proposal -o assistant_performance_proposal.schema.json
render-master validate examples/unreal_runtime_performance_capture.json --contract unreal-runtime-performance-capture
render-master schema assistant-runtime-performance-report -o assistant_runtime_performance_report.schema.json
render-master validate examples/unreal_insights_gpu_capture.json --contract unreal-insights-gpu-capture
render-master schema assistant-insights-gpu-report -o assistant_insights_gpu_report.schema.json
render-master validate examples/unreal_actor_gpu_impact_experiment.json --contract unreal-actor-gpu-impact-experiment
render-master schema assistant-actor-gpu-impact-report -o assistant_actor_gpu_impact_report.schema.json
```

`render-master schema` and `render-master validate` default to `render-spec`,
so the version 0.1 command line remains backward compatible.
