from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from .db import utcnow
from .models import TERMINAL_JOB_STATES, Job, JobCost, JobEvent, Project

CPU_PROFILE = "cpu"


def _next_sequence(db: DbSession, job: Job) -> int:
    return (db.scalar(select(func.max(JobEvent.sequence)).where(JobEvent.job_id == job.id)) or 0) + 1


def add_event(db: DbSession, job: Job, type_: str, payload: dict | None = None, stage: str | None = None) -> int:
    """Append an event. API and worker write concurrently, so a taken sequence is retried."""
    for _ in range(8):
        seq = _next_sequence(db, job)
        try:
            with db.begin_nested():
                db.add(JobEvent(job_id=job.id, sequence=seq, type=type_, stage=stage, payload_json=payload or {},
                                created_at=utcnow()))
            return seq
        except IntegrityError:
            continue
    raise RuntimeError("could not allocate an event sequence")


def enqueue_internal_job(db: DbSession, project: Project, kind: str, options: dict) -> Job:
    job = Job(project_id=project.id, session_id=project.session_id, input_revision=project.current_revision,
              kind=kind, options_json=options, model_profile=CPU_PROFILE, state="queued")
    db.add(job)
    db.flush()
    add_event(db, job, "state", {"state": "queued"})
    return job


def request_cancel(db: DbSession, job_id) -> str | None:
    """Compare-and-set cancel; the worker may change the row concurrently.

    Never-claimed queued jobs become `cancelled` (nothing was spent). Other non-terminal jobs become
    `cancel_requested` for the worker to resolve. Returns the new state, or None if already terminal.
    """
    now = utcnow()
    requested = func.coalesce(Job.cancellation_requested_at, now)
    res = db.execute(update(Job).where(Job.id == job_id, Job.state == "queued", Job.lease_owner.is_(None))
                     .values(state="cancelled", finished_at=now, cancellation_requested_at=requested)
                     .execution_options(synchronize_session=False))
    if res.rowcount == 1:
        db.execute(update(JobCost).where(JobCost.job_id == job_id)
                   .values(actual_units_nullable=0, settled_at=now).execution_options(synchronize_session=False))
        state = "cancelled"
    else:
        res = db.execute(update(Job).where(Job.id == job_id, Job.state.not_in(TERMINAL_JOB_STATES))
                         .values(state="cancel_requested", cancellation_requested_at=requested)
                         .execution_options(synchronize_session=False))
        if res.rowcount != 1:
            return None
        state = "cancel_requested"
    job = db.get(Job, job_id)
    db.refresh(job)
    add_event(db, job, "state", {"state": state})
    return state
