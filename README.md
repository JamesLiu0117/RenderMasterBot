# RenderMasterBot

RenderMasterBot is a local-first graphics planning system. It turns a natural-language request into a strictly validated `RenderSpec`, which a renderer adapter can later execute in Unreal Engine.

The integration remains schema-gated: a language model is not allowed to run arbitrary engine code; it must return a versioned JSON document with known fields and constraints.

## Final product vision

RenderMasterBot is intended to become a local graphics AI agent, not a single
large language model and not merely an Unreal automation script. The finished
product combines two independently testable systems:

1. **AI Core** understands a graphics request, retrieves real project assets
   and cited graphics knowledge, produces a validated scene plan, evaluates
   rendered evidence, and proposes bounded corrections.
2. **Unreal Integration** observes the actual project and engine capabilities,
   resolves asset IDs, builds or modifies a scene, renders previews and finals,
   and returns structured execution evidence.

The intended user-facing entry point is an Unreal Editor panel backed by a
local RenderMaster service. The command line remains available for development,
debugging, automation, and reproducible experiments.

The target workflow is a closed loop:

```text
request -> project-aware retrieval -> RenderSpec -> validation -> Unreal preview
   ^                                                               |
   |                                                               v
approved correction <- bounded repair decision <- visual evaluation
```

When a requested change is supported, the system should apply it to a temporary
scene, rerender, and compare the result. When it is not supported, the system
should name the missing asset or capability instead of inventing an Unreal path
or claiming that an unrelated camera or lighting change solved the problem.

The final deliverables include the Unreal scene changes or rendered output,
the exact assets and materials used, the validated RenderSpec, evaluation and
correction records, and a reproducible run manifest.

## Project advantages

- **Project-aware instead of generic:** planning is constrained to assets and
  capabilities actually observed in the current Unreal project.
- **Closed-loop instead of one-shot:** the system renders, inspects evidence,
  plans a correction, rerenders, and measures whether the result improved.
- **Local-first:** private assets, prompts, model execution, vector data, and
  render evidence can remain on the workstation.
- **Schema-gated execution:** model output crosses versioned contracts, asset
  allowlists, semantic checks, hashes, and deterministic adapters before Unreal
  receives an instruction.
- **Auditable and reproducible:** every run can preserve model identities,
  inputs, asset references, parameters, outputs, findings, and corrections.
- **Replaceable models:** planning, vision, and embedding roles are separated,
  so each local model can be upgraded or benchmarked independently.
- **Domain-improvable:** cited research, engine documentation, successful runs,
  failure cases, preference data, and later fine-tuning can improve the AI Core
  without giving a model unrestricted engine access.
- **Extensible beyond one renderer:** the shared contracts isolate graphics
  intent from Unreal-specific execution and leave room for future adapters.

## Current milestone

- `RenderSpec` schema version 0.1
- Pydantic validation and JSON Schema export
- Dual-model defaults: `gpt-oss:20b` planning and `qwen3-vl:8b-instruct` visual evaluation
- Ollama structured-output client over the local REST API
- Deterministic semantic preflight with structured `EvaluationReport` output
- Read-only Unreal project capability probing with evidence provenance
- Headless Unreal Asset Registry scanning into validated `AssetCard` records
- Persistent Chroma indexing with explicit local Ollama embeddings
- Bounded 32-document embedding batches for stable full-catalog synchronization
- Incremental Chroma synchronization that embeds only new or changed AssetCards
- Type-filtered Chroma retrieval that rejects assets outside the returned catalog
- Catalog-backed per-slot material assignments with host and Unreal evidence validation
- Automated four-map PBR material import with frozen source hashes and no-overwrite behavior
- Verified UE 5.7 material override in both transient scene build and MRQ preview paths
- Verified CC0 wood-material acquisition, Unreal import, multilingual retrieval, and planner assignment
- Verified planner refusal to hide a remaining geometry gap after a material becomes available
- Deterministic AssetCard-bounds camera framing through auditable `RenderSpecPatch` output
- Automatic principal-axis product framing plus explicit six-axis inspection views
- Runtime Unreal bounds and pivot readback matched against the catalog before a run is trusted
- Default first-preview studio calibration at fixed EV100 9 and at least 20,000 directional lux
- Backward-compatible automatic or fixed-EV100 camera exposure with Unreal readback evidence
- Transient Unreal scene construction for static meshes, camera, and physical lights
- One-frame Unreal Movie Render Queue previews with terminal, artifact-hashed `RunManifest` records
- Verified local Qwen preview evaluation with strict, evidence-linked `EvaluationReport` output
- Verified Qwen3-VL material evaluation after fixed-exposure, top-down UE 5.7 rerender
- Repeatable visual evaluator benchmarking with frozen labels, deterministic PNG evidence, stability scoring, and contradiction detection
- First real four-case Qwen3-VL benchmark: 8/8 valid responses, 1.000 verdict stability, 0.750 case accuracy, and one preserved hard-negative contradiction
- Bounded end-to-end workflow orchestration with immutable iterations, explicit stop reasons, and correction-cycle detection
- Per-iteration deterministic PNG statistics, cross-iteration regression detection, and pixel-bound SHA-256 evidence
- Evidence-gated exposure correction, protected deterministic framing, and one auditable semantic retry
- Retrieved-only per-workflow asset catalogs that bound Unreal resolution and correction context
- Schema-guided one-retry recovery for truncated planner JSON, with invalid raw-output evidence
- Unreal scratch-output isolation with copied, hashed PNG/result artifacts and persistent process logs
- Bounded local correction planning that emits a validated patch or an explicit capability gap
- Native Unreal Editor dashboard with prompt input, six-stage progress, live process logs,
  latest PNG preview, visual verdict, pixel evidence, bounded cancellation, and local runtime settings
