"""vfx.render: tracked procedural effect composited over the work clip -> H.264 MP4 (render profile "Quick").

Stages (each stores durable assets; a restarted job resumes after the last finished stage):
- ``layers``: per-frame effect anchor in output px. ``follow=false`` -> the config anchor (uv x canvas) on every
  frame; ``follow=true`` -> the track job's points (uv01 x canvas). Invalid track frames follow the preset's
  ``invalid_policy``: ``hide`` draws no effect that frame, ``freeze`` holds the last valid position. Plate and subject
  masks are resolved here too; neither exists yet (plates: Task 12, masks: Task 10), so the effect is procedural only
  and is composited without occlusion (Ruling R2).
- ``composite``: streams the work-clip frames (one in flight) through ``composite_frame`` into ``Mp4Writer`` and an
  ``IntegrityAccumulator``; per-frame ROI size/box and changed pixel count (pre-encode, vs the source frame) are kept.
  An outside-ROI integrity failure fails the job (``roi_integrity``, Ruling R13): no video is stored.
- ``encode``: final video (audio mux plugs in here, Task 13), output contract probe, ``cover`` PNG of the peak frame
  and the ``roi_qa`` JSON.
"""

from __future__ import annotations

import numpy as np
from oc_api.pocket_vfx.jobs import config_track_key
from open_creative.media.video import Mp4Writer, decode_frames, probe
from open_creative.pocket_vfx.composite import FrameInputs, composite_frame
from open_creative.pocket_vfx.integrity import IntegrityAccumulator
from open_creative.pocket_vfx.presets import load_presets
from open_creative.pocket_vfx.procedural import render_effect
from open_creative.pocket_vfx.timeline import EffectWindow, effect_window
from PIL import Image

from ..stages import JobRun, StageFailed
from .camera_common import json_asset, ready_asset, source_job, with_heartbeat
from .vfx_common import (
    canvas,
    derived_asset,
    fresh_work_clip,
    pinned_config,
    store_json,
)

RENDER_PROFILE_LABEL = "Quick"
EFFECT_PARAMS = ("scale", "intensity", "palette", "rotation_deg", "squash")


def resolve_plate(run: JobRun, cfg, w: int, h: int) -> dict | None:
    """The effect plate placed on the canvas, or None (procedural only). Plates arrive with Task 12."""
    return None


def plate_layer(plate: dict | None, frame: int):
    """The plate's ``EffectLayer`` at ``frame`` (retimed into the effect window); None without a plate."""


def final_video(run: JobRun, cfg, video_id: str) -> tuple[str, bool]:
    """``(asset id, has_audio)`` of the deliverable MP4. Audio mux (original audio / SFX, Task 13) plugs in here and
    stores a new muxed ``render_video``; until then the composite video is the deliverable, without audio."""
    return video_id, False


def _anchors(track: dict | None, cfg, w: int, h: int, n: int, policy: str) -> list[list[float] | None]:
    if track is None:  # follow=false: the config anchor on every frame
        return [[cfg.anchor.x * w, cfg.anchor.y * h]] * n
    points = {p["frame"]: p for p in track["points"]}
    out, last = [], None
    for i in range(n):
        p = points.get(i)
        if p is not None and p["valid"]:
            last = [p["x"] * w, p["y"] * h]
            out.append(last)
        else:
            out.append(last if policy == "freeze" else None)
    return out


