# Unreal preview rendering

`render-master unreal-render-preview` is the first complete RenderSpec-to-image
slice. It resolves a validated scene, launches Unreal Editor offscreen, creates
an unsaved one-frame Level Sequence with a camera cut, renders one PNG through
Movie Render Queue, and records the run as reproducible artifacts.

## Execution flow

1. Validate the `RenderSpec` and AssetCard catalog.
2. Run deterministic semantic preflight and resolve every asset ID.
3. Refuse to write into a non-empty run directory.
4. Copy the exact RenderSpec, asset catalog, and private scene request into
   `inputs/`, then hash them.
5. Write a `RunManifest` with `status="running"` before Unreal starts.
6. Replace the project's startup level with an unsaved blank Editor world, then
   spawn the scene and camera without inherited level geometry.
7. Create an in-memory Level Sequence and one-frame Movie Render Queue job.
8. Validate Unreal's structured result, observed actor transforms, and exactly
   one non-empty PNG inside the run directory.
9. Hash the outputs and replace the manifest with terminal status `succeeded`.
   Any host or Unreal error instead produces terminal status `failed` with a
   bounded diagnostic message.

An Unreal process exit code of zero is insufficient by itself. The host also
checks the RenderSpec identity, scene name, actor IDs and kinds, transforms,
preview containment, file type, file count, and file size.

## Persistence boundary

Preview actors and the Level Sequence must exist long enough for Play In Editor
rendering to duplicate them. They are therefore ordinary objects in the current
unsaved Editor world rather than actors carrying the transient object flag. The
adapter never saves that world, sequence, package, or any generated project
asset. Persistent outputs are written only under the caller-selected run
directory outside the Unreal project.

The PIE executor is explicitly allowed to use unsaved levels, runs offscreen,
and keeps the Python script alive until its completion callback has written the
structured result.

The adapter creates the blank world with
`EditorLoadingAndSavingUtils.new_blank_map(False)`. This prevents the project's
EditorStartupMap geometry from occluding generated subjects, while the `False`
argument prevents the previous map from being saved. The camera uses a fixed
36 mm sensor width and derives sensor height from the RenderSpec output aspect
ratio. The private scene request records that width so deterministic framing
and Unreal projection use the same filmback.

## Run directory

```text
preview-002/
|-- inputs/
|   |-- asset_cards.json
|   |-- render_spec.json
|   `-- scene_request.json
|-- preview/
|   `-- beauty.png
|-- run_manifest.json
`-- unreal_result.json
```

The caller supplies a contract-safe `--run-id`. A non-empty `--run-dir` is
rejected so an old result cannot be silently mixed with or overwritten by a new
run. Paths stored in the manifest are relative to this directory; every input
and output artifact also carries a SHA-256 digest.

## Usage

```powershell
render-master unreal-render-preview `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --spec "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\retrieval-001\door_plan_v2.json" `
  --assets "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\asset_cards.json" `
  --run-dir "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\preview-002" `
  --run-id "preview_002" `
  --fail-on-warning
```

The initial preview intentionally evaluates execution, not aesthetics.
`render-master frame-camera` can now fit known AssetCard bounds before render.
The next slice will ask the visual evaluator to report composition, lighting,
material, and visibility issues as a structured `EvaluationReport`.
