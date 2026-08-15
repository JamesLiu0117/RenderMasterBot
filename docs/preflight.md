# Semantic preflight

`RenderSpec` validation answers: "Is this document structurally legal?"
Semantic preflight answers: "Are these legal values suspicious when combined
as a scene?"

The checker is deterministic and local. It does not call Ollama or Unreal, so
the same `RenderSpec` and checker version always produce the same findings.
Every result is an `EvaluationReport` with `evaluation_stage="preflight"` and
the canonical SHA-256 identity of the evaluated specification.

## Verdict policy

| Findings | Verdict | Default CLI exit code |
| --- | --- | --- |
| any `error` or `blocking` | `fail` | 1 |
| one or more `warning`, no errors | `needs_review` | 0 |
| only `info`, or no issues | `pass` | 0 |

Use `--fail-on-warning` in unattended pipelines to give `needs_review` exit
code 2. This makes warning policy a pipeline decision instead of silently
turning subjective checks into hard failures.

## Version 1 rules

| Rule ID prefix | Severity | Reason |
| --- | --- | --- |
| `duplicate_asset_transform` | error | identical asset instances with identical transforms overlap exactly |
| `camera_at_object_pivot` | warning | camera may start inside geometry |
| `visible_objects_behind_camera` | warning | all visible object pivots are outside the camera's forward hemisphere |
| `no_visible_objects` | warning | most render requests need a visible subject |
| `no_explicit_lights` | warning | level lighting may exist, so this is not a hard error |
| `zero_intensity_lights` | warning | defined lights cannot affect the image |
| `coincident_local_lights` | warning | generated light transforms may have remained at defaults |
| `extreme_object_scale` | warning | scale may be a unit or generation mistake |
| `extreme_axis_scale_ratio` | warning | object may be unintentionally flattened |
| `extreme_focal_length` | info | unusual lenses can be intentional |
| `extreme_aspect_ratio` | info | unusual output formats can be intentional |
| `high_pixel_count` | info | warns about expensive iterative previews |

## Usage

```powershell
render-master preflight examples\simple_studio.json

render-master preflight examples\suspicious_scene.json `
  --output preflight-report.json

render-master preflight examples\suspicious_scene.json `
  --fail-on-warning
```

The first version intentionally avoids rules that require asset bounds, level
lighting knowledge, occlusion, or renderer state. Those checks become reliable
only after the Unreal project probe and `AssetCard` scanner are connected.
