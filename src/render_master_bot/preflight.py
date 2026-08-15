"""Deterministic semantic checks for structurally valid RenderSpec values."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from math import cos, dist, radians, sin, sqrt
from typing import Iterable

from render_master_bot.contracts import EvaluationIssue, EvaluationReport
from render_master_bot.models import RenderSpec, Transform, Vector3
from render_master_bot.serialization import canonical_sha256


@dataclass(frozen=True, slots=True)
class PreflightConfig:
    """Conservative thresholds for high-signal, renderer-independent rules."""

    coincidence_distance_cm: float = 1.0
    minimum_reasonable_focal_length_mm: float = 12.0
    maximum_reasonable_focal_length_mm: float = 200.0
    minimum_reasonable_scale: float = 0.01
    maximum_reasonable_scale: float = 100.0
    maximum_scale_axis_ratio: float = 100.0
    minimum_reasonable_aspect_ratio: float = 0.25
    maximum_reasonable_aspect_ratio: float = 4.0
    high_pixel_count: int = 32_000_000


DEFAULT_PREFLIGHT_CONFIG = PreflightConfig()


def _location(vector: Vector3) -> tuple[float, float, float]:
    return (vector.x, vector.y, vector.z)


def _camera_forward(rotation: Vector3) -> tuple[float, float, float]:
    """Return Unreal's +X camera-forward vector for the RenderSpec axis mapping."""

    pitch = radians(rotation.y)
    yaw = radians(rotation.z)
    return (
        cos(pitch) * cos(yaw),
        cos(pitch) * sin(yaw),
        sin(pitch),
    )


def _transform_key(transform: Transform) -> tuple[float, ...]:
    location = transform.location_cm
    rotation = transform.rotation_deg
    scale = transform.scale
    return (
        location.x,
        location.y,
        location.z,
        rotation.x,
        rotation.y,
        rotation.z,
        scale.x,
        scale.y,
        scale.z,
    )


def _bounded_ids(values: Iterable[str]) -> list[str]:
    """Keep reports within the public contract's 64-object evidence limit."""

    return list(dict.fromkeys(values))[:64]


