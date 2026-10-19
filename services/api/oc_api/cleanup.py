"""Deletion work scheduled by project delete. Internal storage and provider artifacts are tracked apart."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from .db import utcnow
from .models import ACTIVE_JOB_STATES, Asset, CleanupTask, Job


def run_cleanup(db_factory: sessionmaker, storage, limit: int = 20) -> int:
    done = 0
    with db_factory() as db:
        tasks = db.scalars(select(CleanupTask).where(CleanupTask.state == "pending", CleanupTask.target == "storage")
                           .limit(limit)).all()
        for task in tasks:
            busy = db.scalar(select(Job.id).where(Job.project_id == task.project_id,
                                                  Job.state.in_(ACTIVE_JOB_STATES)).limit(1))
            if busy is not None:
                continue  # wait until cancelled jobs stop writing outputs
            if task.object_prefix:
                storage.delete_prefix(task.object_prefix)
            db.execute(delete(Asset).where(Asset.project_id == task.project_id))
            task.state, task.done_at = "done", utcnow()
            done += 1
        db.commit()
    return done
