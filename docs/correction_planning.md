# Bounded correction planning

`render-master plan-correction` converts a verified preview
`EvaluationReport` into one of two explicit outcomes:

- `patch`: a non-empty `RenderSpecPatch` that the host has already applied and
  revalidated as a complete `RenderSpec`;
- `unresolved`: no patch, plus one or more capabilities that the current
  contract or adapter lacks.

The first implementation uses the configured text planner, normally
`gpt-oss:20b`, through the local Ollama REST API.

## Why this is a separate stage

The vision model observes the rendered image; it does not receive authority to
edit the scene. Keeping correction separate gives the text planner the full
validated RenderSpec, EvaluationReport, bounded asset evidence, and exact list
of legal replacement paths. The host, not either model, owns hashes, model
identity, patch construction, patch application, and final validation.

## Input verification

Before inference, the host requires:

1. a succeeded `RunManifest`;
2. exactly one hashed RenderSpec, AssetCard catalog, and beauty preview;
3. all artifact paths to remain inside the run directory;
4. every artifact SHA-256 to match its manifest record;
5. matching canonical RenderSpec identities in the manifest and report;
6. a preview-stage report pointing to the exact beauty artifact.

Any disagreement fails before the model is called.

## Allowed patch surface

Only JSON `replace` operations against fields already present in the evaluated
RenderSpec are accepted:

- camera transform and lens/focus settings;
- existing object transforms;
- existing light transforms, intensity, color, and shadow state;
- render width, height, quality, and seed.

Asset references, object IDs, scene identity, prompts, schema metadata, new
objects, new lights, and material invention are forbidden. Each model path is
checked against both a fixed pattern and the exact paths that exist in the
source RenderSpec. The finished patch is then applied by the deterministic
patch engine and the entire resulting RenderSpec is revalidated.

## Usage and outputs

```powershell
render-master plan-correction `
  "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\preview-005"
```

The command writes run-relative outputs without overwriting existing files:

```text
correction.json
correction_metrics.json
corrected_render_spec.json  # only when outcome is patch
```

## First real result

The first real `gpt-oss:20b` correction pass consumed the failed `preview-005`
evaluation in 33.85 seconds. It correctly returned `unresolved` with the
missing capability `material`: camera, light, transform, and render changes
cannot add the wood appearance requested by the prompt. It emitted zero patch
operations and no corrected RenderSpec, so Unreal was not rerun under a false
claim of repair.

The next capability milestone is to retrieve a suitable material, represent a
bounded material assignment in RenderSpec, and implement that assignment in
the Unreal adapter. Only then should the same preview enter an automatic
rerender-and-compare loop.
