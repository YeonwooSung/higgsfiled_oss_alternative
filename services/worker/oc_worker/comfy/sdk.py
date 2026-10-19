"""Comfy API v2 runner on top of comfy-sdk 0.5.0 (synchronous; worker threads only).

SDK semantics this relies on (verified against comfy-sdk 0.5.0 source):
- `client.submit(wf, idempotency_key=k)` rejects a reused key (no replay).
- A transport failure that never reached the server (connect error) leaves the key unclaimed.
- A read timeout / dropped connection is ambiguous: the job may exist.
- Terminal statuses: succeeded, canceled, failed, expired.
"""

from __future__ import annotations

import json
import mimetypes
import os
from collections.abc import Iterator
from pathlib import Path

import httpx

from open_creative.comfy.bindings import BindingError, validate_values
from open_creative.comfy.profiles import OPEN_WEIGHTS_PROFILE, load_workflow

from .runner import (
    ProviderRejected,
    ProviderStatus,
    ProviderUnavailable,
    SubmissionKeyReused,
    SubmissionUnknown,
)

_STATE = {"succeeded": "succeeded", "canceled": "cancelled", "cancelled": "cancelled", "failed": "failed",
          "expired": "failed", "queued": "queued", "pending": "queued", "running": "running",
          "in_progress": "running"}


def _map_submit_error(e: Exception) -> Exception:
    from comfy_sdk import ComfyError, IdempotencyKeyReuse, InvalidWorkflow, MissingAsset, QueueFull

    if isinstance(e, IdempotencyKeyReuse):
        return SubmissionKeyReused(str(e))
    if isinstance(e, QueueFull):
        return ProviderUnavailable(f"queue full, retry after {e.retry_after}s")
    if isinstance(e, (InvalidWorkflow, MissingAsset)):
        return ProviderRejected(str(e))
    if isinstance(e, httpx.ConnectError):
        return ProviderUnavailable(str(e))
    if isinstance(e, (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError)):
        return SubmissionUnknown(str(e))
    if isinstance(e, ComfyError):
        status = getattr(e, "status_code", None) or getattr(e, "status", None)
        if isinstance(status, int) and 400 <= status < 500:
            return ProviderRejected(str(e))
        return SubmissionUnknown(str(e))
    return SubmissionUnknown(repr(e))


class ComfySdkRunner:
    profile = OPEN_WEIGHTS_PROFILE

    def __init__(self, client, workflows_dir: Path):
        self.client = client
        self.workflows_dir = Path(workflows_dir)

    @classmethod
    def from_env(cls, workflows_dir: Path) -> ComfySdkRunner:
        from comfy_sdk import Comfy

        return cls(Comfy(api_key=os.environ.get("COMFY_API_KEY") or None, client_info="open-creative-apps"),
                   workflows_dir)

    def capabilities(self) -> dict:
        return {"profile": self.profile, "is_mock": False, "remote_cancel": True}

    def submit(self, workflow: str, bindings: dict, submission_key: str) -> str:
        try:
            graph, manifest = load_workflow(self.workflows_dir, workflow)
            validate_values(manifest, bindings)
        except (BindingError, FileNotFoundError) as e:
            raise ProviderRejected(str(e)) from e
        try:  # nothing has been sent to /jobs yet: network errors are safe to retry
            wf = self.client.workflows.from_json(graph)
            for name, value in bindings.items():
                spec = manifest["bindings"][name]
                if spec["type"] == "image":
                    value = self.client.assets.from_file(value)
                elif spec["type"] == "json":
                    value = json.dumps(value, separators=(",", ":"))
                wf.set_input(str(spec["node_id"]), spec["input"], value)
        except httpx.HTTPError as e:
            raise ProviderUnavailable(str(e)) from e
        except Exception as e:  # noqa: BLE001 - a graph/input the SDK refuses is deterministic
            raise ProviderRejected(str(e)) from e
        try:
            job = self.client.submit(wf, idempotency_key=submission_key)
        except Exception as e:  # noqa: BLE001 - every failure is classified
            raise _map_submit_error(e) from e
        return job.id

    def _job(self, provider_job_id: str):
        return self.client.jobs.get(provider_job_id)

    def inspect(self, provider_job_id: str) -> ProviderStatus:
        job = self._job(provider_job_id).refresh()
        state = _STATE.get(job.status, "running")
        err = job.error
        return {"state": state, "progress": None, "error": None if err is None else str(err)[:500]}

    def events(self, provider_job_id: str, cursor: str | None = None) -> Iterator[dict]:
        from comfy_sdk import Progress, StatusChange

        for ev in self._job(provider_job_id).events():
            if isinstance(ev, Progress):
                yield {"type": "progress", "step": ev.step, "steps": ev.steps, "value": ev.value}
            elif isinstance(ev, StatusChange):
                yield {"type": "status", "state": _STATE.get(ev.status, "running")}

    def collect(self, provider_job_id: str, target_dir: Path, workflow: str) -> list[Path]:
        _, manifest = load_workflow(self.workflows_dir, workflow)
        job = self._job(provider_job_id).refresh()
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for name, out in manifest["outputs"].items():
            for i, o in enumerate(job.get_outputs(str(out["node_id"]))):
                ext = Path(getattr(o, "name", "") or "").suffix or (
                    mimetypes.guess_extension(getattr(o, "content_type", "") or "") or ".bin")
                paths.append(Path(o.to_file(target_dir / f"{name}_{i}{ext}")))
        return paths

    def cancel(self, provider_job_id: str) -> ProviderStatus:
        job = self._job(provider_job_id).cancel()
        return {"state": _STATE.get(job.status, "running"), "progress": None, "error": None}
