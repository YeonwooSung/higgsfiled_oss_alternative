"""product.render: background video (generated or reused) → deterministic composite → core QA → MP4."""

from __future__ import annotations

import json
from collections.abc import Iterator

import numpy as np

from open_creative.media.image import to_png_bytes
from open_creative.media.video import decode_frames, fit_sequence, probe
from open_creative.product_lock.geometry import output_size
from open_creative.product_lock.integrity import postencode_metrics
from open_creative.product_lock.pipeline import render_to_mp4

from ..stages import JobRun
from .background_video import generate_background_video
from .product_inputs import layer_config, load_locked

FRAMES = 120
FPS = 24


def _with_heartbeat(run: JobRun, frames: Iterator[np.ndarray]) -> Iterator[np.ndarray]:
    for i, f in enumerate(frames):
        if i % 10 == 0:
            run.heartbeat()
        yield f


def output_check(info: dict, w: int, h: int) -> dict:
    expected = {"width": w, "height": h, "frame_count": FRAMES, "fps": FPS, "codec": "h264", "pix_fmt": "yuv420p"}
    mismatches = {k: info.get(k) for k, v in expected.items() if info.get(k) != v}
    return {"ok": not mismatches, "expected": expected, "mismatches": mismatches}


def render(run: JobRun) -> tuple[list[str], dict]:
    cfg = run.config()
    opts = run.job.options_json
    video_id = opts.get("reuse_background_video_asset_id") or generate_background_video(
        run, cfg, cfg.scene.background_asset_id, cfg.seed, "background_video")

    def produce(stage):
        run.transition("postprocessing", "composite")
        locked = load_locked(run, cfg)
        w, h = output_size(cfg.output.aspect)
        video_path = run.fetch(run.asset(video_id), "background.mp4")
        frames = _with_heartbeat(run, fit_sequence(decode_frames(video_path), w, h, FRAMES))
        mp4 = run.work_dir / "ad.mp4"
        result = render_to_mp4(frames, locked, layer_config(cfg), mp4, w, h, fps=FPS)
        run.heartbeat(force=True)
        check = output_check(probe(mp4), w, h)
        post = postencode_metrics(decode_frames(mp4), locked, w, h)
        cover = run.work_dir / "cover.png"
        cover.write_bytes(to_png_bytes(result.cover))
        render_id = run.store_asset("render", mp4, "video/mp4", source_asset_id=video_id,
                                    meta={"revision": run.job.input_revision})
        cover_id = run.store_asset("cover", cover, "image/png", source_asset_id=render_id)
        report = {**result.report, "postencode_metrics": post, "output_check": check,
                  "revision": run.job.input_revision, "aspect": cfg.output.aspect,
                  "transform": cfg.transform.model_dump(), "placement": locked.placement.__dict__,
                  "preview_asset_ids": [cover_id]}
        report_path = run.work_dir / "integrity.json"
        report_path.write_text(json.dumps(report, indent=2))
        integrity_id = run.store_asset("integrity", report_path, "application/json", source_asset_id=render_id)
        return [render_id, cover_id, integrity_id]

    outputs = run.ensure_stage("composite", produce)
    integrity = json.loads(run.ctx.storage.get_bytes(run.asset(outputs[2]).object_key))
    return outputs, {"integrity": integrity, "background_video_asset_id": str(video_id),
                     "revision": run.job.input_revision}
