# Selected Static Mesh performance review

RenderMasterBot can capture measured Editor evidence for one to 32 selected
native `StaticMeshActor` instances, ask the local planning model for a bounded
review, and optionally prepare two component-level settings for explicit
approval.

This selected-Actor workflow is not the runtime sampler. It does not claim GPU
time, CPU time, memory use, overdraw, occlusion, or whole-level frame cost from
static mesh metadata. Use **Capture Runtime Performance** for the separate
frame-level PIE/SIE review described in `assistant_runtime_performance.md`.

## Editor workflow

1. Select one to 32 native `StaticMeshActor` instances with valid Static Mesh
   assets. Mixed Actor types are rejected.
2. Enter a request such as `Review the selected meshes for obvious static
   performance risks.`
3. Press **Review Performance**.
4. Review the **Selected Mesh Performance Review** card. Every finding shows
   the Actor and the exact captured fields used as evidence.
5. A diagnostic request is read-only and has no Apply button.
6. For an explicitly requested supported change, review every ordered
   Before/After action and press **Approve & Apply Settings**.
7. One Ctrl+Z restores the complete approved batch. The level is never saved
   automatically.

Example bounded action request:

```text
For these selected background props, disable Cast Shadow and set Max Draw
Distance to 10000 cm.
```

## Captured evidence

The Editor owns and serializes:

- Actor path, class, GUID, component, editability, and lock state;
- Static Mesh asset path;
- LOD count and LOD0 triangle count;
- material-slot count and Nanite enabled state;
- collision mode, component Mobility, and component Tick state;
- world-space bounds radius;
- current Cast Shadow and component Max Draw Distance.

The model may cite only these named fields. The proposal loader rejects a
finding or action that references an unselected Actor.

## Executable boundary

Only these instance/component settings are connected:

- `cast_shadow`;
- `max_draw_distance_cm`, where `0` disables distance culling and a non-zero
  value must be between 500 and 1,000,000 cm.

Python compiles the model's partial intent into one ordered action for every
selected Actor, including explicit no-ops. Unreal rechecks the complete mesh,
geometry, material, Nanite, collision, Tick, bounds, identity, and mutable
Before evidence immediately before applying anything. One stale Actor rejects
the batch. Successful changes use one rollback-safe `FScopedTransaction`.

Nanite changes, LOD generation, mesh simplification, material merging,
collision changes, Tick changes, Mobility changes, spawning, deletion, asset
saving, and runtime-profiler claims remain unconnected. The Assistant may
recommend them from measured evidence, but cannot present them as executable.

## CLI

```powershell
render-master assistant-performance-propose `
  --prompt "Review the selected meshes for obvious static performance risks" `
  --context examples/unreal_performance_selection_context.json `
  --proposal-id performance_review_001 `
  --output performance_proposal.json

render-master validate `
  examples/unreal_performance_selection_context.json `
  --contract unreal-performance-selection-context
```

The model gets one bounded retry for malformed, out-of-scope, or unselected
evidence. A second unsafe response fails without changing Unreal.