- Assistant-centered Unreal workspace with live project, level, and actor-selection context,
  an explicit proposed-action review, and approval required before execution
- Catalog-verified material proposals for one selected Static Mesh Actor, with
  automatic single-slot targeting, explicit multi-slot targeting, approval-time
  revalidation, no automatic save, and Ctrl+Z Undo
- Bounded world/local-space Transform proposals for one to 32 selected Actors,
  with ordered Editor-owned identity and Before evidence, per-Actor
  host-computed After values, all-or-nothing stale-state rejection, no
  automatic save, and one grouped Ctrl+Z Undo
- Compatible group light-property proposals for one to 16 selected Directional,
  Point, Spot, or Rect Lights, with frozen type/unit evidence, per-light
  Before/After review, all-or-nothing stale-state rejection, no automatic save,
  and one grouped Ctrl+Z Undo
- Camera-relative Key/Fill/Rim coordination for one subject, one perspective
  Camera or Cine Camera, and exactly three compatible Movable local lights,
  with model-selected roles, host-computed placement and intensity, five-Actor
  stale-state rejection, no automatic save, and one grouped Ctrl+Z Undo
- One-shot camera-view review of an applied Key/Fill/Rim rig, with a restored
  Lit Editor viewport, SHA-256-bound PNG evidence, categorical local-vision
  diagnosis, deterministic intensity-only correction, explicit approval,
  five-Actor revalidation, and a separate grouped Ctrl+Z Undo
- Type-specific Camera and Cine Camera proposals for one selected camera, with
  bounded world Transform, FOV or focal length, aperture, focus, exposure
  compensation, approval-time lens/state revalidation, no automatic save, and
  Ctrl+Z Undo
- Coordinated shared-property proposals for 2-16 selected Camera/Cine Camera
  Actors, with complete ordered Before/After evidence, type-aware all-or-nothing
  planning, selection-wide stale-state rejection, no automatic save, and one
  grouped Ctrl+Z Undo
- Evidence-backed performance review for 1-32 selected native StaticMeshActors,
  with measured mesh/LOD/material/Nanite/collision/Tick/shadow/culling/bounds
  fields, read-only findings, optional approval-gated Cast Shadow and Max Draw
  Distance actions, full stale-state rejection, and one grouped Ctrl+Z Undo
- Read-only PIE/SIE runtime performance capture with 30 warmup frames and 120
  consecutive raw samples, host-recomputed frame/Game/Render/RHI/GPU P50/P95/
  maximum evidence, frame-budget misses, process and RHI texture memory,
  schema-constrained local-model diagnosis, and explicit Editor/packaged-build
  caveats
- Trace-backed PIE/SIE GPU scope review with a five-second CPU/GPU/frame/
  bookmark `.utrace`, UE 5.7 `TraceServices` queue-timeline parsing, ranked
  inclusive scope count/total/mean/maximum evidence, SHA-256 trace identity,
  schema-constrained local-model findings, and one-click raw trace inspection
  in Unreal Insights
