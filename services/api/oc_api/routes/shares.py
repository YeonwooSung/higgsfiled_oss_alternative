from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from open_creative.comfy.profiles import PROFILES
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..db import utcnow
from ..deps import current_session, get_db, owned_project, parse_uuid
from ..errors import ApiError
from ..models import Asset, Job, Project, Share
from ..models import Session as SessionRow

router = APIRouter()
# Finished outputs only (Product Lock ad + cover, Camera Sketch Geometry / Cinematic videos, Pocket VFX render video and
# cover); originals never.
SHAREABLE_ROLES = ("render", "cover", "geometry_video", "cinematic_video", "render_video")


class ShareCreate(BaseModel):
    output_asset_id: uuid.UUID
    ttl_days: int = Field(7, ge=1, le=30)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("/projects/{project_id}/shares", status_code=201)
def create_share(project_id: str, body: ShareCreate, db: DbSession = Depends(get_db),
                 session: SessionRow = Depends(current_session)):
    project = owned_project(project_id, db, session)
    asset = db.get(Asset, body.output_asset_id)
    if asset is None or asset.project_id != project.id or asset.role not in SHAREABLE_ROLES or asset.state != "ready":
        raise ApiError(422, "not_shareable", "Only finished results can be shared; originals never are.",
                       field="output_asset_id")
    token = secrets.token_urlsafe(32)
    share = Share(project_id=project.id, output_asset_id=asset.id, token_hash=_hash(token),
                  expires_at=utcnow() + timedelta(days=body.ttl_days))
    db.add(share)
    db.commit()
    return {"id": str(share.id), "url": f"/s/{token}", "expires_at": share.expires_at.isoformat(),
            "output_asset_id": str(asset.id)}


@router.get("/projects/{project_id}/shares")
def list_shares(project_id: str, db: DbSession = Depends(get_db), session: SessionRow = Depends(current_session)):
    """The project's active links (not revoked, not expired), newest first. Only the token hash is stored, so the
    public URL is shown once at creation and never again; the id is enough to revoke."""
    project = owned_project(project_id, db, session)
    rows = db.scalars(select(Share).where(Share.project_id == project.id, Share.revoked_at.is_(None),
                                          Share.expires_at > utcnow()).order_by(Share.created_at.desc()))
    return {"shares": [{"id": str(s.id), "output_asset_id": str(s.output_asset_id),
                        "created_at": s.created_at.isoformat(), "expires_at": s.expires_at.isoformat()}
                       for s in rows]}


@router.delete("/shares/{share_id}", status_code=204)
def revoke_share(share_id: str, db: DbSession = Depends(get_db), session: SessionRow = Depends(current_session)):
    share = db.scalar(select(Share).join(Project, Project.id == Share.project_id).where(
        Share.id == parse_uuid(share_id, "share"), Project.session_id == session.id))
    if share is None:
        raise ApiError(404, "not_found", "share not found")
    share.revoked_at = share.revoked_at or utcnow()
    db.commit()
    return Response(status_code=204)


def resolve_share(db: DbSession, token: str) -> tuple[Share, Asset]:
    share = db.scalar(select(Share).where(Share.token_hash == _hash(token)))
    if share is None or share.revoked_at is not None or share.expires_at < utcnow():
        raise ApiError(404, "not_found", "This link is no longer available.")
    project = db.get(Project, share.project_id)
    asset = db.get(Asset, share.output_asset_id)
    if project is None or project.deleted_at is not None or asset is None:
        raise ApiError(404, "not_found", "This link is no longer available.")
    return share, asset


@router.get("/public/shares/{token}")
def open_share(token: str, request: Request, db: DbSession = Depends(get_db)):
    _, asset = resolve_share(db, token)
    ttl = request.app.state.settings.read_url_ttl_s
    return RedirectResponse(request.app.state.storage.read_url(asset.object_key, ttl, mime=asset.mime),
                            status_code=302, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/public/shares/{token}/meta")
def share_meta(token: str, db: DbSession = Depends(get_db)):
    share, asset = resolve_share(db, token)
    meta = asset.meta_json or {}
    job = db.get(Job, asset.job_id) if asset.job_id else None
    profile = PROFILES.get(job.model_profile) if job is not None else None
    # Mock = stand-in output in the asset itself, or made by a mock-profile job (e.g. a Quick render on mock masks)
    mock = bool(meta.get("mock")) or bool(profile is not None and profile.is_mock)
    return {"mime": asset.mime, "width": asset.width, "height": asset.height, "role": asset.role,
            "mode": meta.get("mode"), "mock": mock,
            "render_profile": meta.get("render_profile"), "plate_source": meta.get("plate_source"),
            "exact_camera_control": False if meta.get("mode") == "cinematic" else None,
            "expires_at": share.expires_at.isoformat()}
