"""vfx.analyze: uploaded clip -> normalized work clip (rotation applied, CFR 24 fps, cover-fit to the output canvas,
trimmed), its 640 px proxy, filmstrip + first frame, trimmed source audio (only when the clip has audio) and a
camera motion report. Every stage stores durable assets, so a restarted job resumes after the last finished stage.

The work clip's ``meta_json.trim``/``aspect`` are what the API's ``analysis_matches`` compares by equality with the
config, so they are written exactly as ``{"source_start_ms", "duration_ms"}`` and the literal aspect string.
"""

from __future__ import annotations

from open_creative.media.image import MediaError
from open_creative.media.video_ingest import (
    SourceInfo,
    cover_transform,
    extract_audio,
    filmstrip,
    first_frame_png,
    make_proxy,
    normalize_clip,
)
from open_creative.pocket_vfx.motion import camera_motion

from ..stages import JobRun, StageFailed
from .camera_common import frame_checkpoint, json_asset, ready_asset
from .vfx_common import canvas, gray_frames, pinned_config, store_json

FILMSTRIP_THUMBS = 12


def vfx_analyze(run: JobRun) -> tuple[list[str], dict]:
    cfg, _ = pinned_config(run)
    clip = ready_asset(run, cfg.video_asset_id, "clip", "video_required")
    source = (clip.meta_json or {}).get("source")
    if not source:
        raise StageFailed("asset_not_ready", "The clip has not passed inspection yet.")
    info = SourceInfo(**source)
    out_w, out_h, n = canvas(cfg)
    trim = {"source_start_ms": cfg.trim.source_start_ms, "duration_ms": cfg.trim.duration_ms}
    if trim["source_start_ms"] + trim["duration_ms"] > info.duration_ms:
        raise StageFailed("trim_range", "The trim runs past the end of the clip.")

    def normalize(stage):
        run.transition("running", "normalize")
        src = run.fetch(clip)
        run.heartbeat(force=True)
        out = run.work_dir / "work_clip.mp4"
        try:
            pts = normalize_clip(src, out, info, trim["source_start_ms"], trim["duration_ms"], out_w, out_h,
                                 on_frame=lambda i: frame_checkpoint(run, i))
        except MediaError as e:
            raise StageFailed(e.code, "The clip could not be decoded.") from e
        transform = {"kind": "cover", "src_w": info.display_w, "src_h": info.display_h, "out_w": out_w,
                     "out_h": out_h, "rotation": info.rotation,
                     **cover_transform(info.display_w, info.display_h, out_w, out_h)}
        meta = {"source_asset_id": str(clip.id), "trim": trim, "aspect": cfg.output.aspect, "fps": 24,
                "frame_count": n, "pts_map": [round(t, 3) for t in pts.source_pts_ms], "mock": False}
        return [run.store_asset("work_clip", out, "video/mp4", meta=meta, source_asset_id=clip.id,
                                transform=transform)]

    (work_id,) = run.ensure_stage("normalize", normalize)
    work = run.asset(work_id)
    if work.frame_count != n:
        raise StageFailed("frame_count_mismatch", f"The work clip has {work.frame_count} frames, expected {n}.")

    def proxy(stage):
        run.transition("running", "proxy")
        out = run.work_dir / "work_proxy.mp4"
        make_proxy(run.fetch(work, "work.mp4"), out)
        return [run.store_asset("work_proxy", out, "video/mp4", source_asset_id=work.id,
                                meta={"scale_from_work": 640 / max(out_w, out_h), "mock": False})]

    (proxy_id,) = run.ensure_stage("proxy", proxy)

    def strip(stage):
        run.transition("running", "filmstrip")
        src = run.fetch(work, "work.mp4")
        strip_png, first_png = run.work_dir / "filmstrip.png", run.work_dir / "first_frame.png"
        indices = filmstrip(src, strip_png, FILMSTRIP_THUMBS)
        first_frame_png(src, first_png)
        return [run.store_asset("filmstrip", strip_png, "image/png", source_asset_id=work.id,
                                meta={"frame_indices": indices}),
                run.store_asset("first_frame", first_png, "image/png", source_asset_id=work.id, meta={"frame": 0})]

    strip_id, first_id = run.ensure_stage("filmstrip", strip)

    def audio(stage):
        run.transition("running", "audio")
        out = run.work_dir / "source_audio.m4a"
        if not extract_audio(run.fetch(clip), out, info, trim["source_start_ms"], trim["duration_ms"]):
            return []
        return [run.store_asset("source_audio", out, "audio/mp4", source_asset_id=work.id,
                                meta={"trim": trim, "sample_rate": 48_000, "codec": "aac"})]

    audio_ids = run.ensure_stage("audio", audio)

    def motion(stage):
        run.transition("running", "motion")
        frames = gray_frames(run.fetch(run.asset(proxy_id), "proxy.mp4"))
        report = {**camera_motion(frames), "frames": len(frames), "space": "proxy_px"}
        return [store_json(run, "motion_report", report, source_asset_id=work.id, meta={"status": report["status"]})]

    (motion_id,) = run.ensure_stage("motion", motion)
    report = json_asset(run, motion_id)
    outputs = [work_id, proxy_id, strip_id, first_id, *audio_ids, motion_id]
    return outputs, {
        "work_asset_id": work_id, "proxy_asset_id": proxy_id, "filmstrip_asset_id": strip_id,
        "first_frame_asset_id": first_id, "audio_asset_id": audio_ids[0] if audio_ids else None,
        "motion_asset_id": motion_id, "frame_count": n, "has_audio": bool(audio_ids),
        "camera_motion": {k: report[k] for k in ("status", "median_shift_px", "max_shift_px")},
        "filmstrip_frames": run.asset(strip_id).meta_json["frame_indices"],
        "trim": trim, "aspect": cfg.output.aspect, "revision": run.job.input_revision,
    }