- Controlled selected-Actor GPU impact measurement with Actor-visible and
  temporarily runtime-hidden five-second traces, verified runtime-state
  restoration, one-second variant warmup, duration-normalized matched-scope
  deltas, constrained association-only model review, and both raw traces
  preserved for Unreal Insights
- Parameterized `MaterialInstanceConstant` variants with exposed-parameter
  inspection, exact value readback, and no-overwrite behavior
- Official Poly Haven texture discovery with local semantic ranking, visible
  provider credit, CC0 provenance, trusted-host enforcement, provider MD5, and SHA-256
- Hash-bound external material approval that creates exactly four textures and
  one connected material, then automatically rescans, merges, backs up, and
  incrementally synchronizes the AssetCard catalog and Chroma
- Unreal Assistant buttons for separate project-only search and external CC0
  search, with the five saved Content paths shown before **Approve Import & Apply**
- Command-line doctor, schema, validate, preflight, Unreal, retrieval, planning, evaluation, and correction commands
- Standard-library unit tests for contracts, planning, preflight, Unreal execution, and preview orchestration

General geometry-aware Actor arrangement, repeated/interleaved Actor GPU trials,
direct per-asset/per-material/per-shader/per-draw attribution, packaged-build benchmarking,
asset-level Nanite/LOD/mesh/material/
collision optimization, richer shot-specific lighting calibration, multi-shot
visual evaluation, and model fine-tuning are later milestones.

## Local setup

Create the virtual environment outside this repository if desired, then install this package in editable mode:

```powershell
& "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\.venv\Scripts\python.exe" -m pip install -e "E:\RenderBot\RenderMasterBot[dev]"
```

Verify the local Ollama server:

```powershell
render-master doctor
```

Install the default local models for planning, visual evaluation, and retrieval:

```powershell
ollama pull gpt-oss:20b
ollama pull qwen3-vl:8b-instruct
ollama pull qwen3-embedding:0.6b
```

Export the schema:

```powershell
render-master schema --output renderspec.schema.json
```

Validate the included example:

```powershell
render-master validate examples\simple_studio.json
```

Run semantic checks before sending a scene to Unreal:

```powershell
render-master preflight examples\simple_studio.json
render-master preflight examples\suspicious_scene.json --output preflight-report.json
```

Inspect a real Unreal project without modifying or launching it:

```powershell
render-master unreal-probe `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --output capability_manifest.json
```

Launch a compiled project headlessly and export a bounded, type-balanced asset sample:

```powershell
render-master unreal-scan-assets `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --limit 20 `
  --raw-output "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\raw_asset_scan.json" `
  --output "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\asset_cards.json"
```

The raw file preserves what Unreal observed. The second file contains records that have each passed the strict `AssetCard` contract.

Import four local PBR maps into a new Unreal content folder and create a connected material:

```powershell
render-master unreal-import-pbr-material `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --destination-path "/Game/RenderMasterBot/TestMaterials/WeatheredPlanks" `
  --material-name "M_WeatheredPlanks" `
  --base-color "C:\local-data\weathered_planks_diff_1k.jpg" `
  --normal "C:\local-data\weathered_planks_nor_dx_1k.jpg" `
  --roughness "C:\local-data\weathered_planks_rough_1k.jpg" `
  --ambient-occlusion "C:\local-data\weathered_planks_ao_1k.jpg" `
  --output "C:\local-data\weathered_planks_import.json"
```

The importer freezes every source SHA-256 before launching Unreal, refuses to overwrite an
existing target asset, applies Unreal-appropriate color and compression settings, connects the
four material properties, saves all five assets, and verifies the returned engine paths.

Create a parameterized variant without touching its parent material:

```powershell
render-master unreal-create-material-variant `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --parent-material "/Game/LevelPrototyping/Materials/M_PrototypeGrid" `
  --destination-path "/Game/RenderMasterBot/MaterialVariants" `
  --instance-name "MI_DarkRough" `
  --scalar "Roughness=0.85" `
  --vector "SurfaceColor=0.04,0.03,0.02,1" `
  --output "C:\local-data\dark_rough_variant.json"
