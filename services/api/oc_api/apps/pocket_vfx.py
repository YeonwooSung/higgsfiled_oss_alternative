from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..errors import ApiError
from ..models import Project
from ..pocket_vfx.config import PocketVfxConfig, default_config
from . import JobPlan


class PocketVfxApp:
    slug = "pocket-vfx"
    config_model = PocketVfxConfig
    kinds: tuple[str, ...] = ()
    asset_ref_fields: tuple[str, ...] = ("video_asset_id",)
    upload_roles = {"clip": ("video/mp4", "video/quicktime", "video/webm"),
                    "clean_plate": ("image/jpeg", "image/png", "image/webp"),
                    "mask_edit": ("image/png",)}
    upload_limits = {"clip": 100 * 1024 * 1024}
    render_kinds: frozenset[str] = frozenset()
    export_formats: dict[str, tuple[str, ...]] = {}
    export_kind = ""
    export_source_key = ""
    workflows: tuple[str, ...] = ()
    kind_workflows: dict[str, tuple[str, ...]] = {}

    def default_config(self, profile: str) -> BaseModel:
        return default_config(profile)

    def plan_job(self, db: DbSession, storage, project: Project, cfg: BaseModel, kind: str, options: dict,
                 *, validate_assets: bool = True) -> JobPlan:
        raise ApiError(422, "unknown_kind", f"unknown job kind {kind!r}", field="kind")

    def default_estimate_options(self, kind: str, cfg: BaseModel) -> dict:
        return {}

    def capabilities_extra(self) -> dict:
        return {}


MODULE = PocketVfxApp()
