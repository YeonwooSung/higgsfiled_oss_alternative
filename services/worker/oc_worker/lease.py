"""DB-lease job claiming.

PostgreSQL: SELECT … FOR UPDATE SKIP LOCKED hands concurrent workers different rows. SQLite ignores the
hint, so the claim itself is a compare-and-set UPDATE keyed on the `attempts` value that was read (every
claim bumps it): if another worker claimed the row in between, the UPDATE matches nothing and we pick again.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.sql.elements import ColumnElement

from oc_api.models import ACTIVE_JOB_STATES, Job

LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 15
# A job whose lease lapsed this many times probably kills the worker (OOM, crash); stop retrying it.
MAX_ATTEMPTS = 3


def claim_next(db: DbSession, worker_id: str, now: datetime) -> Job | None:
    lapsed = or_(Job.lease_expires_at.is_(None), Job.lease_expires_at < now)
    eligible = or_(
        and_(Job.state == "queued", lapsed),
        and_(Job.state.in_(ACTIVE_JOB_STATES - {"queued"}), lapsed),
    )
    while True:
        job = db.scalar(select(Job).where(eligible).order_by(Job.created_at).limit(1)
                        .with_for_update(skip_locked=True))
        if job is None:
            db.rollback()
            return None
        attempts = job.attempts or 0
        unchanged = and_(Job.id == job.id, eligible, func.coalesce(Job.attempts, 0) == attempts)
        if attempts >= MAX_ATTEMPTS:
            _give_up(db, job, unchanged, now)
            continue
        res = db.execute(update(Job).where(unchanged)
                         .values(lease_owner=worker_id, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                                 attempts=attempts + 1)
                         .execution_options(synchronize_session=False))
        if res.rowcount != 1:  # another worker claimed it after our SELECT (SQLite only)
            db.rollback()
            continue
        db.commit()
        db.refresh(job)
        return job


def extend_lease(db: DbSession, job: Job, worker_id: str, now: datetime) -> bool:
    db.refresh(job)
    if job.lease_owner != worker_id:
        return False
    job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    db.commit()
    return True


def _give_up(db: DbSession, job: Job, unchanged: ColumnElement[bool], now: datetime) -> None:
    from oc_api.jobs_service import add_event

    res = db.execute(update(Job).where(unchanged)
                     .values(state="failed", error_code="worker_crashed",
                             error_message="Processing stopped repeatedly and was not retried again.",
                             lease_owner=None, lease_expires_at=None, finished_at=now)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:  # another worker got to it first
        db.rollback()
        return
    db.refresh(job)
    add_event(db, job, "state", {"state": "failed", "error": {"code": job.error_code}})
    db.commit()
