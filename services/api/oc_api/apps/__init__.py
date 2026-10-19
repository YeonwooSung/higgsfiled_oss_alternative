"""App-module registry: everything an app contributes to the shared API (kinds, quotas, assets, exports)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..errors import ApiError
from ..models import Project


@dataclass
class JobPlan:
    kind: str
    options: dict
    stages: list[str]
    uses_gpu: bool
    reserved_units: float
    unit: str = "gpu_seconds"
    notes: list[str] = field(default_factory=list)


class AppModule(Protocol):
    slug: str
    config_model: type[BaseModel]
    kinds: tuple[str, ...]
    asset_ref_fields: tuple[str, ...]  # dotted paths into the config
    upload_roles: dict[str, tuple[str, ...]]  # role -> allowed mime types
    render_kinds: frozenset[str]  # at most max_active_renders_per_project active per project
    export_formats: dict[str, tuple[str, ...]]  # source job kind -> export formats
    export_kind: str  # internal job kind created by POST /projects/{id}/exports
    export_source_key: str  # options key holding the source job id
    workflows: tuple[str, ...]  # bindings GPU profiles need for this app

    def default_config(self, profile: str) -> BaseModel: ...

    def plan_job(self, db: DbSession, storage, project: Project, cfg: BaseModel, kind: str, options: dict,
                 *, validate_assets: bool = True) -> JobPlan: ...

    def default_estimate_options(self, kind: str, cfg: BaseModel) -> dict: ...

    def capabilities_extra(self) -> dict: ...


APPS: dict[str, AppModule] = {}


def get_app(slug: str) -> AppModule:
    try:
        return APPS[slug]
    except KeyError:
        raise ApiError(422, "unknown_app", f"unknown app {slug!r}", field="app_slug") from None


def _register() -> None:
    from .product_lock import MODULE as product_lock

    from .camera_sketch import MODULE as camera_sketch

    APPS[product_lock.slug] = product_lock
    APPS[camera_sketch.slug] = camera_sketch


_register()
