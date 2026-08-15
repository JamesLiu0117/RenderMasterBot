"""Versioned scene contract shared by planners and renderer adapters."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
PositiveFiniteFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]
NonNegativeFiniteFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]
UnitFloat = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_\-]{0,63}$")]
DEFAULT_SENSOR_WIDTH_MM = 36.0


class StrictModel(BaseModel):
    """Reject unknown fields so model mistakes fail loudly."""

    model_config = ConfigDict(extra="forbid")


class Vector3(StrictModel):
    x: FiniteFloat = 0.0
    y: FiniteFloat = 0.0
    z: FiniteFloat = 0.0


class Scale3(StrictModel):
    x: PositiveFiniteFloat = 1.0
    y: PositiveFiniteFloat = 1.0
    z: PositiveFiniteFloat = 1.0


class Transform(StrictModel):
    location_cm: Vector3 = Field(default_factory=Vector3)
    rotation_deg: Vector3 = Field(default_factory=Vector3)
    scale: Scale3 = Field(default_factory=Scale3)


class AssetReference(StrictModel):
    asset_id: Identifier
    display_name: str | None = Field(default=None, max_length=120)


class MaterialAssignment(StrictModel):
    """Assign one catalog material asset to a named mesh material slot."""

    slot_name: Annotated[str, Field(min_length=1, max_length=120)]
    material: AssetReference

    @model_validator(mode="after")
    def slot_name_is_trimmed(self) -> "MaterialAssignment":
        if self.slot_name != self.slot_name.strip():
            raise ValueError("material slot names cannot have surrounding whitespace")
        return self


class SceneObject(StrictModel):
    object_id: Identifier
    asset: AssetReference
    transform: Transform = Field(default_factory=Transform)
    visible: bool = True
    materials: list[MaterialAssignment] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def material_slots_are_unique(self) -> "SceneObject":
        slots = [assignment.slot_name.casefold() for assignment in self.materials]
        duplicates = sorted({slot for slot in slots if slots.count(slot) > 1})
        if duplicates:
            raise ValueError("material assignments must use unique slot names")
        return self


class Camera(StrictModel):
    camera_id: Identifier = "main_camera"
    transform: Transform
    focal_length_mm: PositiveFiniteFloat = 50.0
    focus_distance_cm: PositiveFiniteFloat | None = None
    aperture_f_stop: PositiveFiniteFloat = 5.6


class Light(StrictModel):
    light_id: Identifier
    kind: Literal["directional", "point", "spot", "rect"]
    transform: Transform = Field(default_factory=Transform)
    intensity: NonNegativeFiniteFloat
    intensity_unit: Literal["lux", "lumens", "candelas", "unitless"]
    color_rgb: tuple[UnitFloat, UnitFloat, UnitFloat] = (1.0, 1.0, 1.0)
    cast_shadows: bool = True

    @model_validator(mode="after")
    def intensity_unit_matches_light_type(self) -> "Light":
        if self.kind == "directional" and self.intensity_unit != "lux":
            raise ValueError("directional lights must use lux")
        if self.kind != "directional" and self.intensity_unit == "lux":
            raise ValueError("point, spot, and rect lights cannot use lux")
        return self


class RenderSettings(StrictModel):
    width_px: Annotated[int, Field(ge=64, le=16384)] = 1920
    height_px: Annotated[int, Field(ge=64, le=16384)] = 1080
    output_format: Literal["png", "exr"] = "png"
    quality: Literal["preview", "final"] = "preview"
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)] = 0


class RenderSpec(StrictModel):
    """RenderMasterBot's stable planner-to-renderer contract."""

    schema_version: Literal["0.1"] = "0.1"
    source_prompt: Annotated[str, Field(min_length=1, max_length=4000)]
    scene_name: Identifier
    objects: list[SceneObject] = Field(default_factory=list, max_length=256)
    camera: Camera
    lights: list[Light] = Field(default_factory=list, max_length=64)
    render: RenderSettings = Field(default_factory=RenderSettings)
    notes: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> "RenderSpec":
        ids = [item.object_id for item in self.objects]
        ids.extend(item.light_id for item in self.lights)
        ids.append(self.camera.camera_id)
        duplicates = sorted({value for value in ids if ids.count(value) > 1})
        if duplicates:
            raise ValueError(f"scene identifiers must be unique: {', '.join(duplicates)}")
        return self
