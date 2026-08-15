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

When `image_statistics.json` exists, its stored PNG SHA-256 must also match the
verified beauty artifact. Those statistics become correction evidence rather
than optional model commentary.

Any disagreement fails before the model is called.

## Allowed patch surface

Only JSON `replace` operations against fields already present in the evaluated
RenderSpec are accepted:

- camera transform and lens/focus settings;
- existing object transforms;
- complete material-assignment lists for existing objects, using only supplied
  catalog material IDs and the target mesh's observed slot names;
- existing light transforms, intensity, color, and shadow state.

Primary mesh asset references, object IDs, scene identity, prompts, schema
metadata, new objects, new lights, and material invention are forbidden. Each
model path is checked against both a fixed pattern and the exact paths that
exist in the source RenderSpec. The finished patch is then applied by the
deterministic patch engine, the entire resulting RenderSpec is revalidated, and
all mesh, material, and slot references are resolved against the run's catalog.
Render resolution, format, quality, and seed are not correction controls. Patch
value rules explicitly state numeric ranges, vector shapes, booleans, material
assignment structure, and the photographic rule that lower EV100 brightens an
image while higher EV100 darkens it.

Healthy center luminance with neither an underexposed nor overexposed flag
forbids a global exposure patch. For an underexposed fixed-EV image, the new EV
must be lower; for an overexposed image, it must be higher. End-to-end workflows
using deterministic framing also remove camera location, rotation, focal length,
and focus distance from the model's allowed paths.

Malformed or truncated correction JSON receives one concise format-only retry.
A schema-valid decision rejected by path or deterministic exposure evidence can
instead receive one semantic retry containing the exact host rejection reason.
The first response and retry reason are preserved. A second violation, invented
asset, invalid enum value, no-op replacement, or patch application failure is
never silently accepted.

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

That baseline predates material-assignment support. The contract and adapter can
now apply catalog-backed materials, but the same preview still requires a
suitable wood material in its bounded asset catalog. Once such an asset is
retrieved, the run can enter the first automatic rerender-and-compare loop.
