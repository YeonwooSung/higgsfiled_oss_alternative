from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from ..apps import get_app
from ..apps_registry import default_app_config, validate_config
from ..deps import current_session, get_db, owned_project
from ..errors import ApiError
from ..jobs_service import request_cancel
from ..db import utcnow
from ..models import ACTIVE_JOB_STATES, Asset, CleanupTask, Job, JobStage, Project, ProjectRevision, Share
from ..models import Session as SessionRow
from ..serializers import asset_out, job_out

router = APIRouter()


class ProjectCreate(BaseModel):
    app_slug: Literal["product-lock", "camera-sketch"]
    title: str = Field(min_length=1, max_length=120)


class ProjectPatch(BaseModel):
    expected_revision: int = Field(ge=1)
    config: dict


def _dig(config: dict, path: str):
    cur = config
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def referenced_asset_ids(app_slug: str, config: dict) -> list[tuple[str, str]]:
    refs = [(f, _dig(config, f)) for f in get_app(app_slug).asset_ref_fields]
    return [(f, str(v)) for f, v in refs if v]


def check_asset_refs(db: DbSession, project: Project, config: dict) -> None:
    for field, value in referenced_asset_ids(project.app_slug, config):
        asset = db.get(Asset, uuid.UUID(value))
        if asset is None or asset.project_id != project.id:
            raise ApiError(422, "asset_not_found", "Referenced asset does not belong to this project.",
                           field=f"config.{field}")
    hook = getattr(get_app(project.app_slug), "check_config_assets", None)
    if hook is not None:
        hook(db, project, config)


def current_config(db: DbSession, project: Project) -> dict:
    rev = db.get(ProjectRevision, (project.id, project.current_revision))
    return rev.config_json


def project_out(db: DbSession, project: Project, request: Request) -> dict:
    assets = db.scalars(select(Asset).where(Asset.project_id == project.id).order_by(Asset.created_at)).all()
    jobs = list(db.scalars(select(Job).where(Job.project_id == project.id).order_by(Job.created_at.desc()).limit(10)).all())
    # Results must survive a reload even after many export/inspect jobs: also the 10 newest render jobs.
    render_kinds = get_app(project.app_slug).render_kinds
    seen = {j.id for j in jobs}
    jobs += [j for j in db.scalars(select(Job).where(Job.project_id == project.id, Job.kind.in_(render_kinds))
                                   .order_by(Job.created_at.desc()).limit(10)).all() if j.id not in seen]
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return {
        "id": str(project.id),
        "app_slug": project.app_slug,
        "title": project.title,
        "current_revision": project.current_revision,
        "config": current_config(db, project),
        "assets": [asset_out(request, a) for a in assets],
        "recent_jobs": [job_out(j) for j in jobs],
        "created_at": project.created_at.isoformat(),
    }


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, request: Request, db: DbSession = Depends(get_db),
                   session: SessionRow = Depends(current_session)):
    config = default_app_config(body.app_slug, request.app.state.settings.default_profile)
    project = Project(session_id=session.id, app_slug=body.app_slug, title=body.title, current_revision=1)
    db.add(project)
    db.flush()
    db.add(ProjectRevision(project_id=project.id, revision=1, config_json=config.model_dump(mode="json"),
                           config_schema_version=config.schema_version))
    db.commit()
    return project_out(db, project, request)


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request, db: DbSession = Depends(get_db),
                session: SessionRow = Depends(current_session)):
    return project_out(db, owned_project(project_id, db, session), request)


@router.get("/projects/{project_id}/revisions/{revision}")
def get_revision(project_id: str, revision: int, db: DbSession = Depends(get_db),
                 session: SessionRow = Depends(current_session)):
    project = owned_project(project_id, db, session)
    rev = db.get(ProjectRevision, (project.id, revision))
    if rev is None:
        raise ApiError(404, "not_found", "revision not found")
    return {"revision": rev.revision, "config": rev.config_json, "created_at": rev.created_at.isoformat()}


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch, request: Request, db: DbSession = Depends(get_db),
                  session: SessionRow = Depends(current_session)):
    project = owned_project(project_id, db, session)
    config = validate_config(project.app_slug, body.config).model_dump(mode="json")
    check_asset_refs(db, project, config)
    # Optimistic concurrency: the UPDATE only matches when the revision is unchanged.
    new_rev = body.expected_revision + 1
    updated = db.query(Project).filter(Project.id == project.id,
                                       Project.current_revision == body.expected_revision).update(
        {Project.current_revision: new_rev}, synchronize_session=False)
    if updated != 1:
        db.rollback()
        db.refresh(project)
        raise ApiError(409, "revision_conflict", "The project changed since you loaded it.",
                       details={"current_revision": project.current_revision})
    db.add(ProjectRevision(project_id=project.id, revision=new_rev, config_json=config,
                           config_schema_version=config["schema_version"]))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "revision_conflict", "The project changed since you loaded it.") from None
    db.refresh(project)
    return project_out(db, project, request)


@router.delete("/projects/{project_id}", status_code=202)
def delete_project(project_id: str, db: DbSession = Depends(get_db), session: SessionRow = Depends(current_session)):
    """Access stops immediately; originals, outputs and provider artifacts are removed by cleanup tasks."""
    project = owned_project(project_id, db, session)
    now = utcnow()
    project.deleted_at = now
    for share in db.scalars(select(Share).where(Share.project_id == project.id, Share.revoked_at.is_(None))):
        share.revoked_at = now
    for job_id in db.scalars(select(Job.id).where(Job.project_id == project.id,
                                                   Job.state.in_(ACTIVE_JOB_STATES))).all():
        request_cancel(db, job_id)
    db.add(CleanupTask(project_id=project.id, target="storage", object_prefix=f"{session.id}/{project.id}/"))
    provider_jobs = db.scalar(select(JobStage.id).join(Job, Job.id == JobStage.job_id).where(
        Job.project_id == project.id, JobStage.provider_job_id.is_not(None), Job.model_profile != "mock_v1").limit(1))
    if provider_jobs is not None:
        db.add(CleanupTask(project_id=project.id, target="provider"))
    db.commit()
    return {"id": str(project.id), "deleted": True, "cleanup": "scheduled"}