```

For a material that is not already in the project, the external workflow uses
official Poly Haven metadata, downloads four verified maps to the local data
root, freezes a five-asset proposal, requires its exact SHA-256 for approval,
then imports and synchronizes the catalog and Chroma in the same command. See
[docs/external_materials.md](docs/external_materials.md) for the panel and CLI
lifecycles, license boundary, recovery command, and evidence files.

Build a validated scene as transient actors without saving or modifying project Content:

```powershell
render-master unreal-build-scene `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --spec "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\retrieval-001\door_plan.json" `
  --assets "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\full-scan-003\asset_cards.json" `
  --output "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\unreal-build-001\scene_build.json" `
  --fail-on-warning
```

Render one offscreen PNG through Unreal Movie Render Queue and finalize a hashed run record:

```powershell
render-master apply-patch `
  "C:\path\to\source_render_spec.json" `
  --patch "C:\path\to\validated_patch.json" `
  --output "C:\path\to\corrected_render_spec.json"

render-master frame-camera `
  "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\retrieval-001\door_plan_v2.json" `
  --assets "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\asset_cards.json" `
  --output "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\retrieval-001\door_plan_v3.json" `
  --patch-output "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\retrieval-001\door_plan_v3.patch.json"

render-master unreal-render-preview `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --spec "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\retrieval-001\door_plan_v3.json" `
  --assets "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\asset_cards.json" `
  --run-dir "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\preview-002" `
  --run-id "preview_002" `
  --fail-on-warning

render-master evaluate-preview `
  "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\preview-005"

render-master benchmark-evaluator `
  "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\visual_benchmark_suite_v1.json" `
  --output "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\visual_benchmark_qwen3vl_v1.json"

render-master plan-correction `
  "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\preview-005"
```

Synchronize those cards into the local Chroma collection:

```powershell
render-master asset-index `
  "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\asset_cards.json"
```

Test multilingual semantic retrieval, optionally restricted to an assignable
asset type:

```powershell
render-master asset-search --query "一扇可以打开的木门" --limit 5
render-master asset-search --query "natural wood grain" --asset-type material --limit 5
```

Show the resolved dual-model configuration:

```powershell
render-master config
```

Create a plan with the configured planning model:

```powershell
render-master plan --prompt "Create a studio product shot of a red chair"
```

Retrieve real assets first and restrict the planner to those IDs:

```powershell
render-master plan `
  --prompt "Create a product-style scene featuring one usable wooden door" `
  --retrieve-assets 8 `
  --retrieve-materials 5 `
  --output door-scene.json
```

Run the complete bounded local workflow:

```powershell
render-master run `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --prompt "Create a material-focused preview of a dark weathered wood door" `
  --assets "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\asset_cards.json" `
  --workflow-dir "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\workflows\door-001" `
  --workflow-id "door_001" `
  --max-iterations 2 `
  --view-axis preserve
```

Use `--model` only to benchmark or override the configured planner.

## Unreal Editor panel

The native Editor assistant lives in `unreal/RenderMasterBot`. Copy that plugin
folder into the target project's `Plugins` directory, enable `RenderMasterBot`
for the Editor target, compile the Editor target, and open **Tools >
RenderMasterBot > RenderMasterBot Assistant**. The workspace can also be opened at
startup with `-RenderMasterOpenPanel`.

The default **Assistant** page reads the live project, level, and selected-actor
context without modifying the scene. **Prepare Material** retrieves only
catalog-verified project materials. **Search Poly Haven** searches official CC0
external materials, caches and verifies four maps outside the project, and shows
the source, license, approval hash, and exact five Content paths before any
project mutation. A single slot is targeted automatically;
a multi-slot mesh requires an explicit choice in the **Target material slot**
menu. The panel shows the exact Actor, slot, current material, proposed material,
and evidence before an approval button becomes available. Project materials use
**Approve & Apply Material**. External materials use **Approve Import & Apply**,
which creates and saves four textures plus one material, updates the catalog and
Chroma, and then applies the override. Target revalidation and the scene override
remain transactional; the new Content assets are intentionally persistent.

**Prepare Transform** targets one to 32 selected Actors and accepts one bounded
world- or local-space move, rotation, or scale request. It freezes the ordered
Actor identities, root-component evidence, and current world Transforms before
asking the local planner for a restricted intent. The host applies that intent
to each Actor and shows a separate **Transform Action** card with complete
per-Actor Before/After evidence. **Approve & Apply Transform** revalidates the
complete selection, rejects the entire proposal if any Actor is stale, and
applies one grouped Unreal transaction. It never saves the level automatically;
one Ctrl+Z undoes the batch.

