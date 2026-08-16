# External CC0 material workflow

RenderMasterBot can expand beyond materials already present in the Unreal
project. The connected provider is Poly Haven. Its assets are published under
CC0, while live use of its public API requires a unique User-Agent and visible
`Powered by Poly Haven` credit. The implementation records both the
[Poly Haven license](https://polyhaven.com/license) and
[API terms](https://polyhaven.com/our-api) in its evidence.

This path is local-first, not offline-only: semantic ranking, hashes, Chroma,
and model inference are local, but search metadata and selected texture files
come from Poly Haven over HTTPS.

## Editor lifecycle

1. Select one Actor with one valid Static Mesh Component and choose the exact
   slot when the mesh has more than one.
2. Enter the desired appearance and press **Search Poly Haven**.
3. The host searches official texture metadata, ranks candidates with the local
   embedding model, caches the top candidate's 1K JPG base-color, DirectX
   normal, roughness, and ambient-occlusion maps, and verifies provider MD5 plus
   host SHA-256. Unreal Content is unchanged.
4. Review the candidate name, description, source URL, CC0 license, four cached
   maps, five planned Unreal paths, and the canonical approval SHA-256.
5. Press **Approve Import & Apply** or **Reject**. Rejection leaves only the
   reusable cache outside the project.
6. Approval must match the exact proposal hash. It creates and saves four
   `Texture2D` assets and one connected `Material`, rescans only that destination,
   enriches five AssetCards with provenance, incrementally updates Chroma, then
   applies the material to the captured slot.

The component override is an Unreal transaction and supports Ctrl+Z. The five
new Content assets are persistent project files and are not removed by Ctrl+Z.
The level is never saved automatically.

## CLI lifecycle

The same boundary is split into explicit commands for diagnosis and automation:

```powershell
render-master external-material-search `
  --query "dark weathered wood planks" `
  --output "C:\local-data\external_search.json"

render-master external-material-acquire `
  "C:\local-data\external_search.json" `
  --asset-id "planks_brown_10" `
  --destination-root "C:\local-data\material-library" `
  --output "C:\local-data\external_acquisition.json"

render-master external-material-propose-import `
  "C:\local-data\external_acquisition.json" `
  --destination-path "/Game/RenderMasterBot/Imported/PolyHaven/PlanksBrown10" `
  --material-name "M_PH_PlanksBrown10" `
  --proposal-id "polyhaven_planks_brown_10_001" `
  --output "C:\local-data\external_import_proposal.json"
```

The proposal command prints the exact `APPROVAL SHA-256` and does not modify
Content. Pass that value, not a newly calculated or approximate identifier, to
the execution command:

```powershell
render-master external-material-execute-import `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --proposal "C:\local-data\external_import_proposal.json" `
  --approve-sha256 "<exact 64-character proposal hash>" `
  --approved-by "local_operator" `
  --import-output "C:\local-data\unreal_import.json" `
  --asset-catalog "C:\local-data\asset_cards.json" `
  --scan-output "C:\local-data\imported_asset_scan.json" `
  --catalog-sync-output "C:\local-data\catalog_sync.json" `
  --output "C:\local-data\import_execution.json"
```

If Unreal import succeeded but catalog synchronization was interrupted, resume
without attempting to overwrite the five assets:

```powershell
render-master external-material-sync-import `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --proposal "C:\local-data\external_import_proposal.json" `
  --execution "C:\local-data\import_execution.json" `
  --asset-catalog "C:\local-data\asset_cards.json" `
  --scan-output "C:\local-data\imported_asset_scan.json" `
  --output "C:\local-data\catalog_sync.json"
```

## Safety boundary

- Only official `api.polyhaven.com` metadata and HTTPS downloads from
  `dl.polyhaven.org` are accepted.
- Every map is limited to 100 MB, redirects are disabled, and size, MD5, and
  SHA-256 must all match before proposal creation.
- Proposal creation re-reads the four files and freezes their identities.
- Import rejects an incorrect proposal SHA-256 before starting Unreal and
  refuses to overwrite any of the five target assets.
- Catalog synchronization requires an exact five-path scan, preserves all
  unrelated cards, writes a timestamped backup, and uses atomic replacement.
