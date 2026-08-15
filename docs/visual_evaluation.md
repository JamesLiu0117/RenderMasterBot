# Local preview evaluation

`render-master evaluate-preview` sends one verified Unreal beauty preview to
the configured local vision model and emits a strict `EvaluationReport`. The
first adapter uses `qwen3.5:9b` through the local Ollama REST API.

## Trust boundary

The run directory, not an arbitrary image path, is the evaluator input. Before
model inference, the host:

1. validates `run_manifest.json` and requires terminal status `succeeded`;
2. requires exactly one `render_spec` input and one `beauty_preview` output;
3. resolves both artifact paths inside the run directory;
4. recalculates and compares both file SHA-256 values;
5. validates the complete RenderSpec and compares its canonical identity with
   `RunManifest.render_spec_sha256`;
6. accepts only a PNG signature and a maximum image size of 20 MiB.

Only then is the PNG base64 encoded for Ollama. The evaluator has no URL, file
search, Unreal command, or tool-calling interface.

## Model-owned and host-owned fields

The model returns a private `VisualEvaluationDraft` containing only:

- verdict and summary;
- at most 16 structured issues;
- issue category, severity, confidence, message, and known scene object IDs.

The host supplies the trusted public envelope:

- canonical RenderSpec SHA-256;
- actual model identity returned by Ollama;
- `evaluation_stage="preview"`;
- preview and per-issue evidence paths.

Any unknown object ID, duplicate issue ID, invalid verdict/severity combination,
or schema violation rejects the response. The first version intentionally does
not accept model-generated patches. Correction planning is a separate stage so
its operations can be validated and benchmarked independently.

After evaluation, `render-master plan-correction` consumes the public report
and emits a `CorrectionDecision`. See `correction_planning.md` for the repair
boundary and current material limitation.

## Ollama request

The REST request sends the base64 image in the user message's `images` array,
passes the Pydantic-derived JSON Schema in `format`, disables streaming, and
sets temperature to zero. The prompt also contains the exact RenderSpec, the
allowed object IDs, and the output schema.

## Usage

```powershell
render-master evaluate-preview `
  "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\preview-005"
```

By default the command writes these files inside the run directory:

```text
evaluation.json
evaluation_metrics.json
```

Existing files are never overwritten. `--output` and `--metrics-output` accept
alternative paths only when they remain inside the run directory.

## First real baseline

The initial UE 5.7 preview evaluation completed with `qwen3.5:9b` in 84.93
seconds. It produced 146 output tokens from 5,780 prompt tokens and correctly
reported a blocking material problem: the requested wooden door appeared as a
featureless grey surface without visible wood detail. The resulting public
`EvaluationReport` passed independent contract validation.
