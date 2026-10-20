from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from open_creative.comfy.profiles import VFX_WORKFLOWS

from ..errors import ApiError
from ..models import Asset, Project
from ..pocket_vfx.config import OUTPUT_SIZES, SEED_MAX, PocketVfxConfig, default_config
from . import JobPlan

CLIP_MAX_BYTES = 100 * 1024 * 1024


def _role_asset(db: DbSession, project: Project, value, role: str, field: str) -> Asset | None:
    if not value:
        return None
    asset = db.get(Asset, uuid.UUID(str(value)))
    if asset is None or asset.project_id != project.id:
        raise ApiError(422, "asset_not_found", "Referenced asset does not belong to this project.", field=field)
    if asset.role != role:
        raise ApiError(422, "invalid_asset_role", f"{field.split('.', 1)[1]} must be a {role.replace('_', ' ')}.",
                       field=field)
    return asset


class PocketVfxApp:
    slug = "pocket-vfx"
    config_model = PocketVfxConfig
    kinds = ("vfx.analyze", "vfx.track", "vfx.generate_plate", "vfx.render", "vfx.export")
    export_only_kinds = frozenset({"vfx.export"})  # created via POST /projects/{id}/exports only
    asset_ref_fields = ("video_asset_id", "work_asset_id", "clean_plate_asset_id", "plate.asset_id")
    upload_roles = {"clip": ("video/mp4", "video/quicktime", "video/webm"),
                    "clean_plate": ("image/jpeg", "image/png", "image/webp"),
                    "mask_edit": ("image/png",)}
    upload_limits = {"clip": CLIP_MAX_BYTES}
    render_kinds = frozenset({"vfx.render"})
    export_formats = {"vfx.render": ("mp4", "cover_png", "recipe_zip", "layers_zip")}
    export_kind = "vfx.export"
    export_source_key = "render_job_id"
    workflows: tuple[str, ...] = VFX_WORKFLOWS  # Comfy-side; analyze, LK track, render and export run on the worker
    kind_workflows: dict[str, tuple[str, ...]] = {"vfx.track": ("vfx_masks_v1",),
                                                  "vfx.generate_plate": ("vfx_plate_v1",)}

    def default_config(self, profile: str) -> BaseModel:
        return default_config(profile)

    def check_config_assets(self, db: DbSession, project: Project, config: dict) -> None:
        """PATCH role + lineage rules: the work clip must be the analysis of this clip, trim and aspect."""
        _role_asset(db, project, config.get("video_asset_id"), "clip", "config.video_asset_id")
        work = _role_asset(db, project, config.get("work_asset_id"), "work_clip", "config.work_asset_id")
        if work is not None:
            from ..pocket_vfx.jobs import analysis_matches  # lazy: jobs imports JobPlan from this package

            if not analysis_matches(work, config):
                raise ApiError(422, "analysis_stale", "The clip, trim or aspect changed after the clip was prepared. "
                               "Prepare it again.", field="config.work_asset_id")
        for i, edit in enumerate(config.get("mask_edits") or []):
            _role_asset(db, project, edit["asset_id"], "mask_edit", f"config.mask_edits.{i}.asset_id")
        _role_asset(db, project, config.get("clean_plate_asset_id"), "clean_plate", "config.clean_plate_asset_id")
        _role_asset(db, project, (config.get("plate") or {}).get("asset_id"), "raw_plate", "config.plate.asset_id")

    def plan_job(self, db: DbSession, storage, project: Project, cfg: BaseModel, kind: str, options: dict,
                 *, validate_assets: bool = True) -> JobPlan:
        from ..pocket_vfx.jobs import plan_job  # lazy: jobs imports JobPlan from this package

        return plan_job(db, storage, project, cfg, kind, options, validate_assets=validate_assets)

    def default_estimate_options(self, kind: str, cfg: BaseModel) -> dict:
        if kind == "vfx.render":
            return {"track_job_id": None, "render_profile": cfg.render_profile}
        return {}

    def capabilities_extra(self) -> dict:
        from ..pocket_vfx.routes import public_presets

        return {
            "presets": public_presets(),
            "limits": {"trim_duration_ms": [1000, 5000], "source_duration_ms_max": 60_000, "fps": 24,
                       "aspects": ["16:9", "9:16"], "output_sizes": {k: list(v) for k, v in OUTPUT_SIZES.items()},
                       "clip_max_bytes": CLIP_MAX_BYTES, "corrections_max": 32, "subject_prompts_max": 8,
                       "prompt_points_max": 16, "mask_edits_max": 16, "sfx_gain_db": [-30, 0],
                       "seed_max": SEED_MAX},
        }


MODULE = PocketVfxApp()
