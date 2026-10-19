from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from open_creative.comfy.profiles import PROFILES, profile_status
from open_creative.product_lock.prompts import load_presets

from ..apps_registry import APP_KINDS, validate_config
from ..db import utcnow
from ..deps import current_session, get_db, owned_project, parse_uuid
from ..errors import ApiError
from ..jobs_service import add_event, request_cancel
from ..models import (ACTIVE_JOB_STATES, TERMINAL_JOB_STATES, IdempotencyRecord, Job, JobCost, JobEvent,
                      JobStage, Project, ProjectRevision)
from ..models import Session as SessionRow
from ..product_lock.jobs import plan_job
from ..serializers import job_out

router = APIRouter()


class JobCreate(BaseModel):
    kind: str = Field(max_length=40)
    revision: int = Field(ge=1)
    options: dict = Field(default_factory=dict)


class EstimateRequest(BaseModel):
    kind: str
    revision: int = Field(ge=1)
    options: dict = Field(default_factory=dict)


def _status(request: Request, profile_id: str):
    s = request.app.state.settings
    if profile_id not in PROFILES:
        raise ApiError(422, "unknown_profile", f"unknown profile {profile_id!r}", field="config.model_profile")
    return profile_status(profile_id, s.workflows_dir, s.comfy_base_url)


def _profile_out(st) -> dict:
    return {"id": st.id, "label": st.label, "is_mock": st.is_mock, "available": st.available, "reason": st.reason}


def _pinned_config(db: DbSession, project: Project, revision: int):
    rev = db.get(ProjectRevision, (project.id, revision))
    if rev is None:
        raise ApiError(422, "revision_not_found", "Revision does not exist.", field="revision")
    return validate_config(project.app_slug, rev.config_json)


def _owned_job(job_id: str, db: DbSession, session: SessionRow) -> Job:
    job = db.scalar(select(Job).join(Project, Project.id == Job.project_id).where(
        Job.id == parse_uuid(job_id, "job"), Job.session_id == session.id, Project.deleted_at.is_(None)))
    if job is None:
        raise ApiError(404, "not_found", "job not found")
    return job


def _check_quotas(db: DbSession, settings, session: SessionRow, project: Project, kind: str, uses_gpu: bool):
    active = select(func.count()).select_from(Job).where(Job.session_id == session.id,
                                                         Job.state.in_(ACTIVE_JOB_STATES),
                                                         Job.kind.not_like("asset.%"))
    if db.scalar(active) >= settings.max_queued_per_session:
        raise ApiError(429, "queue_full", "Too many jobs are waiting. Try again when one finishes.", retryable=True)
    if uses_gpu and db.scalar(active.where(Job.uses_gpu.is_(True))) >= settings.max_gpu_jobs_per_session:
        raise ApiError(429, "gpu_busy", "A generation is already running for this session.", retryable=True)
    if kind == "product.render":
        renders = select(func.count()).select_from(Job).where(Job.project_id == project.id,
                                                              Job.kind == "product.render",
                                                              Job.state.in_(ACTIVE_JOB_STATES))
        if db.scalar(renders) >= settings.max_active_renders_per_project:
            raise ApiError(429, "render_busy", "A render is already running for this project.", retryable=True)


def _request_hash(body: JobCreate) -> str:
    canon = json.dumps({"kind": body.kind, "revision": body.revision, "options": body.options},
                       sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def _replay(db: DbSession, settings, session: SessionRow, endpoint: str, key: str, rhash: str) -> Job | None:
    rec = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.session_id == session.id,
                                                    IdempotencyRecord.endpoint == endpoint,
                                                    IdempotencyRecord.key == key))
    if rec is None:
        return None
    if rec.created_at < utcnow() - timedelta(hours=settings.idempotency_retention_h):
        db.delete(rec)
        db.commit()
        return None
    if rec.request_hash != rhash:
        raise ApiError(409, "idempotency_conflict", "Idempotency-Key was already used with a different request.")
    return db.get(Job, rec.job_id)


