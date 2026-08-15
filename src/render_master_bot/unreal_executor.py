"""Validated RenderSpec execution boundary for Unreal Editor."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from render_master_bot.asset_index import AssetIndexError, load_asset_card_catalog
from render_master_bot.contracts import AssetCard, Sha256
from render_master_bot.models import (
    Camera,
    DEFAULT_SENSOR_WIDTH_MM,
    Identifier,
    Light,
    PositiveFiniteFloat,
    RenderSettings,
    RenderSpec,
    StrictModel,
    Transform,
)
from render_master_bot.preflight import run_preflight
from render_master_bot.serialization import canonical_sha256


class UnrealSceneBuildError(RuntimeError):
    """Raised when a RenderSpec cannot be staged safely inside Unreal."""


class ResolvedMaterialAssignment(StrictModel):
    """Catalog-backed material assignment with a deterministic Unreal slot index."""

    slot_name: str = Field(min_length=1, max_length=120)
    slot_index: int = Field(ge=0, le=63)
    asset_id: Identifier
    engine_path: str = Field(min_length=1, max_length=500)
    asset_type: Literal["material"]


class ResolvedSceneObject(StrictModel):
    """Scene object whose public asset ID has been resolved to an Unreal path."""

    object_id: Identifier
    asset_id: Identifier
    engine_path: str = Field(min_length=1, max_length=500)
    asset_type: Literal["static_mesh"]
    transform: Transform
    visible: bool
    materials: list[ResolvedMaterialAssignment] = Field(default_factory=list, max_length=64)


class UnrealSceneBuildRequest(StrictModel):
    """Private, deterministic request consumed by the bundled Unreal script."""

    schema_version: Literal["0.1"] = "0.1"
    render_spec_sha256: Sha256
    scene_name: Identifier
    objects: list[ResolvedSceneObject] = Field(max_length=256)
    camera: Camera
    lights: list[Light] = Field(max_length=64)
    render: RenderSettings
    sensor_width_mm: PositiveFiniteFloat = DEFAULT_SENSOR_WIDTH_MM


class AppliedMaterialRecord(StrictModel):
    """Material assignment observed on the spawned Unreal component."""

    slot_name: str = Field(min_length=1, max_length=120)
    slot_index: int = Field(ge=0, le=63)
    asset_id: Identifier
    engine_path: str = Field(min_length=1, max_length=500)
    asset_type: Literal["material"]


class AppliedExposureRecord(StrictModel):
    """Camera exposure state observed after Unreal post-process configuration."""

    mode: Literal["auto", "fixed"]
    fixed_ev100: float | None = Field(default=None, ge=-20.0, le=30.0)
    extended_luminance_range: bool

    @model_validator(mode="after")
    def value_matches_mode(self) -> "AppliedExposureRecord":
        if self.mode == "fixed" and self.fixed_ev100 is None:
            raise ValueError("fixed exposure evidence requires fixed_ev100")
        if self.mode == "auto" and self.fixed_ev100 is not None:
            raise ValueError("auto exposure evidence cannot contain fixed_ev100")
        return self


class SpawnedActorRecord(StrictModel):
    """Observed actor state returned by Unreal after scene construction."""

    actor_id: Identifier
    actor_kind: Literal[
        "static_mesh",
        "camera",
        "directional",
        "point",
        "spot",
        "rect",
    ]
    actor_name: str = Field(min_length=1, max_length=240)
    class_name: str = Field(min_length=1, max_length=240)
    transform: Transform
    materials: list[AppliedMaterialRecord] = Field(default_factory=list, max_length=64)
    exposure: AppliedExposureRecord | None = None


class UnrealSceneBuildResult(StrictModel):
    """Versioned evidence written by the Unreal-side scene builder."""

    schema_version: Literal["0.1"] = "0.1"
    status: Literal["succeeded", "failed"]
    project_name: str = Field(min_length=1, max_length=240)
    world_name: str | None = Field(default=None, max_length=500)
    scene_name: Identifier
    render_spec_sha256: Sha256
    actors: list[SpawnedActorRecord] = Field(default_factory=list, max_length=321)
    warnings: list[str] = Field(default_factory=list, max_length=64)
    errors: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def status_matches_errors(self) -> "UnrealSceneBuildResult":
        if self.status == "succeeded" and self.errors:
            raise ValueError("a successful Unreal scene build cannot contain errors")
        if self.status == "failed" and not self.errors:
            raise ValueError("a failed Unreal scene build must contain at least one error")
        ids = [actor.actor_id for actor in self.actors]
        duplicates = sorted({actor_id for actor_id in ids if ids.count(actor_id) > 1})
        if duplicates:
            raise ValueError("duplicate spawned actor IDs: " + ", ".join(duplicates))
        return self


def _safe_unreal_asset_path(
    card: AssetCard,
    *,
    expected_type: Literal["static_mesh", "material"],
) -> str:
    if card.engine != "unreal":
        raise UnrealSceneBuildError(
            f"asset {card.asset_id!r} targets {card.engine!r}, not Unreal"
        )
    if card.asset_type != expected_type:
        raise UnrealSceneBuildError(
            f"asset {card.asset_id!r} has type {card.asset_type!r}, "
            f"not required type {expected_type!r}"
        )
    path = card.engine_path.strip()
    if not path.startswith("/Game/") or "\\" in path or ".." in path:
        raise UnrealSceneBuildError(
            f"asset {card.asset_id!r} has an unsafe Unreal package path: {path!r}"
        )
    return path


def resolve_scene_build_request(
    spec: RenderSpec,
    cards: list[AssetCard],
    *,
    fail_on_warning: bool = False,
) -> UnrealSceneBuildRequest:
    """Resolve public asset IDs after deterministic validation and preflight."""

    preflight = run_preflight(spec)
    if preflight.verdict == "fail" or (
        fail_on_warning and preflight.verdict == "needs_review"
    ):
        issue_ids = ", ".join(issue.issue_id for issue in preflight.issues) or "unknown"
        raise UnrealSceneBuildError(
            f"RenderSpec preflight returned {preflight.verdict}: {issue_ids}"
        )

    catalog: dict[str, AssetCard] = {}
    for card in cards:
        if card.asset_id in catalog:
            raise UnrealSceneBuildError(f"duplicate AssetCard ID: {card.asset_id}")
        catalog[card.asset_id] = card

    resolved: list[ResolvedSceneObject] = []
    for scene_object in spec.objects:
        card = catalog.get(scene_object.asset.asset_id)
        if card is None:
            raise UnrealSceneBuildError(
                f"RenderSpec object {scene_object.object_id!r} references missing asset "
                f"{scene_object.asset.asset_id!r}"
            )
        material_assignments: list[ResolvedMaterialAssignment] = []
        for assignment in scene_object.materials:
            if assignment.slot_name not in card.material_slots:
                available = ", ".join(card.material_slots) or "none"
                raise UnrealSceneBuildError(
                    f"RenderSpec object {scene_object.object_id!r} references missing material "
                    f"slot {assignment.slot_name!r}; available slots: {available}"
                )
            material_card = catalog.get(assignment.material.asset_id)
            if material_card is None:
                raise UnrealSceneBuildError(
                    f"RenderSpec object {scene_object.object_id!r} references missing material "
                    f"asset {assignment.material.asset_id!r}"
                )
            material_assignments.append(
                ResolvedMaterialAssignment(
                    slot_name=assignment.slot_name,
                    slot_index=card.material_slots.index(assignment.slot_name),
                    asset_id=material_card.asset_id,
                    engine_path=_safe_unreal_asset_path(
                        material_card,
                        expected_type="material",
                    ),
                    asset_type="material",
                )
            )
        resolved.append(
            ResolvedSceneObject(
                object_id=scene_object.object_id,
                asset_id=card.asset_id,
                engine_path=_safe_unreal_asset_path(card, expected_type="static_mesh"),
                asset_type="static_mesh",
                transform=scene_object.transform,
                visible=scene_object.visible,
                materials=material_assignments,
            )
        )

    return UnrealSceneBuildRequest(
        render_spec_sha256=canonical_sha256(spec),
        scene_name=spec.scene_name,
        objects=resolved,
        camera=spec.camera,
        lights=spec.lights,
        render=spec.render,
    )


def load_scene_build_result(path: str | Path) -> UnrealSceneBuildResult:
    """Read and strictly validate evidence emitted from Unreal."""

    result_path = Path(path)
    try:
        return UnrealSceneBuildResult.model_validate_json(
            result_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, ValidationError) as exc:
        raise UnrealSceneBuildError(
            f"invalid Unreal scene build result at {result_path}: {exc}"
        ) from exc


def _validate_observed_result(
    request: UnrealSceneBuildRequest,
    result: UnrealSceneBuildResult,
) -> None:
    if result.status != "succeeded":
        raise UnrealSceneBuildError("Unreal scene build failed: " + "; ".join(result.errors))
    if result.render_spec_sha256 != request.render_spec_sha256:
        raise UnrealSceneBuildError("Unreal returned a different RenderSpec SHA-256")
    if result.scene_name != request.scene_name:
        raise UnrealSceneBuildError("Unreal returned a different scene name")

    expected_kinds = {item.object_id: "static_mesh" for item in request.objects}
    expected_kinds[request.camera.camera_id] = "camera"
    expected_kinds.update({light.light_id: light.kind for light in request.lights})
    observed_kinds = {actor.actor_id: actor.actor_kind for actor in result.actors}
    if observed_kinds != expected_kinds:
        missing = sorted(set(expected_kinds) - set(observed_kinds))
        unexpected = sorted(set(observed_kinds) - set(expected_kinds))
        wrong_kind = sorted(
            actor_id
            for actor_id in set(expected_kinds) & set(observed_kinds)
            if expected_kinds[actor_id] != observed_kinds[actor_id]
        )
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        if wrong_kind:
            details.append("wrong_kind=" + ",".join(wrong_kind))
        raise UnrealSceneBuildError(
            "Unreal actor evidence does not match the request: " + "; ".join(details)
        )

    expected_transforms = {item.object_id: item.transform for item in request.objects}
    expected_transforms[request.camera.camera_id] = request.camera.transform
    expected_transforms.update(
        {light.light_id: light.transform for light in request.lights}
    )
    observed_transforms = {actor.actor_id: actor.transform for actor in result.actors}
    mismatched = sorted(
        actor_id
        for actor_id, expected in expected_transforms.items()
        if not _transforms_equivalent(expected, observed_transforms[actor_id])
    )
    if mismatched:
        raise UnrealSceneBuildError(
            "Unreal actor transforms do not match the request: " + ", ".join(mismatched)
        )

    observed_actors = {actor.actor_id: actor for actor in result.actors}
    material_mismatches = sorted(
        item.object_id
        for item in request.objects
        if [material.model_dump(mode="json") for material in item.materials]
        != [
            material.model_dump(mode="json")
            for material in observed_actors[item.object_id].materials
        ]
    )
    if material_mismatches:
        raise UnrealSceneBuildError(
            "Unreal material evidence does not match the request: "
            + ", ".join(material_mismatches)
        )

    camera_actor = observed_actors[request.camera.camera_id]
    exposure = camera_actor.exposure
    expected_exposure = request.camera.exposure
    if exposure is None or exposure.mode != expected_exposure.mode:
        raise UnrealSceneBuildError("Unreal camera exposure evidence does not match the request")
    if expected_exposure.mode == "fixed":
        if (
            not exposure.extended_luminance_range
            or exposure.fixed_ev100 is None
            or expected_exposure.fixed_ev100 is None
            or not _close(exposure.fixed_ev100, expected_exposure.fixed_ev100)
        ):
            raise UnrealSceneBuildError(
                "Unreal fixed EV100 evidence does not match the request"
            )
    elif exposure.fixed_ev100 is not None:
        raise UnrealSceneBuildError("Unreal auto-exposure evidence contains a fixed EV100")


def _close(first: float, second: float, *, tolerance: float = 1e-3) -> bool:
    return abs(first - second) <= tolerance


def _angles_equivalent(first: float, second: float, *, tolerance: float = 1e-3) -> bool:
    difference = (first - second + 180.0) % 360.0 - 180.0
    return abs(difference) <= tolerance


def _transforms_equivalent(expected: Transform, observed: Transform) -> bool:
    return all(
        (
            _close(expected.location_cm.x, observed.location_cm.x),
            _close(expected.location_cm.y, observed.location_cm.y),
            _close(expected.location_cm.z, observed.location_cm.z),
            _angles_equivalent(expected.rotation_deg.x, observed.rotation_deg.x),
            _angles_equivalent(expected.rotation_deg.y, observed.rotation_deg.y),
            _angles_equivalent(expected.rotation_deg.z, observed.rotation_deg.z),
            _close(expected.scale.x, observed.scale.x),
            _close(expected.scale.y, observed.scale.y),
            _close(expected.scale.z, observed.scale.z),
        )
    )


def _diagnostic_output(completed: subprocess.CompletedProcess[str]) -> str:
    lines = [
        line
        for line in (completed.stdout + "\n" + completed.stderr).splitlines()
        if "Error:" in line or "RENDERMASTER" in line or "LogPython: Error" in line
    ]
    return "\n".join(lines)[-4000:]


def run_unreal_scene_build(
    uproject_path: str | Path,
    *,
    engine_root: str | Path,
    render_spec_path: str | Path,
    asset_catalog_path: str | Path,
    output: str | Path,
    timeout_seconds: int = 300,
    fail_on_warning: bool = False,
) -> tuple[UnrealSceneBuildRequest, UnrealSceneBuildResult]:
    """Launch Unreal, build a transient scene, and verify returned actor evidence."""

    project = Path(uproject_path).expanduser().resolve()
    root = Path(engine_root).expanduser().resolve()
    spec_path = Path(render_spec_path).expanduser().resolve()
    catalog_path = Path(asset_catalog_path).expanduser().resolve()
    result_path = Path(output).expanduser().resolve()
    if project.suffix.casefold() != ".uproject" or not project.is_file():
        raise UnrealSceneBuildError(f"not an existing .uproject file: {project}")
    if not 1 <= timeout_seconds <= 3600:
        raise UnrealSceneBuildError("timeout must be between 1 and 3600 seconds")

    editor = root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
    if not editor.is_file():
        raise UnrealSceneBuildError(f"UnrealEditor-Cmd.exe not found under {root}")
    script = Path(__file__).parent / "unreal_scripts" / "scene_build.py"
    if not script.is_file():
        raise UnrealSceneBuildError(f"bundled Unreal scene builder is missing: {script}")

    try:
        spec = RenderSpec.model_validate_json(spec_path.read_text(encoding="utf-8-sig"))
        cards = load_asset_card_catalog(catalog_path)
    except (OSError, ValidationError, AssetIndexError) as exc:
        raise UnrealSceneBuildError(f"cannot prepare Unreal scene build inputs: {exc}") from exc
    request = resolve_scene_build_request(
        spec,
        cards,
        fail_on_warning=fail_on_warning,
    )

    try:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        if result_path.exists():
            result_path.unlink()
    except OSError as exc:
        raise UnrealSceneBuildError(
            f"cannot prepare scene build output {result_path}: {exc}"
        ) from exc

    with tempfile.TemporaryDirectory(prefix="render-master-unreal-") as directory:
        request_path = Path(directory) / "scene_build_request.json"
        request_path.write_text(
            request.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.update(
            {
                "RENDERMASTER_SCENE_BUILD_REQUEST": str(request_path),
                "RENDERMASTER_SCENE_BUILD_OUTPUT": str(result_path),
            }
        )
        command = [
            str(editor),
            str(project),
            f"-ExecutePythonScript={script}",
            "-unattended",
            "-nop4",
            "-nosplash",
            "-RenderOffscreen",
            "-stdout",
            "-FullStdOutLogOutput",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UnrealSceneBuildError(f"failed to run Unreal scene build: {exc}") from exc

    if not result_path.is_file():
        raise UnrealSceneBuildError(
            f"Unreal scene build produced no result (exit {completed.returncode}): "
            f"{_diagnostic_output(completed)}"
        )
    result = load_scene_build_result(result_path)
    if completed.returncode != 0:
        raise UnrealSceneBuildError(
            f"Unreal scene build exited with code {completed.returncode}: "
            f"{_diagnostic_output(completed)}"
        )
    _validate_observed_result(request, result)
    return request, result
