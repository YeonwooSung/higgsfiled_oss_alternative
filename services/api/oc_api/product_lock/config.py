"""Product Lock project config (spec §8) plus the editor fields the inspector needs."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from open_creative.product_lock.prompts import EXTRA_PROMPT_MAX, load_presets

PRODUCT_CATEGORIES = ("beverage", "cosmetics", "food", "household", "electronics", "other")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Transform(_Strict):
    center_x: float = Field(0.5, ge=0.0, le=1.0)
    center_y: float = Field(0.55, ge=0.0, le=1.0)
    width_ratio: float = Field(0.36, ge=0.15, le=0.70)
    rotation_deg: Literal[0] = 0
    feather_px: float = Field(1.0, ge=0.0, le=4.0)


class Scene(_Strict):
    preset_id: str = "soft_studio_v1"
    accent_hex: str = Field("#D9D3C7", pattern=r"^#[0-9A-Fa-f]{6}$")
    extra_prompt: str = Field("", max_length=EXTRA_PROMPT_MAX)
    category: Literal[PRODUCT_CATEGORIES] = "other"  # type: ignore[valid-type]
    background_asset_id: UUID | None = None
    background_video_asset_id: UUID | None = None

    @field_validator("preset_id")
    @classmethod
    def _known_preset(cls, v: str) -> str:
        if v not in load_presets():
            raise ValueError(f"unknown preset {v!r}")
        return v


class Shadow(_Strict):
    enabled: bool = True
    opacity: float = Field(0.28, ge=0.0, le=0.6)
    blur_px: float = Field(18, ge=0, le=40)


class Lighting(_Strict):
    background_brightness: float = Field(0.0, ge=-0.2, le=0.2)


class Output(_Strict):
    aspect: Literal["9:16", "16:9"] = "9:16"
    duration_ms: Literal[5000] = 5000
    fps: Literal[24] = 24


class Box(_Strict):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)


class ProductLockConfig(_Strict):
    schema_version: Literal[1] = 1
    product_asset_id: UUID | None = None
    mask_asset_id: UUID | None = None
    core_mask_asset_id: UUID | None = None
    transform: Transform = Transform()
    scene: Scene = Scene()
    shadow: Shadow = Shadow()
    lighting: Lighting = Lighting()
    motion: Literal["subtle", "balanced"] = "subtle"
    output: Output = Output()
    protect_boxes: list[Box] = Field(default_factory=list, max_length=8)
    seed: int = Field(12345, ge=0, le=2**31 - 1)
    model_profile: str = "open_weights_v1"


def default_config(model_profile: str = "open_weights_v1") -> ProductLockConfig:
    return ProductLockConfig(model_profile=model_profile)
