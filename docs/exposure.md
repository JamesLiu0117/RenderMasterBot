# Deterministic camera exposure

`Camera.exposure` makes the brightness baseline part of the validated
`RenderSpec` instead of leaving it to Unreal's scene-dependent eye adaptation.

## Contract

Automatic exposure remains the backward-compatible default:

```json
{"mode": "auto"}
```

A reproducible preview can request a fixed EV100:

```json
{"mode": "fixed", "fixed_ev100": 12.0}
```

Fixed mode requires `fixed_ev100`; automatic mode forbids it. The accepted
range is -20 through 30, and non-finite numbers are rejected by validation.

Exposure does not replace lighting. Light type, intensity, color, direction,
and shadows still define the subject illumination. Fixed EV100 removes the
adaptive camera response so a lighting change can be compared against a stable
image-brightness reference.

## Unreal mapping and evidence

Fixed EV100 requires the Unreal project setting
`r.DefaultFeature.AutoExposure.ExtendDefaultLuminanceRange=True`. The adapter
sets the Cine Camera post-process blend weight to 1 and sets both histogram
minimum and maximum EV100 to the requested value. Equal bounds disable eye
adaptation while retaining a physically meaningful EV100 input.

The Unreal script reads the two bounds and blend weight back from the camera.
Its result records the observed mode, EV100, and extended-luminance-range state.
The host rejects the run if that evidence differs from the RenderSpec. A zero
Unreal process exit code is therefore not enough to claim fixed exposure was
applied.

Automatic mode sets the camera post-process blend weight to 0 and records an
automatic-exposure result with no fixed EV100.

## Correction boundary

The correction planner may replace `/camera/exposure` when visual evidence
identifies exposure adaptation or global brightness as the direct problem. The
normal patch hash, JSON Pointer allowlist, complete RenderSpec validation, and
artifact identity checks still apply.

The complete workflow applies an auditable first-preview baseline of fixed
EV100 9 and raises existing directional lights below 20,000 lux to that minimum.
It never invents a light. `--no-studio-calibration` disables this baseline for
controlled comparisons.

Each correction consumes deterministic statistics tied to the beauty PNG hash.
When those statistics show healthy center luminance and neither underexposure
nor overexposure, global exposure changes are rejected. For fixed exposure,
underexposure requires a lower EV100 and overexposure requires a higher EV100.
