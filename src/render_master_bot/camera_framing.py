"""Deterministic camera framing from RenderSpec transforms and AssetCard bounds."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from math import atan2, cos, degrees, radians, sin, sqrt
from typing import Literal

from render_master_bot.contracts import AssetCard, PatchOperation, RenderSpecPatch
from render_master_bot.models import DEFAULT_SENSOR_WIDTH_MM, RenderSpec, SceneObject, Vector3
from render_master_bot.patching import apply_render_spec_patch
from render_master_bot.serialization import canonical_sha256


Point3 = tuple[float, float, float]
ViewAxis = Literal[
    "preserve",
    "auto-product",
    "from-negative-x",
    "from-positive-x",
    "from-negative-y",
    "from-positive-y",
    "from-negative-z",
    "from-positive-z",
]
VIEW_AXES: tuple[ViewAxis, ...] = (
    "preserve",
    "auto-product",
    "from-negative-x",
    "from-positive-x",
    "from-negative-y",
    "from-positive-y",
    "from-negative-z",
    "from-positive-z",
)
_VIEW_FORWARDS: dict[ViewAxis, Point3] = {
    "preserve": (0.0, 0.0, 0.0),
    "auto-product": (0.0, 0.0, 0.0),
    "from-negative-x": (1.0, 0.0, 0.0),
    "from-positive-x": (-1.0, 0.0, 0.0),
    "from-negative-y": (0.0, 1.0, 0.0),
    "from-positive-y": (0.0, -1.0, 0.0),
    "from-negative-z": (0.0, 0.0, 1.0),
    "from-positive-z": (0.0, 0.0, -1.0),
}


class CameraFramingError(RuntimeError):
    """Raised when asset evidence is insufficient for deterministic framing."""


@dataclass(frozen=True, slots=True)
class CameraFramingResult:
    spec: RenderSpec
    patch: RenderSpecPatch
    target_cm: Vector3
    distance_cm: float
    object_ids: tuple[str, ...]
    view_axis: ViewAxis


def _add(left: Point3, right: Point3) -> Point3:
    return tuple(a + b for a, b in zip(left, right, strict=True))


def _subtract(left: Point3, right: Point3) -> Point3:
    return tuple(a - b for a, b in zip(left, right, strict=True))


def _multiply(value: Point3, scalar: float) -> Point3:
    return tuple(axis * scalar for axis in value)


def _dot(left: Point3, right: Point3) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _cross(left: Point3, right: Point3) -> Point3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _length(value: Point3) -> float:
    return sqrt(_dot(value, value))


def _normalize(value: Point3) -> Point3:
    length = _length(value)
    if length <= 1e-9:
        raise CameraFramingError("cannot normalize a zero-length framing direction")
    return _multiply(value, 1.0 / length)


def _rotation_basis(rotation: Vector3) -> tuple[Point3, Point3, Point3]:
    """Return Unreal forward/right/up axes for RenderSpec roll/pitch/yaw."""

    roll = radians(rotation.x)
    pitch = radians(rotation.y)
    yaw = radians(rotation.z)
    sr, cr = sin(roll), cos(roll)
    sp, cp = sin(pitch), cos(pitch)
    sy, cy = sin(yaw), cos(yaw)
    forward = (cp * cy, cp * sy, sp)
    right = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    up = (-cr * sp * cy - sr * sy, -cr * sp * sy + sr * cy, cr * cp)
    return forward, right, up


def _world_corners(scene_object: SceneObject, card: AssetCard) -> list[Point3]:
    if card.dimensions_cm is None:
        raise CameraFramingError(f"asset {card.asset_id!r} has no dimensions_cm evidence")
    dimensions = card.dimensions_cm
    center = card.pivot_offset_cm
    scale = scene_object.transform.scale
    location = scene_object.transform.location_cm
    forward, right, up = _rotation_basis(scene_object.transform.rotation_deg)
    corners: list[Point3] = []
    for signs in product((-1.0, 1.0), repeat=3):
        local = (
            (center.x + signs[0] * dimensions.x / 2.0) * scale.x,
            (center.y + signs[1] * dimensions.y / 2.0) * scale.y,
            (center.z + signs[2] * dimensions.z / 2.0) * scale.z,
        )
        rotated = _add(
            _add(_multiply(forward, local[0]), _multiply(right, local[1])),
            _multiply(up, local[2]),
        )
        corners.append(_add((location.x, location.y, location.z), rotated))
    return corners


def _rounded_vector(value: Point3) -> dict[str, float]:
    return {axis: round(component, 6) for axis, component in zip("xyz", value, strict=True)}


def _automatic_product_forward(spec: RenderSpec, target: Point3) -> Point3:
    """Snap the planned camera side to the nearest horizontal world axis."""

    location = spec.camera.transform.location_cm
    toward_target = (target[0] - location.x, target[1] - location.y, 0.0)
    if _length(toward_target) <= 1e-9:
        planned_forward = _rotation_basis(spec.camera.transform.rotation_deg)[0]
        toward_target = (planned_forward[0], planned_forward[1], 0.0)
    if _length(toward_target) <= 1e-9:
        return (1.0, 0.0, 0.0)
    if abs(toward_target[0]) > abs(toward_target[1]):
        return (1.0 if toward_target[0] > 0 else -1.0, 0.0, 0.0)
    return (0.0, 1.0 if toward_target[1] > 0 else -1.0, 0.0)


def frame_camera(
    spec: RenderSpec,
    asset_cards: list[AssetCard],
    *,
    margin_fraction: float = 0.1,
    view_axis: ViewAxis = "preserve",
) -> CameraFramingResult:
    """Frame visible bounded objects from a preserved or explicit Unreal-axis view."""

    if not 0 <= margin_fraction < 0.45:
        raise CameraFramingError("margin_fraction must be at least 0 and below 0.45")
    if view_axis not in VIEW_AXES:
        raise CameraFramingError(f"unsupported framing view_axis: {view_axis!r}")
    catalog = {card.asset_id: card for card in asset_cards}
    if len(catalog) != len(asset_cards):
        raise CameraFramingError("asset catalog contains duplicate asset IDs")
    visible = [item for item in spec.objects if item.visible]
    if not visible:
        raise CameraFramingError("camera framing requires at least one visible object")

    points: list[Point3] = []
    for item in visible:
        card = catalog.get(item.asset.asset_id)
        if card is None:
            raise CameraFramingError(
                f"object {item.object_id!r} references missing asset {item.asset.asset_id!r}"
            )
        points.extend(_world_corners(item, card))

    minimum = tuple(min(point[axis] for point in points) for axis in range(3))
    maximum = tuple(max(point[axis] for point in points) for axis in range(3))
    target = _multiply(_add(minimum, maximum), 0.5)
    if view_axis == "preserve":
        camera_location = spec.camera.transform.location_cm
        current = (camera_location.x, camera_location.y, camera_location.z)
        toward_target = _subtract(target, current)
        if _length(toward_target) <= 1e-9:
            forward = _rotation_basis(spec.camera.transform.rotation_deg)[0]
        else:
            forward = _normalize(toward_target)
    elif view_axis == "auto-product":
        forward = _automatic_product_forward(spec, target)
    else:
        forward = _VIEW_FORWARDS[view_axis]

    reference_up: Point3 = (0.0, 0.0, 1.0)
    if abs(_dot(reference_up, forward)) > 0.999:
        reference_up = (0.0, 1.0, 0.0)
    right = _normalize(_cross(reference_up, forward))
    up = _normalize(_cross(forward, right))

    aspect_ratio = spec.render.width_px / spec.render.height_px
    sensor_height_mm = DEFAULT_SENSOR_WIDTH_MM / aspect_ratio
    tan_half_horizontal = DEFAULT_SENSOR_WIDTH_MM / (2.0 * spec.camera.focal_length_mm)
    tan_half_vertical = sensor_height_mm / (2.0 * spec.camera.focal_length_mm)
    usable_fraction = 1.0 - 2.0 * margin_fraction
    required_distances = [1.0]
    for point in points:
        relative = _subtract(point, target)
        longitudinal = _dot(relative, forward)
        required_distances.extend(
            (
                abs(_dot(relative, right))
                / (tan_half_horizontal * usable_fraction)
                - longitudinal,
                abs(_dot(relative, up)) / (tan_half_vertical * usable_fraction)
                - longitudinal,
                1.0 - longitudinal,
            )
        )
    distance = round(max(required_distances) + 1.0, 6)
    framed_location = _subtract(target, _multiply(forward, distance))
    pitch = degrees(atan2(forward[2], sqrt(forward[0] ** 2 + forward[1] ** 2)))
    yaw = degrees(atan2(forward[1], forward[0]))
    framed_rotation = (0.0, pitch, yaw)

    framed_location_value = _rounded_vector(framed_location)
    framed_rotation_value = _rounded_vector(framed_rotation)
    operations = []
    if spec.camera.transform.location_cm.model_dump(mode="json") != framed_location_value:
        operations.append(PatchOperation(
            op="replace",
            path="/camera/transform/location_cm",
            value=framed_location_value,
        ))
    if spec.camera.transform.rotation_deg.model_dump(mode="json") != framed_rotation_value:
        operations.append(PatchOperation(
            op="replace",
            path="/camera/transform/rotation_deg",
            value=framed_rotation_value,
        ))
    if spec.camera.focus_distance_cm != distance:
        operations.append(PatchOperation(
            op="replace",
            path="/camera/focus_distance_cm",
            value=distance,
        ))
    if not operations:
        raise CameraFramingError("camera already matches the requested deterministic framing")
    object_ids = tuple(item.object_id for item in visible)
    patch = RenderSpecPatch(
        base_spec_sha256=canonical_sha256(spec),
        rationale=(
            f"Frame {len(object_ids)} visible bounded object(s) with "
            f"{margin_fraction:.1%} margin per edge using the adapter's "
            f"{DEFAULT_SENSOR_WIDTH_MM:g} mm sensor from view {view_axis!r}."
        ),
        proposed_by={"provider": "local", "model": "deterministic_autoframe_v1"},
        operations=operations,
    )
    framed_spec = apply_render_spec_patch(spec, patch)
    return CameraFramingResult(
        spec=framed_spec,
        patch=patch,
        target_cm=Vector3.model_validate(_rounded_vector(target)),
        distance_cm=distance,
        object_ids=object_ids,
        view_axis=view_axis,
    )
