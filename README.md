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
- Type-filtered Chroma retrieval that rejects assets outside the returned catalog
- Catalog-backed per-slot material assignments with host and Unreal evidence validation
- Automated four-map PBR material import with frozen source hashes and no-overwrite behavior
- Verified UE 5.7 material override in both transient scene build and MRQ preview paths
- Verified CC0 wood-material acquisition, Unreal import, multilingual retrieval, and planner assignment
- Verified planner refusal to hide a remaining geometry gap after a material becomes available
- Deterministic AssetCard-bounds camera framing through auditable `RenderSpecPatch` output
- Explicit six-axis product-view framing, including top-down material inspection
- Backward-compatible automatic or fixed-EV100 camera exposure with Unreal readback evidence
- Transient Unreal scene construction for static meshes, camera, and physical lights
- One-frame Unreal Movie Render Queue previews with terminal, artifact-hashed `RunManifest` records
- Verified local Qwen preview evaluation with strict, evidence-linked `EvaluationReport` output
- Verified Qwen3-VL material evaluation after fixed-exposure, top-down UE 5.7 rerender
- Repeatable visual evaluator benchmarking with frozen labels, deterministic PNG evidence, stability scoring, and contradiction detection
- First real four-case Qwen3-VL benchmark: 8/8 valid responses, 1.000 verdict stability, 0.750 case accuracy, and one preserved hard-negative contradiction
- Bounded local correction planning that emits a validated patch or an explicit capability gap
- Command-line doctor, schema, validate, preflight, Unreal, retrieval, planning, evaluation, and correction commands
- Standard-library unit tests for contracts, planning, preflight, Unreal execution, and preview orchestration

Broader scene editing, evaluator benchmarking, Unreal Editor UI integration, and model
fine-tuning are later milestones.

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

Build a validated scene as transient actors without saving or modifying project Content:

```powershell
render-master unreal-build-scene `
  "E:\OptimizationPlugin\OptimizationPlugin.uproject" `
  --engine-root "E:\Unreal Engine\UE_5.7" `
  --spec "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\retrieval-001\door_plan.json" `
  --assets "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\asset_cards.json" `
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

Use `--model` only to benchmark or override the configured planner.

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
Transient scene execution is documented in [docs/unreal_execution.md](docs/unreal_execution.md).
Offscreen preview runs are documented in [docs/unreal_preview.md](docs/unreal_preview.md).
Local vision evaluation is documented in [docs/visual_evaluation.md](docs/visual_evaluation.md).
Visual evaluator accuracy and stability benchmarking is documented in [docs/visual_benchmark.md](docs/visual_benchmark.md).
Bounded correction planning is documented in [docs/correction_planning.md](docs/correction_planning.md).
Chroma indexing and retrieval constraints are documented in [docs/retrieval.md](docs/retrieval.md).

Export or validate any contract:

```powershell
render-master schema technique-card
render-master validate examples/asset_card.json --contract asset-card
```
