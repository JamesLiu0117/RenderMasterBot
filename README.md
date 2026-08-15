# RenderMasterBot

RenderMasterBot is a local-first graphics planning system. It turns a natural-language request into a strictly validated `RenderSpec`, which a renderer adapter can later execute in Unreal Engine.

The integration remains schema-gated: a language model is not allowed to run arbitrary engine code; it must return a versioned JSON document with known fields and constraints.

## Current milestone

- `RenderSpec` schema version 0.1
- Pydantic validation and JSON Schema export
- Dual-model defaults: `gpt-oss:20b` planning and `qwen3.5:9b` visual evaluation
- Ollama structured-output client over the local REST API
- Deterministic semantic preflight with structured `EvaluationReport` output
- Read-only Unreal project capability probing with evidence provenance
- Headless Unreal Asset Registry scanning into validated `AssetCard` records
- Persistent Chroma indexing with explicit local Ollama embeddings
- Retrieval-constrained planning that rejects assets outside the returned catalog
- Transient Unreal scene construction for static meshes, camera, and physical lights
- Command-line doctor, schema, validate, preflight, Unreal, asset-index, asset-search, and plan commands
- Standard-library unit tests for contracts, planning, and scene preflight

Preview rendering, visual evaluation, and model fine-tuning are later milestones.

## Local setup

Create the virtual environment outside this repository if desired, then install this package in editable mode:

```powershell
& "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\.venv\Scripts\python.exe" -m pip install -e "E:\RenderBot\RenderMasterBot"
```

Verify the local Ollama server:

```powershell
render-master doctor
```

Install the lightweight multilingual embedding model used by asset retrieval:

```powershell
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

Synchronize those cards into the local Chroma collection:

```powershell
render-master asset-index `
  "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\asset_cards.json"
```

Test multilingual semantic retrieval:

```powershell
render-master asset-search --query "一扇可以打开的木门" --limit 5
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
python -m unittest discover -s tests -v
```

See [docs/architecture.md](docs/architecture.md) for the design boundaries and next milestones.
The public AI/engine contracts are documented in [docs/contracts.md](docs/contracts.md).
The deterministic scene rules are documented in [docs/preflight.md](docs/preflight.md).
Unreal capability discovery is documented in [docs/unreal_probe.md](docs/unreal_probe.md).
Real Asset Registry scanning is documented in [docs/unreal_assets.md](docs/unreal_assets.md).
Transient scene execution is documented in [docs/unreal_execution.md](docs/unreal_execution.md).
Chroma indexing and retrieval constraints are documented in [docs/retrieval.md](docs/retrieval.md).

Export or validate any contract:

```powershell
render-master schema technique-card
render-master validate examples/asset_card.json --contract asset-card
```