def _runs(frames: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for f in frames:
        if out and out[-1][1] == f - 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return out


def _tracking_warnings(track: dict | None, window: EffectWindow, policy: str, motion: dict | None,
                       preset: dict) -> list[dict]:
    warnings = []
    if track is not None:
        lost = [p["frame"] for p in track["points"] if not p["valid"] and window.start <= p["frame"] <= window.end]
        if lost:
            warnings.append({"code": "track_lost", "frames": _runs(lost), "policy": policy})
        if any(p["frame"] == window.peak and not p["valid"] for p in track["points"]):
            warnings.append({"code": "track_invalid_at_peak", "frame": window.peak})
    status = (motion or {}).get("status")
    if preset["input_requirements"].get("fixed_camera") and status != "fixed":
        warnings.append({"code": "camera_not_fixed" if status in ("handheld", "moving") else "camera_motion_unknown",
                         "status": status})
    return warnings


def vfx_render(run: JobRun) -> tuple[list[str], dict]:
    cfg, cfg_json = pinned_config(run)
    opts = run.job.options_json or {}
    profile = opts.get("render_profile") or cfg.render_profile  # the option wins over the config
    if profile != "quick":
        raise StageFailed("plate_required", "Generated plate renders are not available yet.")
    work = fresh_work_clip(run, cfg, cfg_json)
    w, h, n = canvas(cfg)
    preset = load_presets()[cfg.effect.preset_id]
    policy = preset.get("invalid_policy", "hide")
    window = effect_window(cfg_json["effect"], cfg.trim.duration_ms)
    if cfg.effect.preset_id == "blink_out_v1":
        raise StageFailed("mask_required", "Blink Out needs subject masks; track the subject first.")

    def layers_stage(stage):
        run.transition("running", "layers")
        track, track_job = None, None
        if cfg.anchor.follow:
            track_job = source_job(run, opts.get("track_job_id"), ("vfx.track",), "track_required",
                                   "Track the anchor before rendering a following effect.")
            clip = ready_asset(run, cfg.video_asset_id, "clip", "video_required")
            if (track_job.result_json or {}).get("track_key") != config_track_key(cfg, clip):
                raise StageFailed("track_stale", "The clip, anchor, corrections or subject prompts changed after "
                                  "tracking. Track again.")
            track = json_asset(run, track_job.result_json["track_asset_id"])
        motion_asset = derived_asset(run, work, "motion_report")
        motion = json_asset(run, motion_asset.id) if motion_asset else None
        plate = resolve_plate(run, cfg, w, h)
        doc = {"schema_version": 1, "canvas": [w, h], "frame_count": n, "space": "output_px",
               "window": {"start": window.start, "peak": window.peak, "end": window.end},
               "follow": cfg.anchor.follow, "invalid_policy": policy,
               "track_job_id": str(track_job.id) if track_job else None,
               "anchors": _anchors(track, cfg, w, h, n, policy), "plate": plate, "subject_masks": None,
               "tracking_warnings": _tracking_warnings(track, window, policy, motion, preset)}
        return [store_json(run, "render_layers", doc, source_asset_id=work.id,
                           meta={"revision": run.job.input_revision})]

    (layers_id,) = run.ensure_stage("layers", layers_stage)

    def composite_stage(stage):
        run.transition("running", "composite")
        plan = json_asset(run, layers_id)
        anchors = plan["anchors"]
        params = {k: cfg_json["effect"][k] for k in EFFECT_PARAMS}
        acc = IntegrityAccumulator()
        roi_px, roi_bbox, changed_px = [], [], []
        out_path = run.work_dir / "render.mp4"
        src = run.fetch(work, "work.mp4")
        with Mp4Writer(out_path, w, h, fps=24) as writer:
            for i, frame in enumerate(with_heartbeat(run, decode_frames(src), 5)):
                if i >= n:
                    break
                anchor = anchors[i]
                effect = None
                if anchor is not None:
                    effect = render_effect(cfg.effect.preset_id, params, window, i, tuple(anchor), w, h,
                                           cfg.plate.seed, None)
                inp = FrameInputs(source=frame, effect=effect, plate=plate_layer(plan["plate"], i),
                                  subject_mask=None, front_glow=None, roi_limit=None)
                out, roi = composite_frame(inp, cfg.effect.layer_order)
                writer.write(out)
                acc.add(frame, out, roi)
                roi_px.append(int(np.count_nonzero(roi)))
                ys, xs = np.nonzero(roi)
                roi_bbox.append([int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
                                if len(xs) else None)
                changed_px.append(int(np.count_nonzero((out != frame).any(axis=2))))
        if len(roi_px) != n:
            raise StageFailed("frame_count_mismatch", f"Composited {len(roi_px)} frames, expected {n}.")
        integrity = acc.result()
        if integrity["status"] != "pass":  # Ruling R13: never deliver a render that changed pixels outside the ROI
            raise StageFailed("roi_integrity", f"The composite changed pixels outside the effect area (max error "
                              f"{integrity['max_err']}/255 at frame {integrity['worst_frame']}).")
        meta = {"revision": run.job.input_revision, "render_profile": profile, "label": RENDER_PROFILE_LABEL,
                "plate_source": "procedural", "preset_id": cfg.effect.preset_id,
                "layer_order": cfg.effect.layer_order, "occlusion": False, "has_audio": False, "mock": False,
                "integrity": integrity,
                "frame_stats": {"roi_px": roi_px, "roi_bbox": roi_bbox, "changed_px": changed_px}}
        return [run.store_asset("render_video", out_path, "video/mp4", source_asset_id=work.id, meta=meta)]

    (composite_id,) = run.ensure_stage("composite", composite_stage)

    def encode_stage(stage):
        run.transition("postprocessing", "encode")
        video_id, has_audio = final_video(run, cfg, composite_id)
        video = run.asset(video_id)
        path = run.fetch(video, "render.mp4")
        info = probe(path)
        if (info["width"], info["height"], info["frame_count"]) != (w, h, n) or info["pix_fmt"] != "yuv420p":
            raise StageFailed("encode_mismatch", "The encoded render does not match the output contract.")
        cover = run.work_dir / "cover.png"
        for i, f in enumerate(decode_frames(path)):
            if i == window.peak:
                Image.fromarray(f, "RGB").save(cover)
                break
        meta = run.asset(composite_id).meta_json
        qa = {"schema_version": 1, "integrity": meta["integrity"], "frame_stats": meta["frame_stats"],
              "layer_order": meta["layer_order"], "occlusion_qa": None, "max_outside_err_allowed": 1}
        return [video_id, run.store_asset("cover", cover, "image/png", source_asset_id=video_id,
                                          meta={"frame": window.peak, "revision": run.job.input_revision}),
                store_json(run, "roi_qa", qa, source_asset_id=video_id,
                           meta={"status": meta["integrity"]["status"], "has_audio": has_audio})]

    video_id, cover_id, qa_id = run.ensure_stage("encode", encode_stage)
    plan, meta = json_asset(run, layers_id), run.asset(composite_id).meta_json
    return [layers_id, video_id, cover_id, qa_id], {
        "render_profile": profile, "label": RENDER_PROFILE_LABEL, "roi_qa": meta["integrity"],
        "occlusion_qa": None, "tracking_warnings": plan["tracking_warnings"], "plate_source": meta["plate_source"],
        "frame_count": n, "mock": meta["mock"], "has_audio": bool(run.asset(qa_id).meta_json["has_audio"]),
        "render_video_asset_id": video_id, "cover_asset_id": cover_id, "roi_qa_asset_id": qa_id,
        "layers_asset_id": layers_id, "track_job_id": plan["track_job_id"], "revision": run.job.input_revision,
        "width": w, "height": h,
    }
