"""vfx.track, stage ``masks``: subject mask sequence of the proxy work clip through ``vfx_masks_v1`` (SAM 2.1 video;
GPU, mock profile: fixture GT / GrabCut), spec §6.3.

Reuse: the stage is keyed by ``masks_key`` (the track key without point corrections). When a previous succeeded
``vfx.track`` of the project carries masks for the same key, its ``masks_zip`` / ``mask_preview`` assets are reused
and nothing is submitted to the provider (a correction-only re-track never re-runs SAM).

Otherwise the bindings are built only when a submission is still needed (a resumed stage inspects its provider job):
``frames`` = the proxy MP4, ``prompts`` = ``masks.build_prompts`` (subject prompts in proxy px + mask edits as mask
prompt frames; under the mock profile also ``mock_context`` with the clip sha256 and the work frames' source PTS),
``width``/``height`` = the proxy size. The provider ZIP is re-read and checked (N frames, proxy size), the mask edits
are written over their frames (``apply_mask_edits``), and the stage stores ``masks_zip`` (8-bit PNGs + manifest) and
``mask_preview`` (gray proxy MP4 for the browser overlay); the diagnostics travel in the zip asset's meta.
"""

from __future__ import annotations

import json
import uuid

import numpy as np
from oc_api.models import Asset, Job
from oc_api.pocket_vfx.jobs import track_masks
from open_creative.comfy.profiles import get_profile
from open_creative.media.video import encode_mp4
from open_creative.pocket_vfx.masks import (
    MaskFormatError,
    apply_mask_edits,
    build_prompts,
    edit_to_proxy,
    mask_diagnostics,
    mask_stats,
    read_masks_zip,
    write_masks_zip,
)
from PIL import Image
from sqlalchemy import select

from ..stages import JobRun, StageFailed
from .camera_common import ready_asset

WORKFLOW = "vfx_masks_v1"
RESULT_FIELDS = ("masks_key", "size", "frame_count", "diagnostics", "model", "mock", "source", "prompts_hash")


def _reusable(run: JobRun, key: str) -> dict | None:
    """``masks`` result of the newest other succeeded vfx.track of this project made for masks key ``key``."""
    jobs = run.db.scalars(select(Job).where(Job.project_id == run.project.id, Job.kind == "vfx.track",
                                            Job.state == "succeeded", Job.id != run.job.id)
                          .order_by(Job.finished_at.desc()).limit(50))
    for j in jobs:
        m = track_masks(j, key)
        if m is None:
            continue
        ids = (m.get("masks_zip_asset_id"), m.get("mask_preview_asset_id"))
        ready = [run.db.get(Asset, uuid.UUID(str(a))) if a else None for a in ids]
        if all(a is not None and a.state == "ready" and a.project_id == run.project.id for a in ready):
            return m
    return None


def _edits(run: JobRun, cfg, pw: int, ph: int) -> dict[int, np.ndarray]:
    """``config.mask_edits`` (role ``mask_edit`` PNGs of any size) as proxy-size masks; a later edit of the same
    frame wins."""
    out: dict[int, np.ndarray] = {}
    for e in cfg.mask_edits:
        asset = ready_asset(run, e.asset_id, "mask_edit", "mask_edit_missing")
        with Image.open(run.fetch(asset, f"mask_edit_{e.frame}.png")) as im:
            out[e.frame] = edit_to_proxy(np.asarray(im.convert("L")), pw, ph)
    return out


def masks_stage(run: JobRun, cfg, cfg_json: dict, *, key: str, sha: str, work: Asset, proxy: Asset,
                n: int) -> tuple[list[str], dict]:
    """Run (or reuse) the masks stage; returns ``(asset ids, masks result)``."""
    pw, ph = proxy.width, proxy.height

    def produce(st):
        run.transition("running", "masks")
        prev = _reusable(run, key)
        if prev is not None:
            return [prev["masks_zip_asset_id"], prev["mask_preview_asset_id"]]
        edits = _edits(run, cfg, pw, ph)
        bindings = {}
        if st.provider_job_id is None:
            mock = get_profile(run.job.model_profile).is_mock
            context = ({"source_sha256": sha, "source_pts_ms": (work.meta_json or {}).get("pts_map") or []}
                       if mock else None)
            bindings = {"frames": str(run.fetch(proxy, "proxy.mp4")), "width": pw, "height": ph,
                        "prompts": build_prompts(cfg_json["subject_prompts"], edits, pw, ph, n,
                                                 mock_context=context)}
        files = run.provider_stage(st, WORKFLOW, bindings)
        zp = next((p for p in files if p.suffix == ".zip"), None)
        if zp is None:
            raise StageFailed("provider_failed", "The mask service returned no masks.")
        side = next((p for p in files if p.suffix == ".json"), None)
        provider = json.loads(side.read_text()) if side else {}
        run.transition("postprocessing", "masks")
        try:
            seq = read_masks_zip(zp, expect_frames=n, expect_size=(pw, ph))
        except (MaskFormatError, OSError, ValueError) as e:
            raise StageFailed("masks_mismatch", f"The returned masks do not match the work clip: {e}") from e
        seq = apply_mask_edits(seq, edits)
        stats = [mask_stats(m) for m in seq.masks]
        meta = {"masks_key": key, "size": [pw, ph], "frame_count": n,
                "diagnostics": mask_diagnostics(seq, stats=stats), "model": seq.model or provider.get("model"),
                "mock": bool(provider.get("mock")) or get_profile(run.job.model_profile).is_mock,
                "source": provider.get("source"), "prompts_hash": seq.prompts_hash,
                "mask_edit_frames": sorted(edits), "revision": run.job.input_revision}
        zip_path = run.work_dir / "masks.zip"
        write_masks_zip(seq, zip_path)
        preview = run.work_dir / "mask_preview.mp4"
        encode_mp4((np.repeat(m[..., None], 3, axis=2) for m in seq.masks), preview, pw, ph, fps=24)
        zip_id = run.store_asset("masks_zip", zip_path, "application/zip", source_asset_id=proxy.id, meta=meta)
        preview_id = run.store_asset("mask_preview", preview, "video/mp4", source_asset_id=zip_id,
                                     meta={"masks_key": key, "revision": run.job.input_revision})
        return [zip_id, preview_id]

    zip_id, preview_id = run.ensure_stage("masks", produce)
    zip_asset = run.asset(zip_id)
    meta = zip_asset.meta_json or {}
    return [zip_id, preview_id], {
        **{k: meta.get(k) for k in RESULT_FIELDS}, "masks_zip_asset_id": zip_id, "mask_preview_asset_id": preview_id,
        "reused": zip_asset.job_id != run.job.id,
    }
