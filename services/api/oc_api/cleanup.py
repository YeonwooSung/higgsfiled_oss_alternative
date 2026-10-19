"""Deletion work scheduled by project delete. Internal storage and provider artifacts are tracked apart."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from .db import utcnow
from .models import Asset, CleanupTask


def run_cleanup(db_factory: sessionmaker, storage, limit: int = 20) -> int:
    done = 0
    with db_factory() as db:
        tasks = db.scalars(select(CleanupTask).where(CleanupTask.state == "pending", CleanupTask.target == "storage")
                           .limit(limit)).all()
        for task in tasks:
            if task.object_prefix:
                storage.delete_prefix(task.object_prefix)
            db.execute(delete(Asset).where(Asset.project_id == task.project_id))
            task.state, task.done_at = "done", utcnow()
            done += 1
        db.commit()
    return done
