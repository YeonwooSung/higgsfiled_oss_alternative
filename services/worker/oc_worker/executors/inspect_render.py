"""product.inspect: side-by-side 200% crops of the protected core (reference vs. encoded frames)."""

from __future__ import annotations

import uuid

import cv2
import numpy as np

from oc_api.apps_registry import validate_config
from oc_api.models import Job, ProjectRevision
from open_creative.media.image import to_png_bytes
from open_creative.media.video import decode_frames
from open_creative.product_lock.geometry import output_size
from open_creative.product_lock.locked import expected_reference

from ..stages import JobRun, StageFailed
from .product_inputs import load_locked

ZOOM = 2


def _crop_box(core: np.ndarray) -> tuple[slice, slice]:
    ys, xs = np.nonzero(core)
    if ys.size == 0:
        raise StageFailed("invalid_core", "The protected core is empty.")
    return slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1)


def inspect_render(run: JobRun) -> tuple[list[str], dict]:
    render_job = run.db.get(Job, uuid.UUID(run.job.options_json["render_job_id"]))
    if render_job is None or render_job.project_id != run.project.id or render_job.state != "succeeded":
        raise StageFailed("render_not_found", "Render job not found.")
    rev = run.db.get(ProjectRevision, (run.project.id, render_job.input_revision))
    cfg = validate_config(run.project.app_slug, rev.config_json)

    def produce(stage):
        locked = load_locked(run, cfg)
        ref, core = expected_reference(locked)
        w, h = output_size(cfg.output.aspect)
        fy, fx, py, px = locked.placement.visible(w, h)
        ref_v, core_v = ref[py, px], core[py, px]
        ys, xs = _crop_box(core_v)
        mp4 = run.fetch(run.asset(render_job.output_asset_ids[0]), "render.mp4")
        frames = list(decode_frames(mp4))
        picks = [frames[0], frames[len(frames) // 2], frames[-1]]
        tiles = [ref_v[ys, xs]] + [f[fy, fx][ys, xs] for f in picks]
        errors = [float(np.abs(t.astype(np.int16) - tiles[0].astype(np.int16))[core_v[ys, xs]].mean())
                  for t in tiles[1:]]
        zoomed = [cv2.resize(t, None, fx=ZOOM, fy=ZOOM, interpolation=cv2.INTER_NEAREST) for t in tiles]
        gap = np.full((zoomed[0].shape[0], 8, 3), 255, np.uint8)
        sheet = np.hstack([x for t in zoomed for x in (t, gap)][:-1])
        path = run.work_dir / "inspection.png"
        path.write_bytes(to_png_bytes(sheet))
        aid = run.store_asset("inspection", path, "image/png", source_asset_id=render_job.output_asset_ids[0],
                              meta={"tiles": ["reference", "frame_first", "frame_middle", "frame_last"],
                                    "zoom": ZOOM, "mean_abs_error": errors})
        return [aid]

    outputs = run.ensure_stage("inspect", produce)
    meta = run.asset(outputs[0]).meta_json
    return outputs, {"revision": render_job.input_revision, "render_job_id": str(render_job.id), **meta}
