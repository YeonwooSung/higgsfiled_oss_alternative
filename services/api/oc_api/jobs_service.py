from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from .db import utcnow
from .models import Job, JobEvent, Project

CPU_PROFILE = "cpu"


def add_event(db: DbSession, job: Job, type_: str, payload: dict | None = None, stage: str | None = None) -> int:
    seq = (db.scalar(select(func.max(JobEvent.sequence)).where(JobEvent.job_id == job.id)) or 0) + 1
    db.add(JobEvent(job_id=job.id, sequence=seq, type=type_, stage=stage, payload_json=payload or {},
                    created_at=utcnow()))
    return seq


def enqueue_internal_job(db: DbSession, project: Project, kind: str, options: dict) -> Job:
    job = Job(project_id=project.id, session_id=project.session_id, input_revision=project.current_revision,
              kind=kind, options_json=options, model_profile=CPU_PROFILE, state="queued")
    db.add(job)
    db.flush()
    add_event(db, job, "state", {"state": "queued"})
    return job
