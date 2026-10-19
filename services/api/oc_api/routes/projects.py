from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from ..apps_registry import default_app_config, validate_config
from ..deps import current_session, get_db, owned_project
from ..errors import ApiError
from ..models import Asset, Job, Project, ProjectRevision
from ..models import Session as SessionRow
from ..serializers import asset_out, job_out

router = APIRouter()

ASSET_REF_FIELDS = ("product_asset_id", "mask_asset_id", "core_mask_asset_id")
SCENE_ASSET_FIELDS = ("background_asset_id", "background_video_asset_id")


class ProjectCreate(BaseModel):
    app_slug: Literal["product-lock"]
    title: str = Field(min_length=1, max_length=120)


class ProjectPatch(BaseModel):
    expected_revision: int = Field(ge=1)
    config: dict


def referenced_asset_ids(config: dict) -> list[tuple[str, str]]:
    refs = [(f, config.get(f)) for f in ASSET_REF_FIELDS]
    scene = config.get("scene") or {}
    refs += [(f"scene.{f}", scene.get(f)) for f in SCENE_ASSET_FIELDS]
    return [(f, str(v)) for f, v in refs if v]


def check_asset_refs(db: DbSession, project: Project, config: dict) -> None:
    for field, value in referenced_asset_ids(config):
        asset = db.get(Asset, uuid.UUID(value))
        if asset is None or asset.project_id != project.id:
            raise ApiError(422, "asset_not_found", "Referenced asset does not belong to this project.",
                           field=f"config.{field}")


def current_config(db: DbSession, project: Project) -> dict:
    rev = db.get(ProjectRevision, (project.id, project.current_revision))
    return rev.config_json


def project_out(db: DbSession, project: Project, request: Request) -> dict:
    assets = db.scalars(select(Asset).where(Asset.project_id == project.id).order_by(Asset.created_at)).all()
    jobs = db.scalars(select(Job).where(Job.project_id == project.id).order_by(Job.created_at.desc()).limit(10)).all()
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
