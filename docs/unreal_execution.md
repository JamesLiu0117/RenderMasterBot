# Transient Unreal scene execution

`render-master unreal-build-scene` is the first deterministic renderer-adapter
slice. It resolves a validated `RenderSpec` against validated `AssetCard`
records, launches Unreal Editor headlessly, builds transient actors in the
current Editor world, and validates structured evidence returned by Unreal.

This milestone deliberately stops before image rendering. Its purpose is to
prove that the planner-to-engine contract can create the requested engine
objects safely and reproducibly before Movie Render Pipeline is added.

## Safety boundary

The model never supplies Python or console commands. Before Unreal starts, the
host process:

1. validates the complete `RenderSpec` and AssetCard catalog;
2. runs deterministic semantic preflight;
3. resolves every public asset ID to one catalog-owned Unreal package path;
4. rejects missing IDs, duplicate IDs, unsupported asset types, non-Unreal
   assets, and unsafe paths;
5. hashes the canonical RenderSpec and places that identity in the private
   execution request.

After Unreal exits, the host validates the result schema, status, scene name,
RenderSpec hash, actor IDs, actor kinds, and observed transforms. An exit code
of zero alone is not considered success.

## First-adapter scope

- Scene objects: `static_mesh` only.
- Camera: one transient `CineCameraActor` with focal length, aperture, and
  optional manual focus distance.
- Lights: directional, point, spot, and rect lights with physical intensity
  units, normalized linear RGB color, and shadow state.
- Persistence: all generated actors are transient. The command does not save a
  level or modify project Content.

Blueprints, skeletal meshes, levels, animations, material overrides, and asset
creation are rejected or deferred instead of being guessed by the adapter.

## Rotation convention

`Transform.rotation_deg` is axis based and maps to Unreal as follows:

| RenderSpec field | Unreal Rotator field | Physical axis |
| --- | --- | --- |
| `x` | `roll` | X / forward axis |
| `y` | `pitch` | Y / right axis |
| `z` | `yaw` | Z / up axis |

The planner prompt includes the same mapping. The Unreal result converts its
observed Rotator back to this XYZ representation before host-side comparison.

## Usage

```powershell
render-master unreal-build-scene `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --spec "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\retrieval-001\door_plan.json" `
  --assets "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\asset_cards.json" `
  --output "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\unreal-build-001\scene_build.json" `
  --fail-on-warning
```

Scene construction uses `-RenderOffscreen`, not `-nullrhi`. Asset Registry
scanning can run without an RHI, but UE 5.7 requires a real render interface
when the adapter creates a `CineCameraActor`; the offscreen flag keeps the run
headless while preserving the path required by the upcoming preview renderer.

## Next execution milestone

The next adapter slice will keep the transient scene alive, create a bounded
Movie Render Pipeline job from the same camera and RenderSettings, write a
preview image into a run directory, and emit a terminal `RunManifest`.
