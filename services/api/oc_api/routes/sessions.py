from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session as DbSession

from ..db import utcnow
from ..deps import SESSION_COOKIE, get_db, optional_session
from ..models import Session as SessionRow

router = APIRouter()


@router.post("/sessions", status_code=201)
def create_session(request: Request, response: Response, db: DbSession = Depends(get_db)):
    settings = request.app.state.settings
    row = optional_session(request, db)
    if row is None:
        row = SessionRow(expires_at=utcnow() + timedelta(days=settings.session_ttl_days))
        db.add(row)
        db.commit()
    response.set_cookie(SESSION_COOKIE, str(row.id), httponly=True, secure=settings.cookie_secure,
                        samesite="lax", max_age=settings.session_ttl_days * 86400, path="/")
    return {"session_id": str(row.id), "expires_at": row.expires_at.isoformat()}
