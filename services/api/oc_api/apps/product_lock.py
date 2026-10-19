from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from open_creative.product_lock.prompts import load_presets

from ..models import Project
from ..product_lock.config import ProductLockConfig, default_config
from . import JobPlan


class ProductLockApp:
    slug = "product-lock"
    config_model = ProductLockConfig
    kinds = ("product.segment", "product.background", "product.background_video", "product.render",
             "product.inspect")
    asset_ref_fields = ("product_asset_id", "mask_asset_id", "core_mask_asset_id",
                        "scene.background_asset_id", "scene.background_video_asset_id")
    upload_roles = {
        "product": ("image/jpeg", "image/png", "image/webp"),
        "mask": ("image/png",),
        "core_mask": ("image/png",),
    }
    render_kinds = frozenset({"product.render"})
    export_formats = {"product.render": ("mp4", "cover_png", "recipe_zip")}
    export_kind = "export"
    export_source_key = "render_job_id"
    workflows = ("product_segment_v1", "product_background_image_v1", "product_background_video_v1")

    def default_config(self, profile: str) -> BaseModel:
        return default_config(profile)

    def plan_job(self, db: DbSession, storage, project: Project, cfg: BaseModel, kind: str, options: dict,
                 *, validate_assets: bool = True) -> JobPlan:
        from ..product_lock.jobs import plan_job  # lazy: jobs imports JobPlan from this package

        return plan_job(db, storage, project, cfg, kind, options, validate_assets=validate_assets)

    def default_estimate_options(self, kind: str, cfg: BaseModel) -> dict:
        if kind == "product.background":
            return {"preset_id": cfg.scene.preset_id, "seed": cfg.seed}
        if kind == "product.background_video":
            return {"background_asset_id": str(cfg.scene.background_asset_id or uuid.UUID(int=0)),
                    "seed": cfg.seed}
        if kind == "product.segment":
            return {"points": [{"x": 0.5, "y": 0.5, "label": 1}]}
        if kind == "product.inspect":
            return {"render_job_id": str(uuid.UUID(int=0))}
        return {}

    def capabilities_extra(self) -> dict:
        return {
            "presets": [p.public() for p in load_presets().values()],
            "limits": {"duration_ms": 5000, "fps": 24, "aspects": ["9:16", "16:9"],
                       "output_sizes": {"9:16": [720, 1280], "16:9": [1280, 720]},
                       "image_max_bytes": 20 * 1024 * 1024, "image_max_pixels": 24_000_000,
                       "width_ratio": [0.15, 0.70], "feather_px": [0, 4], "shadow_opacity": [0, 0.6],
                       "shadow_blur_px": [0, 40], "background_brightness": [-0.2, 0.2], "brush_px": [2, 80],
                       "extra_prompt_max": 300},
        }


MODULE = ProductLockApp()
