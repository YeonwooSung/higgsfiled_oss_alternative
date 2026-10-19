"""Camera Sketch project config (spec §10) plus the editor fields the inspector needs."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

EXTRA_PROMPT_MAX = 300


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Target(_Strict):
    u: float = Field(0.5, ge=0.0, le=1.0)
    v: float = Field(0.5, ge=0.0, le=1.0)
    depth_scale: float = Field(1.0, ge=0.5, le=2.0)


class Camera(_Strict):
    focal_length_mm_equiv: float = Field(50.0, ge=35.0, le=70.0)
    sensor_width_mm_assumed: Literal[36] = 36
    roll_deg: Literal[0] = 0


class SketchPoint(_Strict):
    t: float = Field(ge=0.0, le=1.0)
    u: float = Field(ge=0.0, le=1.0)
    v: float = Field(ge=0.0, le=1.0)


class Path(_Strict):
    preset: Literal["push_in", "slide", "small_arc"] = "slide"
    sketch_space: Literal["target_screen_position"] = "target_screen_position"
    sketch_points: list[SketchPoint] = Field(default_factory=list, max_length=5)
    lateral_ratio: float = Field(0.0, ge=-0.05, le=0.05)
    push_ratio: float = Field(0.0, ge=0.0, le=0.10)
    arc_deg: float = Field(0.0, ge=-6.0, le=6.0)
    easing: Literal["linear", "ease_in_out"] = "ease_in_out"


class Generation(_Strict):
    seed: int = Field(12345, ge=0, le=2**31 - 1)
    control_preset: Literal["low", "medium", "high"] = "medium"
    extra_prompt: str = Field("", max_length=EXTRA_PROMPT_MAX)


class Output(_Strict):
    aspect: Literal["9:16", "16:9"] = "16:9"
    duration_ms: Literal[5000] = 5000
    fps: Literal[24] = 24


class SceneEditor(_Strict):
    depth_contrast: float = Field(1.0, ge=0.5, le=1.5)
    edge_protection: float = Field(0.5, ge=0.0, le=1.0)
    show_holes: bool = False


class CameraSketchConfig(_Strict):
    schema_version: Literal[1] = 1
    scene_asset_id: UUID | None = None
    depth_asset_id: UUID | None = None
    target: Target = Target()
    camera: Camera = Camera()
    path: Path = Path()
    mode: Literal["geometry", "cinematic"] = "geometry"
    generation: Generation = Generation()
    output: Output = Output()
    scene: SceneEditor = SceneEditor()
    model_profile: str = "open_weights_v1"


OUTPUT_SIZES = {"16:9": (1280, 720), "9:16": (720, 1280)}


def default_config(model_profile: str = "open_weights_v1") -> CameraSketchConfig:
    return CameraSketchConfig(model_profile=model_profile)