def _issue_id(rule: str, object_ids: Iterable[str] = ()) -> str:
    """Build a stable contract-safe issue identity for the same finding."""

    ids = list(object_ids)
    candidate = "_".join([rule, *ids])
    if len(candidate) <= 64:
        return candidate
    digest = sha1(candidate.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    return f"{rule[:53]}_{digest}"


def _issue(
    rule: str,
    *,
    category: str,
    severity: str,
    confidence: float,
    message: str,
    object_ids: Iterable[str] = (),
) -> EvaluationIssue:
    ids = _bounded_ids(object_ids)
    return EvaluationIssue(
        issue_id=_issue_id(rule, ids),
        category=category,
        severity=severity,
        confidence=confidence,
        message=message,
        object_ids=ids,
    )


def _coincident_groups(
    items: list[tuple[str, tuple[float, float, float]]],
    tolerance: float,
) -> list[list[str]]:
    """Greedily group positions within a small Euclidean tolerance."""

    remaining = list(items)
    groups: list[list[str]] = []
    while remaining:
        anchor_id, anchor = remaining.pop(0)
        group = [anchor_id]
        kept: list[tuple[str, tuple[float, float, float]]] = []
        for item_id, position in remaining:
            if dist(anchor, position) <= tolerance:
                group.append(item_id)
            else:
                kept.append((item_id, position))
        remaining = kept
        if len(group) > 1:
            groups.append(group)
    return groups


def _duplicate_object_issues(spec: RenderSpec) -> list[EvaluationIssue]:
    groups: dict[tuple[str, tuple[float, ...]], list[str]] = {}
    for item in spec.objects:
        if not item.visible:
            continue
        key = (item.asset.asset_id, _transform_key(item.transform))
        groups.setdefault(key, []).append(item.object_id)

    issues: list[EvaluationIssue] = []
    for object_ids in groups.values():
        if len(object_ids) < 2:
            continue
        issues.append(
            _issue(
                "duplicate_asset_transform",
                category="geometry",
                severity="error",
                confidence=1.0,
                message=(
                    f"{len(object_ids)} visible objects use the same asset and exact transform; "
                    "they would render as duplicate overlapping instances."
                ),
                object_ids=object_ids,
            )
        )
        if len(issues) == 32:
            break
    return issues


def run_preflight(
    spec: RenderSpec,
    *,
    config: PreflightConfig = DEFAULT_PREFLIGHT_CONFIG,
) -> EvaluationReport:
    """Evaluate a RenderSpec without invoking a model or renderer."""

    issues: list[EvaluationIssue] = []
    visible_objects = [item for item in spec.objects if item.visible]

    if not visible_objects:
        issues.append(
            _issue(
                "no_visible_objects",
                category="composition",
                severity="warning",
                confidence=1.0,
                message=(
                    "The scene has no visible objects. This may be intentional, but most "
                    "render requests need at least one visible subject."
                ),
            )
        )

    issues.extend(_duplicate_object_issues(spec))

    camera_position = _location(spec.camera.transform.location_cm)
    camera_collisions = [
        item.object_id
        for item in visible_objects
        if dist(camera_position, _location(item.transform.location_cm))
        <= config.coincidence_distance_cm
    ]
    if camera_collisions:
        issues.append(
            _issue(
                "camera_at_object_pivot",
                category="camera",
                severity="warning",
                confidence=0.95,
                message=(
                    "The camera is at or extremely close to one or more visible object pivots; "
                    "the camera may start inside the geometry."
                ),
                object_ids=[spec.camera.camera_id, *camera_collisions],
            )
        )

    camera_forward = _camera_forward(spec.camera.transform.rotation_deg)
    facing_dots: list[tuple[str, float]] = []
    for item in visible_objects:
        object_position = _location(item.transform.location_cm)
        to_object = tuple(
            object_axis - camera_axis
            for object_axis, camera_axis in zip(object_position, camera_position, strict=True)
        )
        distance_to_object = sqrt(sum(axis * axis for axis in to_object))
        if distance_to_object <= config.coincidence_distance_cm:
            continue
        facing_dots.append(
            (
                item.object_id,
                sum(
                    forward_axis * object_axis / distance_to_object
                    for forward_axis, object_axis in zip(
                        camera_forward,
                        to_object,
                        strict=True,
                    )
                ),
            )
        )
    if facing_dots and all(dot <= 0 for _, dot in facing_dots):
        object_ids = [object_id for object_id, _ in facing_dots]
        issues.append(
            _issue(
                "visible_objects_behind_camera",
                category="camera",
                severity="warning",
                confidence=0.99,
                message=(
                    "Every visible object pivot is behind or perpendicular to the camera's "
                    "forward direction; the intended subjects are unlikely to appear."
                ),
                object_ids=[spec.camera.camera_id, *object_ids],
            )
        )

    focal_length = spec.camera.focal_length_mm
    if (
        focal_length < config.minimum_reasonable_focal_length_mm
        or focal_length > config.maximum_reasonable_focal_length_mm
    ):
        issues.append(
            _issue(
                "extreme_focal_length",
                category="camera",
                severity="info",
                confidence=1.0,
                message=(
                    f"The {focal_length:g} mm focal length is outside the conservative "
                    f"{config.minimum_reasonable_focal_length_mm:g}-"
                    f"{config.maximum_reasonable_focal_length_mm:g} mm review range."
                ),
                object_ids=[spec.camera.camera_id],
            )
        )

    if not spec.lights:
        issues.append(
            _issue(
                "no_explicit_lights",
                category="lighting",
                severity="warning",
                confidence=0.9,
                message=(
                    "The RenderSpec defines no lights. Existing level or environment lighting "
                    "may be sufficient, so this requires review rather than automatic failure."
                ),
            )
        )
    else:
        zero_lights = [light.light_id for light in spec.lights if light.intensity == 0]
        if zero_lights:
            issues.append(
                _issue(
                    "zero_intensity_lights",
                    category="lighting",
                    severity="warning",
                    confidence=1.0,
                    message=f"{len(zero_lights)} explicitly defined lights have zero intensity.",
                    object_ids=zero_lights,
                )
            )

        local_lights = [
            (light.light_id, _location(light.transform.location_cm))
            for light in spec.lights
            if light.kind != "directional" and light.intensity > 0
        ]
        for light_ids in _coincident_groups(
            local_lights,
            config.coincidence_distance_cm,
        )[:32]:
            issues.append(
                _issue(
                    "coincident_local_lights",
                    category="lighting",
                    severity="warning",
                    confidence=0.99,
                    message=(
                        f"{len(light_ids)} active local lights occupy the same position; "
                        "this often indicates that generated transforms were left at defaults."
                    ),
                    object_ids=light_ids,
                )
            )

    extreme_scale_ids: list[str] = []
    anisotropic_scale_ids: list[str] = []
    for item in visible_objects:
        axes = (item.transform.scale.x, item.transform.scale.y, item.transform.scale.z)
        outside_scale_range = (
            min(axes) < config.minimum_reasonable_scale
            or max(axes) > config.maximum_reasonable_scale
        )
        if outside_scale_range:
            extreme_scale_ids.append(item.object_id)
        if max(axes) / min(axes) > config.maximum_scale_axis_ratio:
            anisotropic_scale_ids.append(item.object_id)

    if extreme_scale_ids:
        issues.append(
            _issue(
                "extreme_object_scale",
                category="geometry",
                severity="warning",
                confidence=0.95,
                message=(
                    "One or more object scale components fall outside the conservative "
                    f"{config.minimum_reasonable_scale:g}-"
                    f"{config.maximum_reasonable_scale:g} review range."
                ),
                object_ids=extreme_scale_ids,
            )
        )
    if anisotropic_scale_ids:
        issues.append(
            _issue(
                "extreme_axis_scale_ratio",
                category="geometry",
                severity="warning",
                confidence=0.95,
                message=(
                    "One or more objects have a scale-axis ratio above "
                    f"{config.maximum_scale_axis_ratio:g}:1 and may be unintentionally flattened."
                ),
                object_ids=anisotropic_scale_ids,
            )
        )

    aspect_ratio = spec.render.width_px / spec.render.height_px
    if (
        aspect_ratio < config.minimum_reasonable_aspect_ratio
        or aspect_ratio > config.maximum_reasonable_aspect_ratio
    ):
        issues.append(
            _issue(
                "extreme_aspect_ratio",
                category="render_quality",
                severity="info",
                confidence=1.0,
                message=f"The requested output aspect ratio is {aspect_ratio:.3f}:1.",
            )
        )

    pixel_count = spec.render.width_px * spec.render.height_px
    if pixel_count > config.high_pixel_count:
        issues.append(
            _issue(
                "high_pixel_count",
                category="performance",
                severity="info",
                confidence=1.0,
                message=(
                    f"The requested output contains {pixel_count:,} pixels and may be expensive "
                    "for iterative preview rendering."
                ),
            )
        )

    if any(issue.severity in {"error", "blocking"} for issue in issues):
        verdict = "fail"
    elif any(issue.severity == "warning" for issue in issues):
        verdict = "needs_review"
    else:
        verdict = "pass"

    counts = {
        severity: sum(issue.severity == severity for issue in issues)
        for severity in ("blocking", "error", "warning", "info")
    }
    summary = (
        f"Semantic preflight {verdict}: {len(issues)} issue(s) "
        f"({counts['blocking']} blocking, {counts['error']} error, "
        f"{counts['warning']} warning, {counts['info']} info)."
    )
    return EvaluationReport(
        render_spec_sha256=canonical_sha256(spec),
        evaluator={"provider": "local", "model": "semantic_preflight_v1"},
        evaluation_stage="preflight",
        verdict=verdict,
        summary=summary,
        issues=issues,
    )
