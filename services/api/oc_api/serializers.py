from __future__ import annotations

from fastapi import Request

from .models import Asset, Job
from .storage import MIME_EXT

PUBLIC_ASSET_FIELDS = ("role", "state", "sha256", "mime", "bytes", "width", "height", "frame_count",
                       "fps_num", "fps_den", "duration_ms", "error_code", "filename")


def download_name(a: Asset) -> str:
    return f"product-lock-{a.role}{MIME_EXT.get(a.mime or '', '.bin')}"


def asset_out(request: Request, a: Asset, with_url: bool = False, download: bool = False) -> dict:
    out = {"id": str(a.id), "project_id": str(a.project_id), "created_at": a.created_at.isoformat(),
           "source_asset_id": str(a.source_asset_id) if a.source_asset_id else None,
           "job_id": str(a.job_id) if a.job_id else None, "meta": a.meta_json or {}}
    out.update({f: getattr(a, f) for f in PUBLIC_ASSET_FIELDS})
    if with_url and a.state == "ready":
        storage = request.app.state.storage
        ttl = request.app.state.settings.read_url_ttl_s
        out["read_url"] = storage.read_url(a.object_key, ttl, mime=a.mime)
        if download:
            out["download_url"] = storage.read_url(a.object_key, ttl, mime=a.mime, filename=download_name(a))
    return out


def job_out(j: Job) -> dict:
    return {
        "id": str(j.id), "project_id": str(j.project_id), "kind": j.kind, "state": j.state, "stage": j.stage,
        "progress": j.progress_nullable, "input_revision": j.input_revision, "options": j.options_json,
        "model_profile": j.model_profile,
        "cancellation_requested": j.cancellation_requested_at is not None,
        "error": {"code": j.error_code, "message": j.error_message} if j.error_code else None,
        "output_asset_ids": j.output_asset_ids or [], "result": j.result_json,
        "created_at": j.created_at.isoformat(),
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }
