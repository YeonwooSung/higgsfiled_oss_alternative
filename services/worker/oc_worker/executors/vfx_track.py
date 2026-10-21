"""vfx.track, stage ``track``: LK point track of the anchor on the work proxy (spec 6.2), with segment reuse.

The proxy is decoded once into gray frames (<= 120 x 640x360 bytes). The track is split into segments between
keyframes (anchor + corrections, ``plan_segments``); each segment's points are cached in a ``track_segments``
sidecar asset keyed by ``segment_keys`` (which hash the bounding keyframes, the frame range, the params and the
proxy's sha256). A re-track loads the cache of the most recent succeeded ``vfx.track`` job of the project and only
recomputes segments whose key is new, so one correction re-tracks at most the two segments next to it.

``follow=false`` stores a static track (the anchor on every frame, all valid): the render does not move the effect.

Stage ``masks`` (``vfx_masks.masks_stage``, GPU) runs when the preset uses a subject mask and a subject is selected
(``masks_stage_runs``); it is keyed by ``masks_key`` and reuses an earlier track job's masks for the same key.
"""

from __future__ import annotations

import math

from oc_api.models import Asset, Job
from oc_api.pocket_vfx.jobs import masks_stage_runs, source_sha
from open_creative.pocket_vfx.keys import masks_key, model_rev, track_key
from open_creative.pocket_vfx.presets import load_presets
from open_creative.pocket_vfx.timeline import effect_window
from open_creative.pocket_vfx.track import (
    SMOOTH_MAX_SHIFT_PX,
    SMOOTH_SIGMA_FRAMES,
    TrackPoint,
    assemble,
    plan_segments,
    segment_keys,
    smooth_track,
    static_track,
    track_segment,
    track_to_json,
)
from sqlalchemy import select

from ..stages import JobRun, StageFailed
from .camera_common import json_asset, ready_asset
from .vfx_common import (
    canvas,
    derived_asset,
    fresh_work_clip,
    gray_frames,
    pinned_config,
    store_json,
)
from .vfx_masks import masks_stage


def _point_dict(p: TrackPoint) -> dict:
    def num(v):
        return None if v is None or math.isnan(v) else v

    return {"frame": p.frame, "x": num(p.x), "y": num(p.y), "valid": p.valid, "confidence": p.confidence,
            "fb_error": num(p.fb_error), "n_features": p.n_features, "source": p.source}


def _point(d: dict) -> TrackPoint:
    def num(v):
        return math.nan if v is None else float(v)

    return TrackPoint(int(d["frame"]), num(d["x"]), num(d["y"]), bool(d["valid"]), float(d["confidence"]),
                      num(d["fb_error"]), int(d["n_features"]), d["source"])


def _previous_segments(run: JobRun) -> dict[str, list[TrackPoint]]:
    """Segment cache of the most recent succeeded vfx.track job of this project (empty if none)."""
    prev = run.db.scalar(select(Job).where(Job.project_id == run.project.id, Job.kind == "vfx.track",
                                           Job.state == "succeeded", Job.id != run.job.id)
                         .order_by(Job.finished_at.desc()).limit(1))
    if prev is None:
        return {}
    side = run.db.scalar(select(Asset).where(Asset.job_id == prev.id, Asset.role == "track_segments",
                                             Asset.state == "ready").limit(1))
    if side is None:
        return {}
    return {k: [_point(d) for d in pts] for k, pts in json_asset(run, side.id)["segments"].items()}


def vfx_track(run: JobRun) -> tuple[list[str], dict]:
    cfg, cfg_json = pinned_config(run)
    clip = ready_asset(run, cfg.video_asset_id, "clip", "video_required")
    work = fresh_work_clip(run, cfg, cfg_json)
    proxy = derived_asset(run, work, "work_proxy", "analysis_required")
    sha = source_sha(clip)
    if not sha:
        raise StageFailed("asset_not_ready", "The clip has not passed inspection yet.")
    key = track_key(cfg_json, sha, model_rev(run.job.model_profile))
    out_w, out_h, n = canvas(cfg)
    anchor, corrections = cfg_json["anchor"], cfg_json["corrections"]
    if anchor["frame"] >= n:
        raise StageFailed("anchor_range", f"The anchor frame must be below {n}.")

    def track_stage(stage):
        run.transition("running", "track")
        frames = gray_frames(run.fetch(proxy, "proxy.mp4"))
        if len(frames) != n:
            raise StageFailed("frame_count_mismatch", f"The proxy has {len(frames)} frames, expected {n}.")
        ph, pw = frames[0].shape[:2]
        reused, cache = 0, {}
        if not anchor["follow"]:
            points = static_track((anchor["x"] * pw, anchor["y"] * ph), n)
            segs = []
        else:
            window = anchor["track_window_frames"]
            segs = plan_segments(anchor, corrections, n, window)
            keys = segment_keys(proxy.sha256, anchor, corrections, window, n_frames=n)
            previous = _previous_segments(run)
            seg_points = []
            for i, (seg, k) in enumerate(zip(segs, keys, strict=True)):
                run.heartbeat()
                if i % 2 == 0:
                    run.check_cancel()
                if k in previous:
                    pts, reused = previous[k], reused + 1
                else:
                    pts = track_segment(frames, seg)
                seg_points.append(pts)
                cache[k] = [_point_dict(p) for p in pts]
            points = smooth_track(assemble(segs, seg_points, n, (pw, ph)), SMOOTH_SIGMA_FRAMES,
                                  SMOOTH_MAX_SHIFT_PX)
        doc = track_to_json(points, (pw, ph), (out_w, out_h))
        valid = sum(p.valid for p in points)
        peak = effect_window(cfg_json["effect"], cfg.trim.duration_ms).peak
        stats = {"track_key": key, "invalid_intervals": doc["invalid_intervals"],
                 "valid_fraction": round(valid / n, 4), "peak_valid": bool(points[peak].valid), "peak_frame": peak,
                 "segments_total": len(segs), "segments_reused": reused, "follow": bool(anchor["follow"])}
        doc.update(track_key=key, revision=run.job.input_revision,
                   smoothing={"sigma_frames": SMOOTH_SIGMA_FRAMES, "max_shift_proxy_px": SMOOTH_MAX_SHIFT_PX})
        track_id = store_json(run, "track_json", doc, source_asset_id=proxy.id,
                              meta={**stats, "revision": run.job.input_revision})
        side_id = store_json(run, "track_segments", {"schema_version": 1, "frames_sha": proxy.sha256,
                                                     "segments": cache},
                             source_asset_id=proxy.id, meta={"track_key": key, "segments": len(cache)})
        return [track_id, side_id]

    track_id, side_id = run.ensure_stage("track", track_stage)
    outputs, masks = [track_id, side_id], None
    if masks_stage_runs(cfg, load_presets()[cfg.effect.preset_id]):
        mkey = masks_key(cfg_json, sha, model_rev(run.job.model_profile))
        mask_ids, masks = masks_stage(run, cfg, cfg_json, key=mkey, sha=sha, work=work, proxy=proxy, n=n)
        outputs += mask_ids
    stats = run.asset(track_id).meta_json
    return outputs, {
        **{k: stats[k] for k in ("track_key", "invalid_intervals", "valid_fraction", "peak_valid", "peak_frame",
                                 "segments_total", "segments_reused", "follow")},
        "track_asset_id": track_id, "track_segments_asset_id": side_id, "work_asset_id": str(work.id),
        "masks": masks, "revision": run.job.input_revision,
    }