@router.post("/projects/{project_id}/jobs", status_code=202)
def create_job(project_id: str, body: JobCreate, request: Request, db: DbSession = Depends(get_db),
               session: SessionRow = Depends(current_session),
               idempotency_key: str | None = Header(default=None, max_length=200)):
    settings = request.app.state.settings
    project = owned_project(project_id, db, session)
    if body.kind not in APP_KINDS[project.app_slug]:
        raise ApiError(422, "invalid_kind", f"kind {body.kind!r} is not available", field="kind")
    endpoint = f"POST /projects/{project.id}/jobs"
    rhash = _request_hash(body)
    if idempotency_key:
        existing = _replay(db, settings, session, endpoint, idempotency_key, rhash)
        if existing is not None:
            return job_out(existing)

    cfg = _pinned_config(db, project, body.revision)
    plan = plan_job(db, request.app.state.storage, project, cfg, body.kind, body.options)
    if plan.uses_gpu:
        st = _status(request, cfg.model_profile)
        if not st.available:
            raise ApiError(503, "profile_unavailable", f"{st.label} profile is not available: {st.reason}",
                           retryable=True)
    _check_quotas(db, settings, session, project, body.kind, plan.uses_gpu)

    job = Job(project_id=project.id, session_id=session.id, input_revision=body.revision, kind=body.kind,
              options_json=plan.options, model_profile=cfg.model_profile, uses_gpu=plan.uses_gpu,
              state="queued", idempotency_key=idempotency_key, request_hash=rhash)
    db.add(job)
    db.flush()
    db.add(JobCost(job_id=job.id, reserved_units=plan.reserved_units, unit=plan.unit))
    add_event(db, job, "state", {"state": "queued", "stages": plan.stages})
    if idempotency_key:
        db.add(IdempotencyRecord(session_id=session.id, endpoint=endpoint, key=idempotency_key,
                                 request_hash=rhash, job_id=job.id))
    try:
        db.commit()
    except IntegrityError:  # concurrent request with the same key won the insert
        db.rollback()
        existing = _replay(db, settings, session, endpoint, idempotency_key or "", rhash)
        if existing is None:
            raise
        return job_out(existing)
    return job_out(job)


@router.post("/projects/{project_id}/estimates")
def estimate(project_id: str, body: EstimateRequest, request: Request, db: DbSession = Depends(get_db),
             session: SessionRow = Depends(current_session)):
    project = owned_project(project_id, db, session)
    if body.kind not in APP_KINDS[project.app_slug]:
        raise ApiError(422, "invalid_kind", f"kind {body.kind!r} is not available", field="kind")
    cfg = _pinned_config(db, project, body.revision)
    options = body.options or _default_estimate_options(body.kind, cfg)
    plan = plan_job(db, request.app.state.storage, project, cfg, body.kind, options, validate_assets=False)
    st = _status(request, cfg.model_profile)
    return {"kind": body.kind, "stages": plan.stages, "uses_gpu": plan.uses_gpu,
            "reserved_units": plan.reserved_units, "unit": plan.unit, "price": None, "price_status": "unknown",
            "profile": _profile_out(st), "notes": plan.notes, "creates_new_job": True}


def _default_estimate_options(kind: str, cfg) -> dict:
    if kind == "product.background":
        return {"preset_id": cfg.scene.preset_id, "seed": cfg.seed}
    if kind == "product.background_video":
        return {"background_asset_id": str(cfg.scene.background_asset_id or uuid.UUID(int=0)), "seed": cfg.seed}
    if kind == "product.segment":
        return {"points": [{"x": 0.5, "y": 0.5, "label": 1}]}
    if kind == "product.inspect":
        return {"render_job_id": str(uuid.UUID(int=0))}
    return {}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: DbSession = Depends(get_db), session: SessionRow = Depends(current_session)):
    job = _owned_job(job_id, db, session)
    stages = db.scalars(select(JobStage).where(JobStage.job_id == job.id).order_by(JobStage.created_at)).all()
    out = job_out(job)
    out["stages"] = [{"name": s.stage_name, "state": s.state, "attempt": s.attempt, "error_code": s.error_code}
                     for s in stages]
    return out


