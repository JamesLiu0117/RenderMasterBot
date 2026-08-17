# Approval-gated material assistant

This is RenderMasterBot's first direct, natural-language Editor action. It does
not create a render. It changes the material override on one selected Unreal
Static Mesh Component only after an operator reviews and approves a concrete
proposal.

```text
selected Actor + request
        |
        v
UnrealSelectionContext (read-only evidence)
        |
        v
local embedding -> Chroma material search -> catalog revalidation
        |
        v
AssistantMaterialProposal (proposed or unresolved)
        |
        v
operator review -> approve -> target revalidation -> Unreal transaction
                    reject -------------------------> no scene change
```

## Boundaries

- The target must be exactly one selected Actor with exactly one valid Static
  Mesh Component.
- A single material slot is targeted automatically. A multi-slot mesh requires
  the operator to choose the exact target from the Editor menu; the contract
  records that index alongside the complete observed slot list.
- Retrieval can recommend only `material` records that still exist in the
  supplied validated `AssetCard` catalog.
- The current material is excluded from the candidates.
- Proposal generation writes evidence locally but does not edit the scene.
- Approval fails safely if the Actor, component, mesh, slot, or current material
  changed after the proposal was prepared, or if the returned proposal points
  at a slot other than the explicit target.
- Approval applies a component material override through `FScopedTransaction`.
  It marks the level dirty but never saves it automatically; Ctrl+Z is supported.

## Material source tiers

**Prepare Material** searches only material assets already observed in the
configured Unreal project catalog. This gives the host an allowlist of real,
immediately loadable engine paths and prevents model-generated paths from
reaching `LoadObject`.

When a parameterized parent is suitable, the CLI can inspect its exposed scalar
and vector parameters and create a read-back-verified material-instance variant
without overwriting an asset.

**Search Poly Haven** is the connected external tier. It retrieves official
texture metadata, ranks with local embeddings, caches four maps outside the
project, verifies size, provider MD5, and SHA-256, and produces a frozen proposal.
The proposal shows `Powered by Poly Haven`, CC0, the source URL, and exactly five
planned Content paths. **Approve Import & Apply** binds to the canonical proposal
hash, imports and saves those assets, synchronizes the catalog and Chroma, then
applies the new material to the revalidated slot.

Rejecting before approval creates no Unreal Content. After approval, Ctrl+Z
undoes the component override but intentionally does not delete the five saved
assets. See `external_materials.md` for the complete evidence and recovery flow.

## Command-line contract probe

The Editor invokes the same public Python command that can be tested directly:

```powershell
render-master assistant-material-propose `
  --prompt "Make this look like dark weathered wood" `
  --context "C:\local-data\selection_context.json" `
  --assets "C:\local-data\asset_cards.json" `
  --output "C:\local-data\material_proposal.json"
```

The output is an `AssistantMaterialProposal`. `status="proposed"` carries one
catalog-verified material and the captured slot. `status="unresolved"` carries
no material and names the missing capability.

The Editor's external prepare step uses:

```powershell
render-master assistant-external-material-prepare `
  --prompt "dark weathered wood planks" `
  --library-root "C:\local-data\material-library" `
  --work-dir "C:\local-data\assistant-external\request-001" `
  --proposal-id "external_request_001" `
  --output "C:\local-data\assistant-external\request-001\assistant_external_proposal.json"
```

This command downloads only to the external cache. Its output is a strict
review envelope containing the frozen import-proposal path and SHA-256.
