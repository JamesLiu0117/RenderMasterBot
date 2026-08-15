# Transient Unreal scene execution

`render-master unreal-build-scene` is the first deterministic renderer-adapter
slice. It resolves a validated `RenderSpec` against validated `AssetCard`
records, launches Unreal Editor headlessly, builds transient actors in the
current Editor world, and validates structured evidence returned by Unreal.

This command deliberately stops before image rendering. Its purpose is to
isolate planner-to-engine scene construction from Movie Render Pipeline and
make engine object failures independently testable.

## Safety boundary

The model never supplies Python or console commands. Before Unreal starts, the
host process:

1. validates the complete `RenderSpec` and AssetCard catalog;
2. runs deterministic semantic preflight;
3. resolves every public mesh and material ID to a catalog-owned Unreal package path;
4. rejects missing IDs, duplicate IDs, unsupported asset types, non-Unreal
   assets, unknown material slots, material type mismatches, and unsafe paths;
5. hashes the canonical RenderSpec and places that identity in the private
   execution request.

After Unreal exits, the host validates the result schema, status, scene name,
RenderSpec hash, actor IDs, actor kinds, observed transforms, and material
evidence. An exit code of zero alone is not considered success.

## First-adapter scope

- Scene objects: `static_mesh` only.
- Materials: catalog-backed `MaterialInterface` assets assigned to named slots
  on spawned static-mesh components, with the applied asset path read back from
  Unreal before success is accepted.
- Camera: one transient `CineCameraActor` with focal length, aperture, and
  optional manual focus distance.
- Lights: directional, point, spot, and rect lights with physical intensity
  units, normalized linear RGB color, and shadow state.
- Persistence: all generated actors are transient. The command does not save a
  level or modify project Content.

Blueprints, skeletal meshes, levels, animations, material creation, parameter
authoring, and texture synthesis are rejected or deferred instead of being
guessed by the adapter.

## First material smoke test

The first real UE 5.7 material test resolved `M_PrototypeGrid` from a complete
539-asset project catalog and applied it to `SM_Door.Material_0`. Unreal read
the material back from slot index 0, returned the same catalog ID and package
path in actor evidence, and Movie Render Queue produced a hashed PNG showing
the grid on the door surface.

This run proves the assignment mechanism; it is deliberately not reported as
a wood-door correction. The scanned project contains 28 directly assignable
materials but no material whose observed name, path, or tags identify it as
wood. A suitable material must be added or ingested before the failed wood-door
preview can be honestly repaired.

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

## Preview continuation

`render-master unreal-render-preview` continues the same validated request into
a one-frame Movie Render Pipeline job, writes the PNG into an isolated run
directory, and emits a terminal `RunManifest`. See `unreal_preview.md` for its
artifact and failure semantics.
