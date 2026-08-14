# Unreal Asset Registry scanning

`render-master unreal-scan-assets` is the first runtime connection between
RenderMasterBot and a real Unreal project. It opens `UnrealEditor-Cmd.exe`, runs
the bundled scanner in Unreal's embedded Python runtime, and converts the result
into strict `AssetCard` records.

## Why there are two output files

- The raw scan is evidence of exactly what Unreal reported. It includes the
  total `/Game` asset count, the selected records, and extraction warnings.
- The AssetCard array is safe input for later retrieval and planning. Every
  item has passed Pydantic validation and uses the bounded public vocabulary.

Keeping both makes conversion bugs diagnosable without rescanning the project.

## Selection and loading behavior

The scanner sorts Asset Registry results by package path and samples them in a
round-robin across static meshes, skeletal meshes, materials, textures,
Blueprints, levels, animations, and other types. This produces a useful catalog
slice instead of returning only the alphabetically first asset class.

Most records are read without loading the asset. Only selected static meshes
are loaded to obtain their local bounding box, pivot-to-bounds-center offset,
and material slot names. Degenerate bounds are omitted instead of inventing a
positive dimension that would violate the `AssetCard` contract.

## Stable identity

An AssetCard ID combines a normalized display name with the first eight
hexadecimal characters of the package-path SHA-256. The ID stays stable across
scans, remains readable, and does not collide when two folders contain assets
with the same display name.

## Required project state

The Unreal project module must already be compiled for the selected engine.
`PythonScriptPlugin` must be available; this project also explicitly enables
`MovieRenderPipeline` and `MoviePipelineMaskRenderPass` for the later rendering
milestone.

The command requires an explicit `--engine-root`. This avoids silently running
a project with a stale or unintended Unreal installation.
