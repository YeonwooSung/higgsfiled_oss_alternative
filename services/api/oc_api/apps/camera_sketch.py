from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from open_creative.camera_sketch.path import load_presets

from ..camera_sketch.config import CameraSketchConfig, default_config
from ..camera_sketch.scene_load import check_depth_lineage
from ..errors import ApiError
from ..models import Asset, Project
from . import JobPlan


class CameraSketchApp:
    slug = "camera-sketch"
    config_model = CameraSketchConfig
    kinds = ("camera.analyze", "camera.guide", "camera.cinematic", "camera.export")
    export_only_kinds = frozenset({"camera.export"})  # created via POST /projects/{id}/exports only
    asset_ref_fields = ("scene_asset_id", "depth_asset_id")
    upload_roles = {"scene": ("image/jpeg", "image/png", "image/webp")}
    render_kinds = frozenset({"camera.guide", "camera.cinematic"})
    export_formats = {"camera.guide": ("mp4", "path_json"), "camera.cinematic": ("mp4", "path_json")}
    workflows: tuple[str, ...] = ()  # camera workflows/bindings are registered in Task 9

    def default_config(self, profile: str) -> BaseModel:
        return default_config(profile)

    def check_config_assets(self, db: DbSession, project: Project, config: dict) -> None:
        """PATCH lineage rule: the depth asset must derive from the config's scene asset."""
        scene_id = config.get("scene_asset_id")
        if scene_id:
            scene = db.get(Asset, uuid.UUID(str(scene_id)))
            if scene is None or scene.role != "scene":
                raise ApiError(422, "invalid_asset_role", "scene_asset_id must be a scene image.",
                               field="config.scene_asset_id")
        depth_id = config.get("depth_asset_id")
        if not depth_id:
            return
        depth = db.get(Asset, uuid.UUID(str(depth_id)))
        check_depth_lineage(depth, scene_id)

    def plan_job(self, db: DbSession, storage, project: Project, cfg: BaseModel, kind: str, options: dict,
                 *, validate_assets: bool = True) -> JobPlan:
        # Camera job planning arrives with Task 8.
        raise ApiError(422, "invalid_kind", f"kind {kind!r} is not available", field="kind")

    def default_estimate_options(self, kind: str, cfg: BaseModel) -> dict:
        return {}

    def capabilities_extra(self) -> dict:
        return {
            "presets": list(load_presets().values()),
            "limits": {"duration_ms": 5000, "fps": 24, "aspects": ["9:16", "16:9"],
                       "output_sizes": {"9:16": [720, 1280], "16:9": [1280, 720]},
                       "image_max_bytes": 20 * 1024 * 1024, "image_max_pixels": 24_000_000,
                       "focal_length_mm_equiv": [35, 70], "lateral_ratio": [-0.05, 0.05],
                       "push_ratio": [0, 0.10], "arc_deg": [-6, 6], "sketch_raw_points_max": 128,
                       "sketch_control_points_max": 5, "extra_prompt_max": 300},
        }


MODULE = CameraSketchApp()
