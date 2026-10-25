from __future__ import annotations

import hashlib
import json
import logging
import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from ..apps import get_app, upload_limit
from ..apps_registry import default_app_config
from ..deps import current_session, get_db
from ..errors import ApiError
from ..handoffs import HANDOFF_TARGETS, TARGET_LABELS, asset_is_mock
from ..jobs_service import enqueue_internal_job
from ..models import Asset, IdempotencyRecord, Job, Project, ProjectRevision
from ..models import Session as SessionRow
from ..storage import MIME_EXT, object_key
from .assets import owned_asset
from .jobs import _replay

router = APIRouter()
log = logging.getLogger(__name__)

ENDPOINT = "POST /handoffs"


class HandoffCreate(BaseModel):
    source_asset_id: str = Field(max_length=64)
    # Any string: an unknown or wrong target is a 422 handoff_not_allowed, like any pair outside the allowlist.
    target_app: str = Field(max_length=40)


def _request_hash(body: HandoffCreate) -> str:
    canon = json.dumps({"source_asset_id": body.source_asset_id, "target_app": body.target_app},
                       sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def _out(project: Project, asset_id: str, job: Job) -> dict:
    return {"project_id": str(project.id), "asset_id": asset_id, "app_slug": project.app_slug,
            "inspect_job_id": str(job.id)}


def _replayed(db: DbSession, job: Job) -> dict:
    """The recorded hand-off: the inspect job points at its project and (via its options) the copied asset.

    A target project deleted since then is gone for the client too (404), never a link to a missing project.
    """
    project = db.get(Project, job.project_id)
    if project is None or project.deleted_at is not None:
        raise ApiError(404, "not_found", "The project this hand-off created was deleted.")
    return _out(project, job.options_json["asset_id"], job)


def _cleanup(storage, prefix: str) -> None:
    """Best-effort removal of the copied object; a failure here must not hide the error that got us here."""
    try:
        storage.delete_prefix(prefix)
    except Exception:
        log.exception("hand-off cleanup failed for %s", prefix)


@router.post("/handoffs", status_code=201)
def create_handoff(body: HandoffCreate, request: Request, db: DbSession = Depends(get_db),
                   session: SessionRow = Depends(current_session),
                   idempotency_key: str | None = Header(default=None, max_length=200)):
    """Copy a finished result into a new project of another tool as an ``uploaded`` input, then inspect it.

    The copy goes through the same ``asset.inspect`` as a normal upload, so every limit of the target role applies.
    The target config is not patched: the editors link the newest ready input of their role themselves.
    """
    settings = request.app.state.settings
    storage = request.app.state.storage
    rhash = _request_hash(body)
    if idempotency_key:
        existing = _replay(db, settings, session, ENDPOINT, idempotency_key, rhash)
        if existing is not None:
            return _replayed(db, existing)

    src = owned_asset(body.source_asset_id, db, session)
    if src.state != "ready":
        raise ApiError(409, "asset_not_ready", "Only a finished result can be handed off.")
    src_project = db.get(Project, src.project_id)
    target = HANDOFF_TARGETS.get((src_project.app_slug, src.role))
    if target is None or target[0] != body.target_app:
        raise ApiError(422, "handoff_not_allowed",
                       f"A {src_project.app_slug} {src.role} cannot be handed off to {body.target_app}.",
                       field="target_app")
    target_app, role = target
    limit = upload_limit(get_app(target_app), role)
    size = src.bytes if src.bytes is not None else storage.size(src.object_key)
    if size > limit:
        raise ApiError(413, "too_large", f"This file is larger than {target_app} accepts "
                                         f"({limit // (1024 * 1024)} MB maximum).", field="source_asset_id")

    # Nothing exists yet; from here on everything lands in one commit (the copied object is removed on failure).
    config = default_app_config(target_app, settings.default_profile)
    project = Project(session_id=session.id, app_slug=target_app, current_revision=1,
                      title=f"{src_project.title} → {TARGET_LABELS[target_app]}"[:120])
    db.add(project)
    db.flush()
    db.add(ProjectRevision(project_id=project.id, revision=1, config_json=config.model_dump(mode="json"),
                           config_schema_version=config.schema_version))
    asset_id = uuid.uuid4()
    key = object_key(session.id, project.id, asset_id, f"{role}-original", src.mime)
    prefix = f"{session.id}/{project.id}/"
    handoff = {"source_app": src_project.app_slug, "source_project_id": str(src_project.id),
               "source_asset_id": str(src.id), "source_job_id": str(src.job_id) if src.job_id else None,
               "source_sha256": src.sha256, "source_title": src_project.title, "mock": asset_is_mock(db, src)}
    try:
        try:
            storage.copy(src.object_key, key, src.mime)
        except FileNotFoundError:
            raise ApiError(409, "asset_missing", "The result file is no longer available.") from None
        db.add(Asset(id=asset_id, project_id=project.id, role=role, state="uploaded", object_key=key, mime=src.mime,
                     bytes=size, sha256=src.sha256, filename=f"{src_project.app_slug}-{src.role}"
                                                             f"{MIME_EXT.get(src.mime, '.bin')}",
                     source_asset_id=src.id, meta_json={"handoff": handoff}))
        job = enqueue_internal_job(db, project, "asset.inspect", {"asset_id": str(asset_id)})
        if idempotency_key:
            db.add(IdempotencyRecord(session_id=session.id, endpoint=ENDPOINT, key=idempotency_key,
                                     request_hash=rhash, job_id=job.id))
        db.commit()
    except IntegrityError:  # concurrent request with the same key won the insert
        db.rollback()
        _cleanup(storage, prefix)
        existing = _replay(db, settings, session, ENDPOINT, idempotency_key or "", rhash)
        if existing is None:
            raise
        return _replayed(db, existing)
    except BaseException:
        db.rollback()
        _cleanup(storage, prefix)
        raise
    return _out(project, str(asset_id), job)
