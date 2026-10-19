from __future__ import annotations

import hashlib
import uuid
from pathlib import PurePath
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..apps import get_app
from ..deps import current_session, get_db, owned_project, parse_uuid
from ..errors import ApiError
from ..jobs_service import enqueue_internal_job
from ..models import Asset, Project
from ..models import Session as SessionRow
from ..serializers import asset_out
from ..storage import object_key

router = APIRouter()

MAX_IMAGE_BYTES = 20 * 1024 * 1024


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
    roles = get_app(project.app_slug).upload_roles
    if body.role not in roles:
        raise ApiError(422, "invalid_role", f"role {body.role!r} cannot be uploaded", field="role")
    if body.mime not in roles[body.role]:
        raise ApiError(415, "unsupported_media_type", "Use JPG, PNG or WebP (masks: PNG).", field="mime")
    if body.bytes > MAX_IMAGE_BYTES:
        raise ApiError(413, "too_large", "Images must be 20 MB or smaller.", field="bytes")
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
    if size > MAX_IMAGE_BYTES or size > (asset.bytes or MAX_IMAGE_BYTES):
        # Presigned S3 PUTs cannot enforce the declared size; check before reading anything.
        storage.delete_prefix(asset.object_key)
        raise ApiError(413, "too_large", "Upload is larger than declared (images must be 20 MB or smaller).")
    data = storage.get_bytes(asset.object_key)
    digest = hashlib.sha256(data).hexdigest()
    if digest != body.sha256.lower():
        raise ApiError(422, "hash_mismatch", "Uploaded bytes do not match the provided sha256.", field="sha256")
    asset.sha256, asset.bytes, asset.state = digest, len(data), "uploaded"
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
