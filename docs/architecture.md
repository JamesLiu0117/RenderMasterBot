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
maps to Unreal Engine APIs. A separate vision model evaluates preview renders
and may propose a `RenderSpecPatch`.

```text
knowledge + assets -> gpt-oss planner -> RenderSpec -> strict validation -> Unreal
                                             ^                              |
                                             |                              v
                                      validated patch <- EvaluationReport <- evidence
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

## Why Chroma is not connected yet

Chroma will retrieve assets by semantic meaning, but the collection needs a real asset-catalog shape first: stable asset ID, engine path, display name, tags, dimensions, license, and optionally an embedding. Connecting an empty vector database now would create infrastructure without testable behavior.

See `contracts.md` for the seven public JSON boundaries used by both tracks.

## Next milestones

1. Add a semantic preflight checker for `RenderSpec` values that are structurally
   valid but physically suspicious.
2. ~~Add an Unreal project probe that emits a real `CapabilityManifest`.~~
3. ~~Enable and verify the required Unreal integration plugins, then scan 10-20
   real Unreal assets into `AssetCard` records.~~
4. Index those cards in Chroma and constrain planning to the returned asset IDs.
5. Execute one hand-written `RenderSpec` through the Unreal adapter.
6. Capture the first preview and `RunManifest`, then connect Qwen evaluation.

## Default model roles

- `gpt-oss:20b`: text planning, structured RenderSpec generation, and later correction planning.
- `qwen3.5:9b`: preview-image inspection and structured visual findings.

Only one role needs to be resident on the GPU at a time.