**Prepare Light** targets one to 16 selected Directional, Point, Spot, or Rect
Lights with one compatible group request. The assistant preserves every
intensity unit and exposes only properties supported by the complete selection:
all types support relative intensity, color, temperature, and shadows; local
lights support attenuation; an all-Spot selection supports cone angles; and a
selection without Point Lights supports rotation. The **Light Action** card
shows every selected light and complete per-light Before/After state. **Approve
& Apply Light** revalidates the complete selection before one grouped,
Undo-backed Editor transaction.

**Prepare Lighting Rig** targets exactly five selected Actors: one subject,
one perspective Camera or Cine Camera, and three compatible Movable Point,
Spot, or Rect Lights that share one non-EV intensity unit. The model assigns
the exact lights to Key, Fill, and Rim roles and chooses bounded creative
controls such as contrast, palette, key side, spacing, and brightness. Python
then computes the final camera-relative positions, aim, intensity ratios,
attenuation, and Spot cone coverage from frozen subject bounds and camera
orientation. The **Lighting Rig Action** card shows all three assignments and
complete Before/After values. Approval revalidates all five Actors and changes
only the lights in one Undo-backed transaction; the subject and camera remain
unchanged.

After applying the rig, **Evaluate Applied Rig** temporarily places the active
perspective Level Editor viewport at the frozen camera, captures one Lit PNG,
and immediately restores the user's viewport. The local vision model classifies
only overall exposure, Fill balance, and Rim separation. It cannot choose
numbers or move Actors. The host can create one bounded intensity-only
correction, shown in a separate **Lighting Rig Visual Review** card. Approval
revalidates the subject, camera, and all three lights and creates a second Undo
transaction; the level is never saved automatically.

**Prepare Camera** targets exactly one selected standard Camera or Cine Camera.
Both types support bounded world Transform, aperture, focus distance/mode, and
exposure compensation. Standard Cameras expose FOV; Cine Cameras expose focal
length constrained by the captured lens. The **Camera Action** card shows the
exact type-specific Before/After state. Approval revalidates identity, lens
bounds, and unchanged properties before one Undo-backed Editor transaction.

With 2-16 selected Camera/Cine Camera Actors, the same button prepares one
**Coordinated Camera Action**. One shared restricted intent is compiled against
every frozen Before state, so relative edits preserve shot offsets and absolute
edits converge on a reviewed target. Approval revalidates the complete ordered
selection and applies it as one grouped Undo transaction.

**Review Performance** captures measured evidence for 1-32 selected native
StaticMeshActors. A diagnostic request produces a read-only **Selected Mesh
Performance Review** with field-level evidence. An explicit request may propose
only Cast Shadow and Max Draw Distance changes; the Apply button appears only
for a complete validated Before/After batch. Unreal revalidates every selected
Actor and applies the settings in one Undo transaction. See
[docs/assistant_performance.md](docs/assistant_performance.md).

**Capture Runtime Performance** requires an active PIE/SIE workload. It drops
30 warmup frames, records 120 raw consecutive frame/thread/GPU samples plus
process and RHI texture-memory evidence, and shows a read-only local-model
diagnosis. Python recomputes every timing summary before inference. See
[docs/assistant_runtime_performance.md](docs/assistant_runtime_performance.md).

**Capture GPU Scope Trace** records five seconds of CPU, GPU, frame, and
bookmark channels from an active PIE/SIE workload. UE 5.7 `TraceServices`
parses the saved `.utrace` into ranked queue-local GPU scopes before the local
model reviews them. The card keeps inclusive nested timings explicit and can
open the authoritative file in Unreal Insights. It remains read-only and does
not claim per-Actor or packaged-build attribution. See
[docs/assistant_insights_gpu.md](docs/assistant_insights_gpu.md).

