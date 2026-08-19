# Architecture: dual-track foundation

## Boundary

The project is intentionally two independently testable products joined by
strict shared contracts:

1. **AI Core** builds cited knowledge, retrieves project assets and techniques,
   produces a `RenderSpec`, evaluates evidence, and proposes bounded repairs.
2. **Unreal Integration** probes a real project, catalogs assets, executes a
   validated `RenderSpec`, renders previews/finals, and captures evidence.

The planning model is not an unrestricted automation agent. It returns a
`RenderSpec`; a deterministic renderer adapter decides how that specification
maps to Unreal Engine APIs. A separate vision model evaluates preview renders.
The text planner then either proposes a host-validated `RenderSpecPatch` or
records a missing capability.

```text
knowledge + assets -> gpt-oss planner -> RenderSpec -> strict validation -> Unreal
                                             ^                              |
                                             |                              v
                                      validated patch <- correction <- EvaluationReport
                                                               |
                                                               v
                                                        capability gap
```

## Why schema-first

1. Invalid and invented fields are rejected before Unreal starts.
2. Planner models can be changed without changing the renderer integration.
3. Every run can store the exact input specification for reproducibility.
4. Unit tests can cover scene logic without launching Unreal.
5. A future Blender adapter can consume the same contract.

The contract uses Unreal's Z-up coordinate convention and explicit physical
light units. Directional lights use lux; local point, spot, and rect lights
use lumens, candelas, or an explicitly requested unitless compatibility mode.

## Why Chroma receives explicit embeddings

Chroma stores validated AssetCards only after Unreal has supplied stable asset
IDs, engine paths, types, dimensions, and material slots. RenderMasterBot calls
Ollama's embedding endpoint explicitly instead of allowing Chroma to choose or
download a default embedding function. The collection records its embedding
model identity and rejects attempts to query it with a different model.

See `contracts.md` for the thirty-seven public JSON boundaries used by both
tracks.

## Next milestones

1. ~~Add a semantic preflight checker for `RenderSpec` values that are structurally
   valid but physically suspicious.~~
2. ~~Add an Unreal project probe that emits a real `CapabilityManifest`.~~
3. ~~Enable and verify the required Unreal integration plugins, then scan 10-20
   real Unreal assets into `AssetCard` records.~~
4. ~~Index those cards in Chroma and constrain planning to the returned asset IDs.~~
5. ~~Execute one validated `RenderSpec` through the transient Unreal adapter.~~
6. ~~Capture the first Movie Render Queue preview and terminal `RunManifest`.~~
7. ~~Use AssetCard bounds for deterministic camera framing through an auditable
   `RenderSpecPatch`.~~
8. ~~Connect Qwen preview evaluation to strict, evidence-linked
   `EvaluationReport` output.~~
9. ~~Generate a bounded correction decision that either applies a validated
   `RenderSpecPatch` or reports an explicit capability gap.~~
10. ~~Add catalog-backed material assignment to the shared contract and Unreal
    adapter, then verify it in transient scene-build and MRQ preview paths.~~
11. ~~Add or ingest a suitable wood material, rerender a failed material preview,
    and compare the before-and-after evaluations.~~
12. ~~Add explicit exposure control and semantic product-view camera constraints,
    then rerun the wood-material correction until the visual evaluator passes or
    reports a narrower asset/geometry capability gap.~~
13. ~~Benchmark the dedicated vision evaluator on a small render suite, then add
    deterministic image statistics or a second-model review for contradictory
    high-severity findings.~~
14. ~~Add a bounded end-to-end orchestration command that runs retrieval,
    planning, preflight, Unreal preview, evaluation, correction, and comparison
    with explicit iteration and stop limits.~~
15. Calibrate real AssetCard bounds, studio lighting, and exposure against
    deterministic pixel evidence so validated corrections improve image quality
    rather than merely completing the loop.
16. ~~Connect the first approval-gated Editor action: capture one selected
    Static Mesh Actor, explicitly target its material slot when needed, retrieve
    a catalog material, review exact evidence, and apply an Undo-backed override
    without automatic saving.~~
17. ~~Extend the proposal/approval/revalidation pattern to bounded single-Actor
    world Transform edits with Before/After evidence and Ctrl+Z Undo.~~
18. ~~Connect approved material expansion: parameterized variants first,
    followed by license-checked external PBR acquisition, hash-recorded Unreal
    import, automatic catalog/Chroma synchronization, and an Editor approval UI.~~
19. ~~Extend the same pattern to bounded type-specific properties for one
    selected Directional, Point, Spot, or Rect Light, with stale-state
    revalidation and Unreal Undo.~~
20. ~~Extend the same pattern to one selected Camera or Cine Camera, including
    bounded world Transform, type-specific FOV/focal length, aperture, focus,
    exposure compensation, stale-state revalidation, and Unreal Undo.~~
21. ~~Extend the same pattern to one-to-32-Actor world/local-space Transform
    edits, with complete ordered evidence, all-or-nothing stale-state rejection,
    and one grouped Unreal Undo transaction.~~
22. ~~Extend the pattern to one-to-16-Light compatible group edits, including
    selection-wide type/unit checks, complete per-light evidence, all-or-nothing
    stale-state rejection, and one grouped Unreal Undo transaction.~~
23. ~~Add bounded camera-relative Key/Fill/Rim role coordination for one
    subject, one perspective camera, and exactly three existing compatible
    Movable local lights, with host-computed placement, five-Actor stale-state
    revalidation, and one grouped Undo transaction.~~
24. ~~Close the bounded lighting-rig feedback loop with one camera-view PNG,
    categorical local-vision diagnosis, host-computed intensity-only
    correction, explicit approval, five-Actor stale-state revalidation, and a
    separate grouped Undo transaction.~~
25. ~~Add coordinated multi-camera edits with complete ordered evidence,
    selection-wide stale-state rejection, and one grouped Undo transaction.~~
26. ~~Add selected-StaticMeshActor performance evidence, read-only findings,
    and approval-gated component Cast Shadow / Max Draw Distance actions with
    complete stale-state revalidation and grouped Undo.~~
27. ~~Add recomputable PIE/SIE runtime frame, Game/Render/RHI/GPU timing,
    frame-budget, process-memory, and RHI texture-memory evidence with a
    read-only local-model review.~~
28. ~~Add a bounded PIE/SIE `.utrace` capture, UE 5.7 TraceServices GPU queue
    parser, ranked inclusive GPU scope evidence, read-only local-model review,
    and direct raw-trace opening in Unreal Insights.~~
29. ~~Add a controlled selected-Actor GPU impact experiment with sequential
    Actor-visible and runtime-hidden traces, verified state restoration,
    duration-normalized queue-local scope deltas, constrained local-model
    interpretation, and direct opening of both traces in Unreal Insights.~~
30. Add repeated/interleaved trials and direct per-asset/per-material/
    per-shader/per-draw attribution, plus general geometry-aware Actor
    arrangement, packaged-build benchmarks, asset-level performance fixes,
    richer shot-specific lighting tools, and multi-shot visual evaluation.

## Default model roles

- `gpt-oss:20b`: text planning, structured RenderSpec generation, and correction planning.
- `qwen3-vl:8b-instruct`: preview-image inspection and structured visual findings.
- `qwen3-embedding:0.6b`: multilingual AssetCard indexing and semantic retrieval.

Only one role needs to be resident on the GPU at a time.
