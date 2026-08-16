# Automated Unreal PBR material import

`render-master unreal-import-pbr-material` turns four existing image files into a connected,
saved Unreal material without opening an import dialog. This command deliberately modifies
project Content, unlike the transient scene-build and preview commands.

## Inputs and connections

The first importer contract requires exactly one image for each role:

| Role | Unreal texture behavior | Material property |
| --- | --- | --- |
| Base color | sRGB, default compression | Base Color |
| DirectX normal | linear, `TC_NORMALMAP` | Normal |
| Roughness | linear, `TC_MASKS` | Roughness |
| Ambient occlusion | linear, `TC_MASKS` | Ambient Occlusion |

The normal input should use the DirectX convention expected by Unreal. Displacement is omitted
from the initial contract because mesh tessellation, Nanite displacement, scale, and material
quality settings need a separate bounded design.

## Host-side safety boundary

Before Unreal starts, the host adapter:

1. resolves and verifies every source file;
2. rejects duplicate inputs and unsupported image extensions;
3. validates the `/Game/...` destination and every asset name;
4. computes and freezes a SHA-256 for each image;
5. prepares a new result path for versioned evidence.

Unreal then checks all five expected target paths before importing anything. If a texture or the
material already exists, the run fails instead of overwriting it. A deliberate replacement or
reimport workflow is not yet exposed.

## Unreal-side execution

The bundled Unreal Python script uses automated `AssetImportTask` records to import four
`Texture2D` assets. It configures color space and compression, creates a `Material` through Asset
Tools, creates four `MaterialExpressionTextureSample` nodes, connects their outputs through the
Material Editing Library, recompiles the material, and saves every asset.

The result JSON records the observed Unreal project, material path, texture paths, source hashes,
sRGB flags, and compression settings. The host rejects a successful-looking result if any role,
hash, or engine path differs from the frozen request.

## Catalog integration

An approved external import immediately starts a second Asset Registry process scoped to the new
destination. The host requires the exact five executed paths, enriches their `AssetCard` records
with CC0 provider metadata and content hashes, and preserves the raw Unreal scan source as separate
engine evidence. It merges by engine path without changing unrelated cards, synchronizes Chroma,
keeps a timestamped pre-import catalog backup, and atomically replaces the JSON catalog.

Large catalogs are embedded in batches of 32 documents. Incremental synchronization compares the
stored Chroma document and metadata first, so unchanged cards do not consume another embedding or
GPU pass.

## Parameterized variants

`unreal-material-parameters` inspects scalar and vector parameters exposed by a parent material.
`unreal-create-material-variant` creates a new `MaterialInstanceConstant`, sets only explicit
overrides, reads every value back inside Unreal, saves the new asset, and refuses to overwrite an
existing path. Variants are the cheaper first expansion tier when a suitable parameterized parent
already exists; external PBR acquisition is the second tier.

## Limitation found by the first real test

The imported material was retrieved, assigned, and rendered, but lowering light intensity did not
reliably darken the first preview because the adapter had not frozen Unreal exposure. The initial
bounds-only camera framing also preserved a poor source viewing direction. Those findings led to
the fixed-EV100 exposure contract and explicit product-view axes documented in `exposure.md` and
`camera_framing.md`. The corrected material must now be rerendered under those controls before the
visual failure can be considered resolved.
