"""Hand-offs: a finished result of one tool becomes the input of a new project in another tool.

The allowlist is keyed by the source project's app and the asset role (Product Lock and Pocket VFX both have a
``cover``; only Product Lock's may be handed off). Provenance and the upstream-mock flag are stored on the copied
asset at hand-off time, so they survive the source project being deleted.
"""

from __future__ import annotations

import uuid

from open_creative.comfy.profiles import PROFILES
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from .models import Asset, Job, Project

# (source app_slug, source asset role) -> (target app_slug, target upload role)
HANDOFF_TARGETS: dict[tuple[str, str], tuple[str, str]] = {
    ("product-lock", "render"): ("pocket-vfx", "clip"),
    ("camera-sketch", "geometry_video"): ("pocket-vfx", "clip"),
    ("camera-sketch", "cinematic_video"): ("pocket-vfx", "clip"),
    ("product-lock", "cover"): ("camera-sketch", "scene"),
}

# Display names used in the new project's title ("<source title> → <label>").
TARGET_LABELS = {"pocket-vfx": "Pocket VFX", "camera-sketch": "Camera Sketch"}


def _handoff_assets(db: DbSession, project: Project) -> list[Asset]:
    """The project's hand-off inputs, newest first (every hand-off asset has a ``source_asset_id``)."""
    rows = db.scalars(select(Asset).where(Asset.project_id == project.id, Asset.source_asset_id.is_not(None))
                      .order_by(Asset.created_at.desc())).all()
    return [a for a in rows if isinstance((a.meta_json or {}).get("handoff"), dict)]


def project_upstream_mock(db: DbSession, project: Project) -> bool:
    """True if any input of the project was handed off from a Mock result (the stored flag; never recomputed)."""
    return any(bool(a.meta_json["handoff"].get("mock")) for a in _handoff_assets(db, project))


def asset_is_mock(db: DbSession, asset: Asset) -> bool:
    """Mock = stand-in output in the asset itself, made by a mock-profile job, or anywhere upstream in the chain."""
    if (asset.meta_json or {}).get("mock"):
        return True
    job = db.get(Job, asset.job_id) if asset.job_id else None
    profile = PROFILES.get(job.model_profile) if job is not None else None
    if profile is not None and profile.is_mock:
        return True
    project = db.get(Project, asset.project_id)
    return project is not None and project_upstream_mock(db, project)


def project_handoff(db: DbSession, project: Project) -> dict | None:
    """Provenance of the project's newest hand-off input (None if the project was not started from a hand-off)."""
    assets = _handoff_assets(db, project)
    if not assets:
        return None
    h = assets[0].meta_json["handoff"]
    source = None
    if h.get("source_project_id"):
        try:
            source = db.get(Project, uuid.UUID(h["source_project_id"]))
        except ValueError:
            source = None
    return {"source_app": h.get("source_app"), "source_project_id": h.get("source_project_id"),
            "source_title": h.get("source_title"),
            "source_exists": source is not None and source.deleted_at is None, "mock": bool(h.get("mock"))}
