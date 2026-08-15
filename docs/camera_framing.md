# Deterministic camera framing

`render-master frame-camera` fits every visible object with known AssetCard
bounds into the requested output frame. It produces both a new `RenderSpec` and
the exact `RenderSpecPatch` used to create it; the source specification is never
modified in place.

## Why this is deterministic

For every visible scene object, the framing stage:

1. resolves its public asset ID against the supplied AssetCard catalog;
2. requires `dimensions_cm` instead of guessing missing bounds;
3. constructs all eight local bounding-box corners around `pivot_offset_cm`;
4. applies the RenderSpec scale and Unreal roll/pitch/yaw rotation;
5. transforms the corners into world space and finds the combined bounds;
6. preserves the original direction, snaps the planned side to the nearest
   horizontal product axis, or uses an explicitly requested world axis;
7. solves the minimum perspective distance that keeps every corner inside the
   horizontal and vertical image limits, including the requested edge margin.

The projection convention is fixed: 36 mm sensor width, sensor height derived
from `render.width_px / render.height_px`, and the RenderSpec focal length. The
same 36 mm value is written into the private Unreal execution request and
applied to the `CineCameraComponent` filmback, so planning math and engine
projection cannot silently diverge.

The standalone command defaults to `--margin 0.1` and `--view-axis preserve`.
The complete `render-master run` workflow defaults to a tighter 2% edge margin
and `auto-product`. A square or tall subject in a 16:9 image naturally has more
horizontal than vertical space because the tighter vertical constraint
determines camera distance.

## Patch boundary

The generated patch contains only the changed fields, selected from:

- replace `/camera/transform/location_cm`;
- replace `/camera/transform/rotation_deg`;
- replace `/camera/focus_distance_cm`.

Its `base_spec_sha256` must match the source RenderSpec exactly. The shared
patch application layer rejects a hash mismatch, missing JSON Pointer targets,
out-of-range list operations, malformed pointers, and any result that fails the
complete RenderSpec contract. This is the same boundary intended for future
visual-evaluator corrections.

## Product-view axes

The default `--view-axis preserve` keeps the source camera's viewing direction.
For repeatable product tests, an axis can be selected explicitly:

| Option | Camera position | Looking direction |
| --- | --- | --- |
| `auto-product` | planned side, snapped to nearest horizontal axis | toward bounds center |
| `from-negative-x` | negative X side | toward positive X |
| `from-positive-x` | positive X side | toward negative X |
| `from-negative-y` | negative Y side | toward positive Y |
| `from-positive-y` | positive Y side | toward negative Y |
| `from-negative-z` | below the subject | toward positive Z |
| `from-positive-z` | above the subject | toward negative Z |

An explicit axis changes only the deterministic framing calculation. The
result is still recorded through the same bounded camera patch paths, with
unchanged fields omitted.

## Usage

```powershell
render-master frame-camera `
  "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\retrieval-001\door_plan_v2.json" `
  --assets "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\assets\optimization-plugin\asset_cards.json" `
  --output "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\retrieval-001\door_plan_v3.json" `
  --patch-output "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\runs\retrieval-001\door_plan_v3.patch.json" `
  --view-axis from-negative-x `
  --margin 0.1
```

Input, framed output, and patch output paths must be distinct. The command
fails before producing a result when there are no visible objects, an asset ID
is missing or duplicated, bounds are unavailable, or the margin is outside the
supported range.
