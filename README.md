# RenderMasterBot

RenderMasterBot is a local-first graphics planning system. It turns a natural-language request into a strictly validated `RenderSpec`, which a renderer adapter can later execute in Unreal Engine.

The first milestone intentionally stops at validated scene planning. A language model is not allowed to run arbitrary engine code; it must return a versioned JSON document with known fields and constraints.

## Current milestone

- `RenderSpec` schema version 0.1
- Pydantic validation and JSON Schema export
- Dual-model defaults: `gpt-oss:20b` planning and `qwen3.5:9b` visual evaluation
- Ollama structured-output client over the local REST API
- Deterministic semantic preflight with structured `EvaluationReport` output
- Read-only Unreal project capability probing with evidence provenance
- Command-line doctor, schema, validate, preflight, unreal-probe, and plan commands
- Standard-library unit tests for contracts, planning, and scene preflight

Unreal execution, Chroma asset retrieval, visual evaluation, and model fine-tuning are later milestones.

## Local setup

Create the virtual environment outside this repository if desired, then install this package in editable mode:

```powershell
& "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\.venv\Scripts\python.exe" -m pip install -e "E:\RenderBot\RenderMasterBot"
```

Verify the local Ollama server:

```powershell
render-master doctor
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

Show the resolved dual-model configuration:

```powershell
render-master config
```

Create a plan with the configured planning model:

```powershell
render-master plan --prompt "Create a studio product shot of a red chair"
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

Export or validate any contract:

```powershell
render-master schema technique-card
render-master validate examples/asset_card.json --contract asset-card
```
