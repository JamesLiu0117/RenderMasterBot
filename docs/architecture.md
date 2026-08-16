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

See `contracts.md` for the thirteen public JSON boundaries used by both tracks.

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
17. Extend the same proposal/approval/revalidation pattern to transforms,
    lights, cameras, and performance fixes.
18. ~~Connect approved material expansion: parameterized variants first,
    followed by license-checked external PBR acquisition, hash-recorded Unreal
    import, automatic catalog/Chroma synchronization, and an Editor approval UI.~~
19. Extend the same proposal/approval/revalidation pattern to transforms,
    lights, cameras, and performance fixes.

## Default model roles

- `gpt-oss:20b`: text planning, structured RenderSpec generation, and correction planning.
- `qwen3-vl:8b-instruct`: preview-image inspection and structured visual findings.
- `qwen3-embedding:0.6b`: multilingual AssetCard indexing and semantic retrieval.

Only one role needs to be resident on the GPU at a time.
