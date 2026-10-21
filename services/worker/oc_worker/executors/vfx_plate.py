"""vfx.generate_plate: the LTX effect plate (``vfx_plate_v1``, GPU; mock profile: procedural plate) and its matte.

Stages (a separate job from vfx.track, so a failed or OOM plate never touches the track/mask outputs):
- ``plate``: submits the preset's ``plate_prompt`` (``{palette}`` -> the effect's palette name), ``plate_negative``,
  the seed (option, default ``config.plate.seed``) and the Ruling R3 shape: 768x448 (16:9) / 448x768 (9:16),
  frames = smallest 8k+1 covering the effect window (min 9). Stores ``raw_plate`` (the provider MP4 as returned,
  ``meta{preset_id, palette, seed, frames, mock, ...}``).
- ``matte``: streams the raw plate through ``matte_sequence`` with ``config.plate.matte``; stores
  ``plate_matte_preview`` (alpha as gray MP4) and ``matte_qa`` JSON. A QA ``check`` never swaps in a bundled plate:
  the user decides (spec §7).
"""

from __future__ import annotations

import json

import numpy as np
from open_creative.comfy.profiles import get_profile
from open_creative.media.video import Mp4Writer, decode_frames, probe
from open_creative.pocket_vfx.matte import (
    MatteConfig,
    MatteStats,
    matte_qa,
    matte_sequence,
)
from open_creative.pocket_vfx.plate import (
    GENERATED_SIZES,
    generated_plate_frames,
    plate_prompt,
    window_frames,
)
from open_creative.pocket_vfx.presets import load_presets
from open_creative.pocket_vfx.timeline import effect_window

from ..stages import JobRun, StageFailed
from .camera_common import json_asset, with_heartbeat
from .vfx_common import pinned_config, store_json

WORKFLOW = "vfx_plate_v1"


def vfx_generate_plate(run: JobRun) -> tuple[list[str], dict]:
    cfg, cfg_json = pinned_config(run)
    preset = load_presets()[cfg.effect.preset_id]
    opts = run.job.options_json or {}
    seed = opts.get("seed")
    seed = cfg.plate.seed if seed is None else int(seed)
    palette = cfg.effect.palette
    window = effect_window(cfg_json["effect"], cfg.trim.duration_ms)
    frames = generated_plate_frames(window_frames(window))
    pw, ph = GENERATED_SIZES[cfg.output.aspect]
    prompt = plate_prompt(preset, palette)

    def plate_stage(st):
        run.transition("running", "plate")
        bindings = {}
        if st.provider_job_id is None:
            bindings = {"prompt": prompt, "negative": preset["plate_negative"], "seed": seed, "width": pw,
                        "height": ph, "frames": frames}
        files = run.provider_stage(st, WORKFLOW, bindings)
        video = next((p for p in files if p.suffix == ".mp4"), None)
        if video is None:
            raise StageFailed("provider_failed", "The generation service returned no plate.")
        side = next((p for p in files if p.suffix == ".json"), None)
        provider = json.loads(side.read_text()) if side else {}
        run.transition("postprocessing", "plate")
        try:
            info = probe(video)
        except Exception as e:
            raise StageFailed("generated_shape_mismatch", "The generated plate could not be read.") from e
        if (info["width"], info["height"], info["frame_count"]) != (pw, ph, frames):
            raise StageFailed("generated_shape_mismatch",
                              f"The generated plate is {info['width']}x{info['height']}x{info['frame_count']}, "
                              f"expected {pw}x{ph}x{frames}.")
        mock = bool(provider.get("mock")) or get_profile(run.job.model_profile).is_mock
        meta = {"preset_id": cfg.effect.preset_id, "palette": palette, "seed": seed, "frames": frames,
                "width": pw, "height": ph, "aspect": cfg.output.aspect, "window_frames": window_frames(window),
                "mock": mock, "model": provider.get("model"), "prompt": prompt, "revision": run.job.input_revision}
        return [run.store_asset("raw_plate", video, "video/mp4", meta=meta)]

    (plate_id,) = run.ensure_stage("plate", plate_stage)

    def matte_stage(st):
        run.transition("running", "matte")
        plate = run.asset(plate_id)
        src = run.fetch(plate, "raw_plate.mp4")
        stats = MatteStats()
        preview = run.work_dir / "plate_matte_preview.mp4"
        mcfg = MatteConfig(cfg.plate.matte.threshold, cfg.plate.matte.knee)
        with Mp4Writer(preview, plate.width, plate.height, fps=24) as writer:
            for _, alpha in matte_sequence(with_heartbeat(run, decode_frames(src), 5), mcfg, stats):
                gray = np.rint(np.clip(alpha, 0.0, 1.0) * 255.0).astype(np.uint8)
                writer.write(np.repeat(gray[..., None], 3, axis=2))
        if writer.count != plate.frame_count:
            raise StageFailed("frame_count_mismatch", f"Matted {writer.count} plate frames, expected "
                              f"{plate.frame_count}.")
        qa = matte_qa(stats.summary())
        doc = {"schema_version": 1, **qa, "matte": cfg_json["plate"]["matte"], "plate_asset_id": plate_id,
               "frames": writer.count}
        preview_id = run.store_asset("plate_matte_preview", preview, "video/mp4", source_asset_id=plate_id,
                                     meta={"matte": cfg_json["plate"]["matte"], "revision": run.job.input_revision})
        qa_id = store_json(run, "matte_qa", doc, source_asset_id=plate_id,
                           meta={"status": qa["status"], "revision": run.job.input_revision})
        return [preview_id, qa_id]

    preview_id, qa_id = run.ensure_stage("matte", matte_stage)
    meta, doc = run.asset(plate_id).meta_json, json_asset(run, qa_id)
    return [plate_id, preview_id, qa_id], {
        "plate_asset_id": plate_id, "matte_preview_asset_id": preview_id, "matte_qa_asset_id": qa_id,
        "matte_qa": {k: doc[k] for k in ("status", "bg_nonuniformity", "flicker", "coverage", "reasons")},
        "frames": meta["frames"], "width": meta["width"], "height": meta["height"], "seed": meta["seed"],
        "palette": meta["palette"], "preset_id": meta["preset_id"], "mock": meta["mock"],
        "revision": run.job.input_revision,
    }
