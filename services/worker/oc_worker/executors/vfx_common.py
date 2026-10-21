"""Shared helpers of the Pocket VFX executors (pinned config, work-clip lineage, derived assets, JSON assets)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from oc_api.models import Asset
from oc_api.pocket_vfx.config import OUTPUT_SIZES, PocketVfxConfig
from oc_api.pocket_vfx.jobs import analysis_matches
from open_creative.media.video import decode_frames
from open_creative.pocket_vfx.timeline import frame_count
from sqlalchemy import select

from ..stages import JobRun, StageFailed
from .camera_common import ready_asset

MAX_TRACK_FRAMES = 120  # 5 s @ 24 fps: the proxy gray frames held in memory (<= 120 x 640x360 bytes)


def pinned_config(run: JobRun) -> tuple[PocketVfxConfig, dict]:
    """The pinned revision's config and its JSON dump (the form the API hashes and compares)."""
    cfg = run.config()
    return cfg, cfg.model_dump(mode="json")


def canvas(cfg: PocketVfxConfig) -> tuple[int, int, int]:
    """(width, height, frames) of the work clip: 1280x720 / 720x1280 and ``frame_index(duration_ms)`` frames."""
    w, h = OUTPUT_SIZES[cfg.output.aspect]
    return w, h, frame_count(cfg.trim.duration_ms)


def fresh_work_clip(run: JobRun, cfg: PocketVfxConfig, cfg_json: dict) -> Asset:
    """The config's work clip; it must be the analysis of the config's clip, trim and aspect."""
    work = ready_asset(run, cfg.work_asset_id, "work_clip", "analysis_required")
    if not analysis_matches(work, cfg_json):
        raise StageFailed("analysis_stale", "The clip, trim or aspect changed after the clip was prepared. "
                          "Prepare it again.")
    return work


def derived_asset(run: JobRun, parent: Asset, role: str, missing_code: str | None = None) -> Asset | None:
    """The newest ready asset of ``role`` derived from ``parent`` (``source_asset_id``)."""
    a = run.db.scalar(select(Asset).where(Asset.project_id == run.project.id, Asset.source_asset_id == parent.id,
                                          Asset.role == role, Asset.state == "ready")
                      .order_by(Asset.created_at.desc()).limit(1))
    if a is None and missing_code is not None:
        raise StageFailed(missing_code, f"The prepared clip has no {role.replace('_', ' ')}. Prepare it again.")
    return a


def gray_frames(path: Path, limit: int = MAX_TRACK_FRAMES) -> list[np.ndarray]:
    """Decode a (proxy) clip once into gray uint8 frames; at most ``limit`` frames are kept."""
    frames = []
    for f in decode_frames(path):
        if len(frames) == limit:
            break
        frames.append(cv2.cvtColor(f, cv2.COLOR_RGB2GRAY))
    return frames


def gray_frames_at(path: Path, indices: list[int]) -> list[np.ndarray]:
    """Gray uint8 frames ``indices`` (ascending) of a clip, decoded in one pass keeping only those frames."""
    wanted, frames = set(indices), {}
    for i, f in enumerate(decode_frames(path)):
        if i in wanted:
            frames[i] = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY)
        if len(frames) == len(wanted):
            break
    if len(frames) != len(wanted):
        raise StageFailed("frame_count_mismatch", f"The clip has fewer frames than expected ({len(frames)} of "
                          f"{len(wanted)} sampled frames decoded).")
    return [frames[i] for i in indices]


def store_json(run: JobRun, role: str, data: dict, *, meta: dict | None = None, source_asset_id=None) -> str:
    path = run.work_dir / f"{role}.json"
    path.write_text(json.dumps(data, separators=(",", ":")))
    return run.store_asset(role, path, "application/json", meta=meta, source_asset_id=source_asset_id)
