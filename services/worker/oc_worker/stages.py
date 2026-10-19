"""One job execution: state transitions, durable stages, provider submission rules, settlement."""

from __future__ import annotations

import hashlib
import logging
import random
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from PIL import Image
from sqlalchemy import select

from oc_api.apps_registry import validate_config
from oc_api.db import utcnow
from oc_api.jobs_service import add_event
from oc_api.models import Asset, Job, JobCost, JobStage, Project, ProjectRevision
from oc_api.storage import object_key
from open_creative.comfy.profiles import get_profile
from open_creative.media.video import probe

from .comfy.runner import (
    TERMINAL_PROVIDER_STATES,
    ProviderRejected,
    ProviderUnavailable,
    SubmissionKeyReused,
    SubmissionUnknown,
)
from .context import WorkerContext
from .lease import HEARTBEAT_SECONDS, extend_lease

log = logging.getLogger("oc_worker")

MAX_UNAVAILABLE_RETRIES = 3
MAX_INSPECT_ERRORS = 8
POLL_SECONDS = 1.0


class StageFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class Cancelled(Exception):
    pass


class SubmissionUnknownError(Exception):
    pass


class LeaseLost(Exception):
    pass


def backoff(attempt: int) -> float:
    """1/2/4/8… seconds with jitter, capped at 30."""
    base = min(30.0, float(2**attempt))
    return min(30.0, base * random.uniform(0.8, 1.2))