**Measure Selected Actor GPU Impact** requires active PIE/SIE and exactly one
visible selected Actor with registered primitive components. It records a
five-second visible baseline, hides only that Actor's runtime counterpart,
waits one second, records a five-second variant, and restores the Actor before
analysis. The host compares only queue-local scopes present in both Top-64 sets
and normalizes them by actual capture duration. The model may describe impact
candidates, not direct pass/draw/material/shader causation. Both `.utrace`
files remain available from the result card. See
[docs/assistant_actor_gpu_impact.md](docs/assistant_actor_gpu_impact.md).

The secondary **Render & Evaluate** page runs the existing schema-gated
`render-master run` workflow. General geometry-aware arrangement, repeated
Actor trials, direct per-asset/material/shader/draw attribution,
packaged-build comparison, asset-level
performance fixes, multi-shot visual evaluation, and richer shot-specific
lighting refinement remain marked as not connected rather than being
represented by nonfunctional controls.

The execution page writes the prompt to a bounded UTF-8 file, starts the
configured virtual environment without exposing prompt text to the shell,
polls `workflow_manifest.json`, streams child-process logs, and shows the
latest `beauty.png`, visual verdict, and deterministic pixel evidence. Cancel
stops the Python process and its child Unreal render process tree.

Generated workflows, prompts, previews, logs, Chroma data, and models remain
outside this repository. See [docs/unreal_editor_panel.md](docs/unreal_editor_panel.md)
for installation, configuration, lifecycle, and verification details.

## Data separation

Keep source code in this repository. Store model weights, Chroma databases, generated renders, experiment logs, and private assets outside Git. Set `RENDERMASTER_DATA_DIR` to choose that local data root.

Recommended local data root:

```text
C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data
```

## Run tests

```powershell
python -m pytest -q
```

See [docs/architecture.md](docs/architecture.md) for the design boundaries and next milestones.
The public AI/engine contracts are documented in [docs/contracts.md](docs/contracts.md).
The deterministic scene rules are documented in [docs/preflight.md](docs/preflight.md).
Asset-bounds camera framing is documented in [docs/camera_framing.md](docs/camera_framing.md).
Deterministic camera exposure is documented in [docs/exposure.md](docs/exposure.md).
Unreal capability discovery is documented in [docs/unreal_probe.md](docs/unreal_probe.md).
Real Asset Registry scanning is documented in [docs/unreal_assets.md](docs/unreal_assets.md).
Automated PBR material import is documented in [docs/unreal_materials.md](docs/unreal_materials.md).
External CC0 discovery, approval, import, and indexing are documented in [docs/external_materials.md](docs/external_materials.md).
Transient scene execution is documented in [docs/unreal_execution.md](docs/unreal_execution.md).
Offscreen preview runs are documented in [docs/unreal_preview.md](docs/unreal_preview.md).
Local vision evaluation is documented in [docs/visual_evaluation.md](docs/visual_evaluation.md).
Visual evaluator accuracy and stability benchmarking is documented in [docs/visual_benchmark.md](docs/visual_benchmark.md).
Bounded end-to-end execution is documented in [docs/orchestration.md](docs/orchestration.md).
Bounded correction planning is documented in [docs/correction_planning.md](docs/correction_planning.md).
Chroma indexing and retrieval constraints are documented in [docs/retrieval.md](docs/retrieval.md).
The approval-gated selected-Actor material action is documented in [docs/assistant_materials.md](docs/assistant_materials.md).
Approval-gated Camera and Cine Camera actions are documented in [docs/assistant_cameras.md](docs/assistant_cameras.md).
Coordinated multi-camera actions are documented in [docs/assistant_camera_batches.md](docs/assistant_camera_batches.md).
The approval-gated selected-Actor Transform action is documented in [docs/assistant_transforms.md](docs/assistant_transforms.md).
The approval-gated selected-Light action is documented in [docs/assistant_lights.md](docs/assistant_lights.md).
The approval-gated three-point lighting rig is documented in [docs/assistant_lighting_rigs.md](docs/assistant_lighting_rigs.md).
The camera-view visual review and intensity-only refinement are documented in [docs/assistant_lighting_reviews.md](docs/assistant_lighting_reviews.md).
The controlled selected-Actor GPU impact experiment is documented in [docs/assistant_actor_gpu_impact.md](docs/assistant_actor_gpu_impact.md).
The native Unreal dashboard is documented in [docs/unreal_editor_panel.md](docs/unreal_editor_panel.md).

Export or validate any contract:

```powershell
render-master schema technique-card
render-master validate examples/asset_card.json --contract asset-card
```
