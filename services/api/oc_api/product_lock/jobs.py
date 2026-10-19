"""Product Lock job kinds: option schemas and pre-submit validation against the pinned revision."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.orm import Session as DbSession

from open_creative.comfy.profiles import GPU_SECONDS_CAP
from open_creative.media.image import load_mask_png
from open_creative.product_lock.masks import CORE_MIN_FRACTION, compute_core, core_fraction, rasterize_boxes
from open_creative.product_lock.prompts import load_presets

from ..errors import ApiError
from ..models import Asset, Job, Project
from .config import Box, ProductLockConfig


class _Opts(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Point(_Opts):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    label: Literal[0, 1] = 1


class SegmentOptions(_Opts):
    points: list[Point] = Field(default_factory=list, max_length=20)
    box: Box | None = None

    @model_validator(mode="after")
    def _needs_prompt(self):
        if not self.points and self.box is None:
            raise ValueError("add at least one point or a box")
        return self


class BackgroundOptions(_Opts):
    preset_id: str
    seed: int = Field(ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def _known(self):
        if self.preset_id not in load_presets():
            raise ValueError(f"unknown preset {self.preset_id!r}")
        return self


class BackgroundVideoOptions(_Opts):
    background_asset_id: uuid.UUID
    seed: int = Field(ge=0, le=2**31 - 1)


class RenderOptions(_Opts):
    reuse_background_video_asset_id: uuid.UUID | None = None


class InspectOptions(_Opts):
    render_job_id: uuid.UUID


OPTION_MODELS: dict[str, type[_Opts]] = {
    "product.segment": SegmentOptions,
    "product.background": BackgroundOptions,
    "product.background_video": BackgroundVideoOptions,
    "product.render": RenderOptions,
    "product.inspect": InspectOptions,
}


@dataclass
class JobPlan:
    kind: str
    options: dict
    stages: list[str]
    uses_gpu: bool
    reserved_units: float
    unit: str = "gpu_seconds"
    notes: list[str] = field(default_factory=list)


def parse_options(kind: str, options: dict) -> _Opts:
    try:
        return OPTION_MODELS[kind].model_validate(options)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise ApiError(422, "invalid_options", first["msg"], field=f"options.{loc}" if loc else "options") from None


def _ready_asset(db: DbSession, project: Project, asset_id, role: str | tuple[str, ...], missing_code: str,
                 field_name: str) -> Asset:
    roles = (role,) if isinstance(role, str) else role
    if asset_id is None:
        raise ApiError(422, missing_code, f"{roles[0].replace('_', ' ')} is required", field=field_name)
    asset = db.get(Asset, uuid.UUID(str(asset_id)))
    if asset is None or asset.project_id != project.id or asset.role not in roles:
        raise ApiError(422, "asset_not_found", "Asset does not belong to this project.", field=field_name)
    if asset.state != "ready":
        raise ApiError(422, "asset_not_ready", "Asset has not passed inspection yet.", field=field_name,
                       retryable=True)
    return asset


def check_core(storage, product: Asset, mask: Asset, core_mask: Asset | None, cfg: ProductLockConfig) -> float:
    if (mask.width, mask.height) != (product.width, product.height):
        raise ApiError(422, "mask_dimension_mismatch", "Mask size must match the normalized product image.",
                       field="config.mask_asset_id")
    m = load_mask_png(storage.get_bytes(mask.object_key))
    user = None
    if core_mask is not None:
        if (core_mask.width, core_mask.height) != (product.width, product.height):
            raise ApiError(422, "mask_dimension_mismatch", "Core mask size must match the product image.",
                           field="config.core_mask_asset_id")
        user = load_mask_png(storage.get_bytes(core_mask.object_key)) >= 0.5
    if cfg.protect_boxes:
        boxes, _ = rasterize_boxes([b.model_dump() for b in cfg.protect_boxes], m.shape[1], m.shape[0])
        user = boxes if user is None else (user | boxes)
    frac = core_fraction(compute_core(m, user), m)
    if frac < CORE_MIN_FRACTION:
        raise ApiError(422, "core_too_small", "The protected label area is too small. Set a label protection box.",
                       field="config.protect_boxes")
    return frac


def plan_job(db: DbSession, storage, project: Project, cfg: ProductLockConfig, kind: str, options: dict,
             *, validate_assets: bool = True) -> JobPlan:
    opts = parse_options(kind, options)
    data = opts.model_dump(mode="json")
    gpu_cap = GPU_SECONDS_CAP
    if kind == "product.segment":
        if validate_assets:
            _ready_asset(db, project, cfg.product_asset_id, "product", "product_required", "config.product_asset_id")
        return JobPlan(kind, data, ["segment"], True, gpu_cap[kind])
    if kind == "product.background":
        return JobPlan(kind, data, ["background_image"], True, gpu_cap[kind])
    if kind == "product.background_video":
        if validate_assets:
            _ready_asset(db, project, opts.background_asset_id, "background", "background_required",
                         "options.background_asset_id")
        return JobPlan(kind, data, ["background_video"], True, gpu_cap[kind])
    if kind == "product.render":
        reuse = opts.reuse_background_video_asset_id
        if validate_assets:
            product = _ready_asset(db, project, cfg.product_asset_id, "product", "product_required",
                                   "config.product_asset_id")
            mask = _ready_asset(db, project, cfg.mask_asset_id, "mask", "mask_required", "config.mask_asset_id")
            core = (_ready_asset(db, project, cfg.core_mask_asset_id, "core_mask", "core_mask_required",
                                 "config.core_mask_asset_id") if cfg.core_mask_asset_id else None)
            if reuse:
                _ready_asset(db, project, reuse, "background_video", "background_required",
                             "options.reuse_background_video_asset_id")
            else:
                _ready_asset(db, project, cfg.scene.background_asset_id, "background", "background_required",
                             "config.scene.background_asset_id")
            check_core(storage, product, mask, core, cfg)
        if reuse:
            return JobPlan(kind, data, ["composite", "encode"], False, 0, notes=["reuses background video"])
        return JobPlan(kind, data, ["background_video", "composite", "encode"], True,
                       gpu_cap["product.background_video"])
    if kind == "product.inspect":
        if validate_assets:
            render = db.get(Job, opts.render_job_id)
            if render is None or render.project_id != project.id or render.kind != "product.render":
                raise ApiError(422, "render_not_found", "Render job not found.", field="options.render_job_id")
            if render.state != "succeeded":
                raise ApiError(422, "render_not_finished", "Render has not finished.",
                               field="options.render_job_id")
        return JobPlan(kind, data, ["inspect"], False, 0)
    raise ApiError(422, "invalid_kind", f"unknown kind {kind!r}", field="kind")