@router.post("/jobs/{job_id}/cancel", status_code=202)
def cancel_job(job_id: str, db: DbSession = Depends(get_db), session: SessionRow = Depends(current_session)):
    job = _owned_job(job_id, db, session)
    state = request_cancel(db, job.id)
    if state is None:
        db.rollback()
        db.refresh(job)
        raise ApiError(409, "already_finished", f"Job already {job.state}.")
    db.commit()
    db.refresh(job)
    return job_out(job)


@router.get("/jobs/{job_id}/events")
def job_events(job_id: str, request: Request, db: DbSession = Depends(get_db),
               session: SessionRow = Depends(current_session),
               last_event_id: str | None = Header(default=None)):
    job = _owned_job(job_id, db, session)
    try:
        start = int(last_event_id or request.query_params.get("last_event_id") or 0)
    except ValueError:
        start = 0
    make_db = request.app.state.db
    jid = job.id

    def stream():
        last, idle = start, 0.0
        yield "retry: 2000\n\n"
        while True:
            with make_db() as s:
                events = s.scalars(select(JobEvent).where(JobEvent.job_id == jid, JobEvent.sequence > last)
                                   .order_by(JobEvent.sequence)).all()
                state = s.get(Job, jid).state
            for ev in events:
                last = ev.sequence
                payload = {"type": ev.type, "stage": ev.stage, **ev.payload_json,
                           "created_at": ev.created_at.isoformat()}
                yield f"id: {ev.sequence}\nevent: {ev.type}\ndata: {json.dumps(payload)}\n\n"
            if state in TERMINAL_JOB_STATES and not events:
                return
            time.sleep(0.5)
            idle += 0.5
            if idle >= 15:
                idle = 0.0
                yield ": keep-alive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/jobs/{job_id}/integrity")
def job_integrity(job_id: str, db: DbSession = Depends(get_db), session: SessionRow = Depends(current_session)):
    job = _owned_job(job_id, db, session)
    integrity = (job.result_json or {}).get("integrity") if job.kind == "product.render" else None
    if integrity is None:
        raise ApiError(404, "integrity_not_available", "No integrity report for this job.")
    return {"job_id": str(job.id), "revision": job.input_revision, **integrity}


@router.get("/capabilities")
def capabilities(request: Request, app_slug: str = "product-lock"):
    if app_slug not in APP_KINDS:
        raise ApiError(422, "unknown_app", f"unknown app {app_slug!r}", field="app_slug")
    s = request.app.state.settings
    profiles = [_profile_out(profile_status(pid, s.workflows_dir, s.comfy_base_url)) for pid in PROFILES]
    return {
        "app_slug": app_slug,
        "default_profile": s.default_profile,
        "profiles": profiles,
        "kinds": list(APP_KINDS[app_slug]),
        "presets": [p.public() for p in load_presets().values()],
        "limits": {"duration_ms": 5000, "fps": 24, "aspects": ["9:16", "16:9"],
                   "output_sizes": {"9:16": [720, 1280], "16:9": [1280, 720]},
                   "image_max_bytes": 20 * 1024 * 1024, "image_max_pixels": 24_000_000,
                   "width_ratio": [0.15, 0.70], "feather_px": [0, 4], "shadow_opacity": [0, 0.6],
                   "shadow_blur_px": [0, 40], "background_brightness": [-0.2, 0.2], "brush_px": [2, 80],
                   "extra_prompt_max": 300},
    }


@router.get("/product-lock/presets")
def presets():
    return {"presets": [{**p.public(), "thumbnail_url": None} for p in load_presets().values()]}
