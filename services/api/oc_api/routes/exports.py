from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..deps import current_session, get_db, owned_project
from ..errors import ApiError
from ..jobs_service import enqueue_internal_job
from ..models import Job
from ..models import Session as SessionRow
from ..serializers import job_out

router = APIRouter()


class ExportCreate(BaseModel):
    job_id: uuid.UUID
    format: Literal["mp4", "cover_png", "recipe_zip"]


@router.post("/projects/{project_id}/exports", status_code=202)
def create_export(project_id: str, body: ExportCreate, db: DbSession = Depends(get_db),
                  session: SessionRow = Depends(current_session)):
    project = owned_project(project_id, db, session)
    render = db.get(Job, body.job_id)
    if render is None or render.project_id != project.id or render.kind != "product.render":
        raise ApiError(422, "render_not_found", "Render job not found.", field="job_id")
    if render.state != "succeeded":
        raise ApiError(422, "render_not_finished", "Render has not finished.", field="job_id")
    job = enqueue_internal_job(db, project, "export", {"render_job_id": str(render.id), "format": body.format})
    job.input_revision = render.input_revision
    db.commit()
    return job_out(job)
