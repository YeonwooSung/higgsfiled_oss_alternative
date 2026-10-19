"""Persistent state (shared spec §5). Revisions are immutable; jobs pin a revision."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, UTCDateTime, utcnow

JOB_STATES = ("queued", "preparing", "submitting", "running", "postprocessing", "succeeded",
              "failed", "cancel_requested", "cancelled", "submission_unknown")
TERMINAL_JOB_STATES = {"succeeded", "failed", "cancelled", "submission_unknown"}
ACTIVE_JOB_STATES = set(JOB_STATES) - TERMINAL_JOB_STATES


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = _id()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    daily_budget_limit: Mapped[float | None] = mapped_column(Float, nullable=True)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = _id()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    app_slug: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(120))
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class ProjectRevision(Base):
    __tablename__ = "project_revisions"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_json: Mapped[dict] = mapped_column(JSON)
    config_schema_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(40))
    # pending_upload → uploaded → (inspecting) → ready | rejected; generated assets start ready
    state: Mapped[str] = mapped_column(String(24))
    object_key: Mapped[str] = mapped_column(String(400))
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frame_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps_den: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transform_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    input_revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))
    options_json: Mapped[dict] = mapped_column(JSON, default=dict)
    model_profile: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    progress_nullable: Mapped[float | None] = mapped_column(Float, nullable=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class JobStage(Base):
    __tablename__ = "job_stages"
    __table_args__ = (UniqueConstraint("job_id", "stage_name", "attempt"),)
    id: Mapped[uuid.UUID] = _id()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    stage_name: Mapped[str] = mapped_column(String(60))
    state: Mapped[str] = mapped_column(String(24), default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    submission_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    provider_job_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    build_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    workflow_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class JobEvent(Base):
    __tablename__ = "job_events"
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    stage: Mapped[str | None] = mapped_column(String(60), nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class JobCost(Base):
    __tablename__ = "job_costs"
    id: Mapped[uuid.UUID] = _id()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    stage_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reserved_units: Mapped[float] = mapped_column(Float)
    estimated_units_nullable: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_units_nullable: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(24))
    provider_usage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Share(Base):
    __tablename__ = "shares"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    output_asset_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("session_id", "endpoint", "key"),)
    id: Mapped[uuid.UUID] = _id()
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    endpoint: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CleanupTask(Base):
    """Deletion work scheduled after a project delete (internal data and provider artifacts tracked apart)."""

    __tablename__ = "cleanup_tasks"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    target: Mapped[str] = mapped_column(String(24))  # storage | provider
    object_prefix: Mapped[str | None] = mapped_column(String(400), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="pending")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
