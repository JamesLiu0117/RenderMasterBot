"""Deterministic first-preview exposure and key-light calibration."""

from __future__ import annotations

from dataclasses import dataclass

from render_master_bot.contracts import PatchOperation, RenderSpecPatch
from render_master_bot.models import RenderSpec
from render_master_bot.patching import apply_render_spec_patch
from render_master_bot.serialization import canonical_sha256


DEFAULT_STUDIO_EV100 = 9.0
DEFAULT_DIRECTIONAL_LUX = 20_000.0


@dataclass(frozen=True, slots=True)
class StudioCalibrationResult:
    spec: RenderSpec
    patch: RenderSpecPatch | None


def calibrate_studio_preview(
    spec: RenderSpec,
    *,
    fixed_ev100: float = DEFAULT_STUDIO_EV100,
    minimum_directional_lux: float = DEFAULT_DIRECTIONAL_LUX,
) -> StudioCalibrationResult:
    """Make the first product preview repeatable without inventing new lights."""

    operations: list[PatchOperation] = []
    exposure = spec.camera.exposure
    if exposure.mode != "fixed" or exposure.fixed_ev100 != fixed_ev100:
        operations.append(PatchOperation(
            op="replace",
            path="/camera/exposure",
            value={"mode": "fixed", "fixed_ev100": fixed_ev100},
        ))
    for index, light in enumerate(spec.lights):
        if light.kind == "directional" and light.intensity < minimum_directional_lux:
            operations.append(PatchOperation(
                op="replace",
                path=f"/lights/{index}/intensity",
                value=minimum_directional_lux,
            ))
    if not operations:
        return StudioCalibrationResult(spec=spec, patch=None)

    patch = RenderSpecPatch(
        base_spec_sha256=canonical_sha256(spec),
        rationale=(
            f"Calibrate the first local product preview to fixed EV100 {fixed_ev100:g} "
            f"and ensure existing directional lights provide at least "
            f"{minimum_directional_lux:g} lux. No lights or assets are invented."
        ),
        proposed_by={"provider": "local", "model": "studio_calibration_v1"},
        operations=operations,
    )
    return StudioCalibrationResult(
        spec=apply_render_spec_patch(spec, patch),
        patch=patch,
    )
