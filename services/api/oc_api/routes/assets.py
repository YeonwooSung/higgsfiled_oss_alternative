from __future__ import annotations

import hashlib
import uuid
from pathlib import PurePath
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..apps import get_app, upload_limit
from ..deps import current_session, get_db, owned_project, parse_uuid
from ..errors import ApiError
from ..jobs_service import enqueue_internal_job
from ..models import Asset, Project
from ..models import Session as SessionRow
from ..serializers import asset_out
from ..storage import object_key

router = APIRouter()

def _is_video(mimes: tuple[str, ...]) -> bool:
    return all(m.startswith("video/") for m in mimes)


def _format_message(mimes: tuple[str, ...]) -> str:
    if _is_video(mimes):
        return "Use an MP4, MOV or WebM video."
    return "Use JPG, PNG or WebP (masks: PNG)."


def _limit_label(limit: int) -> str:
    return f"{limit // (1024 * 1024)} MB"


def _too_large_message(limit: int, mimes: tuple[str, ...]) -> str:
    return f"{'Videos' if _is_video(mimes) else 'Images'} must be {_limit_label(limit)} or smaller."


class AssetInit(BaseModel):
    role: str
    filename: str = Field(max_length=255)
    mime: str
    bytes: int = Field(gt=0)


class AssetComplete(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


def owned_asset(asset_id: str, db: DbSession, session: SessionRow) -> Asset:
    aid = parse_uuid(asset_id, "asset")
    asset = db.scalar(select(Asset).join(Project, Project.id == Asset.project_id).where(
        Asset.id == aid, Project.session_id == session.id, Project.deleted_at.is_(None)))
    if asset is None:
        raise ApiError(404, "not_found", "asset not found")
    return asset


@router.post("/projects/{project_id}/assets/init", status_code=201)
def init_asset(project_id: str, body: AssetInit, request: Request, db: DbSession = Depends(get_db),
               session: SessionRow = Depends(current_session)):
    project = owned_project(project_id, db, session)
    app = get_app(project.app_slug)
    roles = app.upload_roles
    if body.role not in roles:
        raise ApiError(422, "invalid_role", f"role {body.role!r} cannot be uploaded", field="role")
    if body.mime not in roles[body.role]:
        raise ApiError(415, "unsupported_media_type", _format_message(roles[body.role]), field="mime")
    limit = upload_limit(app, body.role)
    if body.bytes > limit:
        raise ApiError(413, "too_large", _too_large_message(limit, roles[body.role]), field="bytes")
    asset_id = uuid.uuid4()
    key = object_key(session.id, project.id, asset_id, f"{body.role}-original", body.mime)
    asset = Asset(id=asset_id, project_id=project.id, role=body.role, state="pending_upload", object_key=key,
                  mime=body.mime, bytes=body.bytes, filename=PurePath(body.filename).name[:200] or None)
    db.add(asset)
    db.commit()
    settings = request.app.state.settings
    url, headers, expires = request.app.state.storage.upload_url(key, settings.upload_url_ttl_s, body.mime,
                                                                 body.bytes)
    return {"asset_id": str(asset_id), "upload_url": url, "method": "PUT", "headers": headers,
            "expires_at": expires.isoformat()}


@router.post("/assets/{asset_id}/complete", status_code=202)
def complete_asset(asset_id: str, body: AssetComplete, request: Request, db: DbSession = Depends(get_db),
                   session: SessionRow = Depends(current_session)):
    asset = owned_asset(asset_id, db, session)
    if asset.state != "pending_upload":
        raise ApiError(409, "already_completed", "Upload was already completed.")
    storage = request.app.state.storage
    if not storage.exists(asset.object_key):
        raise ApiError(409, "upload_missing", "No uploaded bytes found for this asset.", retryable=True)
    size = storage.size(asset.object_key)
    limit = upload_limit(get_app(db.get(Project, asset.project_id).app_slug), asset.role)
    if size > limit or size > (asset.bytes or limit):
        # Presigned S3 PUTs cannot enforce the declared size; check before reading anything.
        storage.delete_prefix(asset.object_key)
        raise ApiError(413, "too_large", f"Upload is larger than declared (maximum {_limit_label(limit)}).")
    hasher = hashlib.sha256()
    for chunk in storage.iter_chunks(asset.object_key):
        hasher.update(chunk)
    digest = hasher.hexdigest()
    if digest != body.sha256.lower():
        raise ApiError(422, "hash_mismatch", "Uploaded bytes do not match the provided sha256.", field="sha256")
    asset.sha256, asset.bytes, asset.state = digest, size, "uploaded"
    project = db.get(Project, asset.project_id)
    job = enqueue_internal_job(db, project, "asset.inspect", {"asset_id": str(asset.id)})
    db.commit()
    return {"asset": asset_out(request, asset), "inspect_job_id": str(job.id)}


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, request: Request, download: bool = False, db: DbSession = Depends(get_db),
              session: SessionRow = Depends(current_session)):
    asset = owned_asset(asset_id, db, session)
    project = db.get(Project, asset.project_id)
    return asset_out(request, asset, with_url=True, download=download, app_slug=project.app_slug)