class JobRun:
    def __init__(self, ctx: WorkerContext, job_id: uuid.UUID):
        self.ctx = ctx
        self.db = ctx.db_factory()
        self.job: Job = self.db.get(Job, job_id)
        self.project: Project = self.db.get(Project, self.job.project_id)
        self._last_beat = utcnow()

    # -- bookkeeping ---------------------------------------------------------
    def close(self) -> None:
        self.db.close()

    def event(self, type_: str, payload: dict, stage: str | None = None) -> None:
        add_event(self.db, self.job, type_, payload, stage)

    def transition(self, state: str, stage: str | None = None) -> None:
        if self.job.state == "cancel_requested" and state not in ("cancelled",):
            # keep the visible cancel intent; the stage still advances
            self.job.stage = stage or self.job.stage
            self.db.commit()
            return
        self.job.state = state
        if stage is not None:
            self.job.stage = stage
        self.event("state", {"state": state}, stage)
        self.db.commit()

    def heartbeat(self, force: bool = False) -> None:
        now = utcnow()
        if force or (now - self._last_beat).total_seconds() >= HEARTBEAT_SECONDS:
            if not extend_lease(self.db, self.job, self.ctx.worker_id, now):
                raise LeaseLost()
            self._last_beat = now

    def check_cancel(self) -> None:
        self.db.refresh(self.job)
        if self.job.cancellation_requested_at is not None:
            raise Cancelled()

    def config(self):
        rev = self.db.get(ProjectRevision, (self.project.id, self.job.input_revision))
        return validate_config(self.project.app_slug, rev.config_json)

    @property
    def work_dir(self) -> Path:
        d = self.ctx.work_dir / str(self.job.id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- assets --------------------------------------------------------------
    def asset(self, asset_id) -> Asset:
        a = self.db.get(Asset, uuid.UUID(str(asset_id)))
        if a is None or a.project_id != self.project.id:
            raise StageFailed("asset_not_found", "referenced asset is missing")
        return a

    def fetch(self, asset: Asset, name: str | None = None) -> Path:
        suffix = Path(asset.object_key).suffix
        return self.ctx.storage.get_file(asset.object_key, self.work_dir / "in" / (name or f"{asset.id}{suffix}"))

    def store_asset(self, role: str, path: Path, mime: str, *, meta: dict | None = None,
                    source_asset_id=None, transform: dict | None = None) -> str:
        aid = uuid.uuid4()
        key = object_key(self.project.session_id, self.project.id, aid, role, mime)
        data_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        a = Asset(id=aid, project_id=self.project.id, role=role, state="ready", object_key=key, mime=mime,
                  bytes=Path(path).stat().st_size, sha256=data_hash, job_id=self.job.id, meta_json=meta,
                  source_asset_id=uuid.UUID(str(source_asset_id)) if source_asset_id else None,
                  transform_json=transform)
        if mime.startswith("image/"):
            with Image.open(path) as im:
                a.width, a.height = im.size
        elif mime == "video/mp4":
            info = probe(path)
            a.width, a.height, a.frame_count = info["width"], info["height"], info["frame_count"]
            a.fps_num, a.fps_den, a.duration_ms = info["fps_num"], info["fps_den"], info["duration_ms"]
        self.ctx.storage.put_file(key, path, mime)
        self.db.add(a)
        self.db.commit()
        return str(aid)

    # -- stages --------------------------------------------------------------
    def stage(self, name: str) -> JobStage:
        st = self.db.scalar(select(JobStage).where(JobStage.job_id == self.job.id, JobStage.stage_name == name)
                            .order_by(JobStage.attempt.desc()).limit(1))
        if st is None:
            st = JobStage(job_id=self.job.id, stage_name=name, state="pending", attempt=1)
            self.db.add(st)
            self.db.commit()
        return st

    def ensure_stage(self, name: str, produce: Callable[[JobStage], list[str]]) -> list[str]:
        """Run a stage once; a stage with durable outputs is skipped on resume."""
        st = self.stage(name)
        if st.state == "succeeded":
            return list(st.output_asset_ids or [])
        self.check_cancel()
        self.heartbeat(force=True)
        outputs = produce(st)
        st.state, st.output_asset_ids = "succeeded", outputs
        self.event("stage", {"stage": name, "state": "succeeded"}, name)
        self.db.commit()
        return outputs

    def provider_stage(self, st: JobStage, workflow: str, bindings: dict) -> list[Path]:
        runner = self.ctx.runner_for(self.job.model_profile)
        if st.provider_job_id is None:
            # The key is persisted before the request. Resending the same key after an interrupted
            # submit is safe: the provider rejects a key it already saw (reject-on-duplicate).
            st.submission_key = st.submission_key or f"{self.job.id}:{st.stage_name}:{st.attempt}"
            st.state = "submitting"
            self.db.commit()
            self.check_cancel()
            self.transition("submitting", st.stage_name)
            for attempt in range(MAX_UNAVAILABLE_RETRIES + 1):
                try:
                    st.provider_job_id = runner.submit(workflow, bindings, st.submission_key)
                    break
                except ProviderUnavailable as e:
                    if attempt == MAX_UNAVAILABLE_RETRIES:
                        st.state, st.error_code = "failed", "provider_unavailable"
                        raise StageFailed("provider_unavailable", "The generation service is unreachable.") from e
                    self.ctx.sleep(backoff(attempt))
                    self.heartbeat()
                except ProviderRejected as e:
                    st.state, st.error_code = "failed", "provider_rejected"
                    raise StageFailed("provider_rejected", f"The workflow was rejected: {e}") from e
                except (SubmissionUnknown, SubmissionKeyReused) as e:
                    st.state, st.error_code = "unknown", "submission_unknown"
                    raise SubmissionUnknownError(str(e)) from e
            st.state = "running"
            self.db.commit()
        self.transition("running", st.stage_name)

        errors, cancel_sent = 0, False
        while True:
            try:
                status = runner.inspect(st.provider_job_id)
                errors = 0
            except Exception as e:  # noqa: BLE001 - transient lookup failure; never resubmit
                errors += 1
                if errors > MAX_INSPECT_ERRORS:
                    raise StageFailed("provider_lookup_failed", "Lost track of the generation job.") from e
                self.ctx.sleep(backoff(errors - 1))
                self.heartbeat()
                continue
            if status["state"] in TERMINAL_PROVIDER_STATES:
                break
            self.db.refresh(self.job)
            if self.job.cancellation_requested_at is not None and not cancel_sent:
                cancel_sent = True
                try:
                    runner.cancel(st.provider_job_id)
                except Exception:  # noqa: BLE001 - best effort
                    log.warning("provider cancel failed job=%s", self.job.id)
            if status.get("progress") is not None:
                self.job.progress_nullable = min(0.99, float(status["progress"]))
                self.event("progress", {"progress": self.job.progress_nullable}, st.stage_name)
                self.db.commit()
            self.ctx.sleep(POLL_SECONDS)
            self.heartbeat()

        self.db.refresh(self.job)
        if status["state"] == "cancelled" or self.job.cancellation_requested_at is not None:
            st.state = "cancelled"
            raise Cancelled()
        if status["state"] == "failed":
            st.state, st.error_code = "failed", "provider_failed"
            raise StageFailed("provider_failed", "Generation failed on the provider.")
        out_dir = self.work_dir / st.stage_name
        if out_dir.exists():
            shutil.rmtree(out_dir)
        return runner.collect(st.provider_job_id, out_dir, workflow)

    # -- completion ----------------------------------------------------------
    def _settle(self) -> None:
        submitted = self.db.scalar(select(JobStage).where(JobStage.job_id == self.job.id,
                                                          JobStage.submission_key.is_not(None)).limit(1))
        if self.job.model_profile == "cpu" or submitted is None:
            known_zero = True  # nothing was sent to a provider
        else:
            known_zero = get_profile(self.job.model_profile).is_mock
        for cost in self.db.scalars(select(JobCost).where(JobCost.job_id == self.job.id)):
            if self.job.state == "submission_unknown" or not known_zero:
                cost.actual_units_nullable = None  # usage not reported: unknown, never 0
            else:
                cost.actual_units_nullable = 0
            cost.settled_at = utcnow()

    def finish(self, state: str, *, error_code: str | None = None, message: str | None = None,
               outputs: list[str] | None = None, result: dict | None = None) -> None:
        # A worker that stalled past its lease must not overwrite the new owner's job.
        self.heartbeat(force=True)
        self.job.state = state
        self.job.error_code, self.job.error_message = error_code, message
        if outputs is not None:
            self.job.output_asset_ids = outputs
        if result is not None:
            self.job.result_json = result
        if state == "succeeded":
            self.job.progress_nullable = 1.0
        self.job.finished_at = utcnow()
        self.job.lease_owner = None
        self.job.lease_expires_at = None
        self._settle()
        payload = {"state": state}
        if error_code:
            payload["error"] = {"code": error_code, "message": message}
        if outputs:
            payload["output_asset_ids"] = outputs
        self.event("state", payload, self.job.stage)
        self.db.commit()
        shutil.rmtree(self.ctx.work_dir / str(self.job.id), ignore_errors=True)
