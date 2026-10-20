"""Signed-URL endpoints for LocalStorage (dev / single-host deployments)."""

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from ..errors import ApiError
from ..storage import LocalStorage, TokenError

router = APIRouter()


def _local(request: Request) -> LocalStorage:
    storage = request.app.state.storage
    if not isinstance(storage, LocalStorage):
        raise ApiError(404, "not_found", "not found")
    return storage


@router.put("/storage/o/{token}", include_in_schema=False)
async def put_object(token: str, request: Request):
    storage = _local(request)
    try:
        claims = storage.verify(token, "put")
    except TokenError:
        raise ApiError(403, "invalid_upload_url", "Upload link is invalid or expired.") from None
    if request.headers.get("content-type", "").split(";")[0].strip() != claims["mime"]:
        raise ApiError(415, "unsupported_media_type", "Content-Type does not match the declared type.")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > claims["max"]:
            raise ApiError(413, "too_large", "Upload is larger than declared.")
        chunks.append(chunk)
    storage.put_bytes(claims["k"], b"".join(chunks), claims["mime"])
    return Response(status_code=200)


@router.get("/storage/o/{token}", include_in_schema=False)
def get_object(token: str, request: Request):
    storage = _local(request)
    try:
        claims = storage.verify(token, "get")
    except TokenError:
        raise ApiError(403, "invalid_read_url", "Link is invalid or expired.") from None
    if not storage.exists(claims["k"]):
        raise ApiError(404, "not_found", "not found")
    headers = {"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff"}
    if claims.get("fn"):  # server-generated names only
        headers["Content-Disposition"] = f'attachment; filename="{claims["fn"]}"'
    # FileResponse answers Range requests (206) and advertises Accept-Ranges, so result videos are seekable.
    return FileResponse(storage.local_path(claims["k"]), media_type=claims.get("mime") or "application/octet-stream",
                        headers=headers)
