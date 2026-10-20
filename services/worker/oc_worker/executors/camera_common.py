"""Shared helpers of the Camera Sketch executors (asset loading, path artifact, output contract)."""

from __future__ import annotations

import contextlib
import json
import uuid
from types import SimpleNamespace

import numpy as np

from oc_api.apps_registry import validate_config
from oc_api.camera_sketch.config import OUTPUT_SIZES
from oc_api.errors import ApiError
from oc_api.models import Asset, Job, ProjectRevision

from ..stages import JobRun, StageFailed

GEOMETRY_LABEL = "Path render"
GEOMETRY_LABEL_KO = "경로 렌더"
CINEMATIC_LABEL = "AI generated"
CINEMATIC_LABEL_KO = "AI 생성"


@contextlib.contextmanager
def api_errors():
    """Shared API-side loaders raise ``ApiError``; a job reports them as a stage failure."""
    try:
        yield
    except ApiError as e:
        raise StageFailed(e.code, e.message) from e


def output_shape(cfg) -> tuple[int, int, int]:
    """(width, height, frames) of the output contract: 1280x720 / 720x1280, 5 s @ 24 fps = 120."""
    w, h = OUTPUT_SIZES[cfg.output.aspect]
    return w, h, cfg.output.duration_ms * cfg.output.fps // 1000


def ready_asset(run: JobRun, asset_id, role: str, missing_code: str) -> Asset:
    if asset_id is None:
        raise StageFailed(missing_code, f"The project has no {role} asset.")
    a = run.asset(asset_id)
    if a.role != role or a.state != "ready":
        raise StageFailed(missing_code, f"The {role} asset is not ready.")
    return a


def json_asset(run: JobRun, asset_id) -> dict:
    return json.loads(run.ctx.storage.get_bytes(run.asset(asset_id).object_key))


def revision_config(run: JobRun, revision: int):
    rev = run.db.get(ProjectRevision, (run.project.id, revision))
    if rev is None:
        raise StageFailed("revision_not_found", "The job's revision no longer exists.")
    return rev.config_json, validate_config(run.project.app_slug, rev.config_json)


def source_job(run: JobRun, job_id, kinds: tuple[str, ...], code: str, message: str) -> Job:
    """A succeeded job of this project and one of ``kinds``; a missing/malformed id is the same failure."""
    try:
        jid = uuid.UUID(str(job_id)) if job_id else None
    except ValueError:
        jid = None
    job = run.db.get(Job, jid) if jid else None
    if job is None or job.project_id != run.project.id or job.kind not in kinds or job.state != "succeeded":
        raise StageFailed(code, message)
    return job


def compiled_from_dict(d: dict) -> SimpleNamespace:
    """The renderer only needs status / samples / K / source_to_output of a stored compiled path."""
    return SimpleNamespace(status=d["status"], samples=d["samples"], K=d["K"],
                           source_to_output=d["source_to_output"])


def target_px_out(artifact: dict) -> tuple[float, float]:
    """Target pixel in OUTPUT space on frame 0, re-derived from a stored path artifact (no recompile).

    Output pixel of the source target is ``A @ p_src`` (cover-fit); ``K_out = A @ K_src``, so the target ray is
    ``K_out^-1 @ p_out`` and the world point is that ray times ``target.depth_scale``.
    """
    c, user = artifact["compiled"], artifact["user_config"]
    fit = c["source_to_output"]
    px = np.array([user["target"]["u"] * fit["src_w"] * fit["scale"] + fit["offset_x"],
                   user["target"]["v"] * fit["src_h"] * fit["scale"] + fit["offset_y"], 1.0])
    K = np.asarray(c["K"], float)
    X = np.linalg.inv(K) @ px * float(user["target"]["depth_scale"])
    T = np.asarray(c["samples"][0]["T_cw"], float)
    h = K @ (T[:3, :3] @ X + T[:3, 3])
    return float(h[0] / h[2]), float(h[1] / h[2])


CANCEL_CHECK_FRAMES = 12  # a cancel request interrupts a frame loop within this many frames


def frame_checkpoint(run: JobRun, i: int, beat_every: int = 10) -> None:
    """Per-frame bookkeeping of a long loop: heartbeat every ``beat_every`` frames and honour a cancel request
    every ``CANCEL_CHECK_FRAMES`` frames (raises ``Cancelled``; no output asset of the stage is stored then, and
    the job's work dir with the partial files is removed when the job finishes as ``cancelled``)."""
    if i % beat_every == 0:
        run.heartbeat()
    if i % CANCEL_CHECK_FRAMES == 0:
        run.check_cancel()


def with_heartbeat(run: JobRun, frames, every: int = 10):
    """Yield ``frames`` with ``frame_checkpoint`` (heartbeat + cancel check) before each frame."""
    for i, f in enumerate(frames):
        frame_checkpoint(run, i, every)
        yield f
