from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from .db import utcnow
from .errors import ApiError
from .models import Project
from .models import Session as SessionRow

SESSION_COOKIE = "oc_session"


def get_db(request: Request) -> Iterator[DbSession]:
    db = request.app.state.db()
    try:
        yield db
    finally:
        db.close()


def parse_uuid(value: str, what: str = "resource") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ApiError(404, "not_found", f"{what} not found") from None


def current_session(request: Request, db: DbSession = Depends(get_db)) -> SessionRow:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        try:
            row = db.get(SessionRow, uuid.UUID(raw))
        except ValueError:
            row = None
        if row is not None and row.expires_at > utcnow():
            return row
    raise ApiError(401, "session_required", "Start a session first.")


def owned_project(project_id: str, db: DbSession, session: SessionRow) -> Project:
    pid = parse_uuid(project_id, "project")
    project = db.scalar(select(Project).where(Project.id == pid, Project.session_id == session.id,
                                              Project.deleted_at.is_(None)))
    if project is None:
        raise ApiError(404, "not_found", "project not found")
    return project
