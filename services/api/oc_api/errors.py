"""Uniform error envelope: {error:{code,message,field?,retryable,request_id}}."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("oc_api")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, field: str | None = None,
                 retryable: bool = False, details: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.field, self.retryable, self.details = field, retryable, details


def request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if rid is None:
        rid = request.state.request_id = uuid.uuid4().hex
    return rid


def error_body(request: Request, code: str, message: str, field=None, retryable=False, details=None) -> dict:
    err = {"code": code, "message": message, "retryable": retryable, "request_id": request_id(request)}
    if field is not None:
        err["field"] = field
    if details:
        err["details"] = details
    return {"error": err}


def _status_code_name(status: int) -> str:
    return {400: "bad_request", 401: "session_required", 403: "forbidden", 404: "not_found",
            405: "method_not_allowed", 409: "conflict", 413: "too_large", 415: "unsupported_media_type",
            422: "invalid_request", 429: "quota_exceeded", 503: "unavailable"}.get(status, "error")


def install_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def _rid(request: Request, call_next):
        rid = request.headers.get("x-request-id")
        request.state.request_id = rid if rid and len(rid) <= 64 and rid.isprintable() else uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError):
        return JSONResponse(error_body(request, exc.code, exc.message, exc.field, exc.retryable, exc.details),
                            status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = [str(p) for p in first.get("loc", []) if p not in ("body", "query", "path")]
        return JSONResponse(error_body(request, "invalid_request", first.get("msg", "invalid request"),
                                       ".".join(loc) or None), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return JSONResponse(error_body(request, _status_code_name(exc.status_code), str(exc.detail)),
                            status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error request_id=%s", request_id(request))
        return JSONResponse(error_body(request, "internal_error", "Something went wrong.", retryable=True),
                            status_code=500)
