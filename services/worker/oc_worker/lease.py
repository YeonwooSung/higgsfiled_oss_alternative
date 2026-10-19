"""DB-lease job claiming: SELECT … FOR UPDATE SKIP LOCKED (PostgreSQL); SQLite ignores the hint."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session as DbSession

from oc_api.models import ACTIVE_JOB_STATES, Job

LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 15


def claim_next(db: DbSession, worker_id: str, now: datetime) -> Job | None:
    lapsed = or_(Job.lease_expires_at.is_(None), Job.lease_expires_at < now)
    eligible = or_(
        and_(Job.state == "queued", lapsed),
        and_(Job.state.in_(ACTIVE_JOB_STATES - {"queued"}), lapsed),
    )
    job = db.scalar(select(Job).where(eligible).order_by(Job.created_at).limit(1)
                    .with_for_update(skip_locked=True))
    if job is None:
        db.rollback()
        return None
    job.lease_owner = worker_id
    job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    job.attempts = (job.attempts or 0) + 1
    db.commit()
    return job


def extend_lease(db: DbSession, job: Job, worker_id: str, now: datetime) -> bool:
    db.refresh(job)
    if job.lease_owner != worker_id:
        return False
    job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    db.commit()
    return True
