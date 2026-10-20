"""Pocket VFX project config (spec §10) plus the editor fields the render and track stages need."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from open_creative.pocket_vfx.presets import load_presets

DEFAULT_PRESET = "impact_wave_v1"
SEED_MAX = 2**31 - 1  # numpy default_rng rejects negatives; 31 bits keep the seed portable
OUTPUT_SIZES = {"16:9": (1280, 720), "9:16": (720, 1280)}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Trim(_Strict):
    source_start_ms: int = Field(0, ge=0)
    duration_ms: int = Field(5000, ge=1000, le=5000)


class Effect(_Strict):
    """Timing is relative to the trimmed clip. Its order/range are checked by ``validate`` (not structurally) so the
    editor can save an intermediate state and show the error."""

    preset_id: str = DEFAULT_PRESET
    start_ms: int = 1400
    peak_ms: int = 1800
    end_ms: int = 3400
    scale: float = Field(1.0, ge=0.25, le=2.0)
    intensity: float = Field(0.8, ge=0.0, le=1.0)
    palette: Literal["violet", "amber", "cyan"] = "amber"
    rotation_deg: float = Field(0.0, ge=-180.0, le=180.0)
    squash: float = Field(0.5, ge=0.2, le=1.0)  # Impact Wave ground ellipse
    layer_order: Literal["behind_subject", "in_front", "split_glow"] = "behind_subject"

    @field_validator("preset_id")
    @classmethod
    def _known_preset(cls, v: str) -> str:
        if v not in load_presets():
            raise ValueError(f"unknown preset {v!r}")
        return v


class Anchor(_Strict):
    frame: int = Field(43, ge=0)
    x: float = Field(0.5, ge=0.0, le=1.0)
    y: float = Field(0.7, ge=0.0, le=1.0)
    follow: bool = True
    track_window_frames: int | None = Field(None, ge=1, le=120)


class Correction(_Strict):
    frame: int = Field(ge=0)
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class PromptPoint(_Strict):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    label: Literal[0, 1] = 1


class SubjectPrompt(_Strict):
    frame: int = Field(ge=0)
    points: list[PromptPoint] = Field(default_factory=list, max_length=16)


class MaskEdit(_Strict):
    frame: int = Field(ge=0)
    asset_id: UUID


class Matte(_Strict):
    threshold: float = Field(0.06, ge=0.01, le=0.3)
    knee: float = Field(0.04, ge=0.0, le=0.2)


class Plate(_Strict):
    mode: Literal["bundled", "generated"] = "bundled"
    bundled_id: str | None = None  # None = the preset's default bundled plate
    asset_id: UUID | None = None  # raw_plate from vfx.generate_plate
    seed: int = Field(12345, ge=0, le=SEED_MAX)
    matte: Matte = Matte()


class Audio(_Strict):
    keep_original: bool = True
    sfx_enabled: bool = False
    sfx_gain_db: float = Field(-9.0, ge=-30.0, le=0.0)


class Output(_Strict):
    aspect: Literal["16:9", "9:16"] = "16:9"
    fps: Literal[24] = 24


class PocketVfxConfig(_Strict):
    schema_version: Literal[1] = 1
    video_asset_id: UUID | None = None
    work_asset_id: UUID | None = None  # work_clip from vfx.analyze; set by the client from the analyze result
    trim: Trim = Trim()
    effect: Effect = Effect()
    anchor: Anchor = Anchor()
    corrections: list[Correction] = Field(default_factory=list, max_length=32)
    subject_prompts: list[SubjectPrompt] = Field(default_factory=list, max_length=8)
    mask_edits: list[MaskEdit] = Field(default_factory=list, max_length=16)
    plate: Plate = Plate()
    clean_plate_asset_id: UUID | None = None
    audio: Audio = Audio()
    output: Output = Output()
    render_profile: Literal["quick", "generated"] = "quick"
    model_profile: str = "open_weights_v1"


def preset_effect(preset_id: str, start_ms: int, peak_ms: int, end_ms: int) -> Effect:
    """An effect with the preset's default controls."""
    d = load_presets()[preset_id]["defaults"]
    controls = {k: d[k] for k in ("scale", "intensity", "palette", "rotation_deg", "squash", "layer_order") if k in d}
    return Effect(preset_id=preset_id, start_ms=start_ms, peak_ms=peak_ms, end_ms=end_ms, **controls)


def default_config(model_profile: str = "open_weights_v1") -> PocketVfxConfig:
    return PocketVfxConfig(effect=preset_effect(DEFAULT_PRESET, 1400, 1800, 3400), model_profile=model_profile)
