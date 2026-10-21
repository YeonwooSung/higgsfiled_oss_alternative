"""vfx.render: tracked procedural effect + effect plate composited over the work clip -> H.264 MP4.

Render profiles (the render option wins over ``config.render_profile``): ``quick`` = procedural effect + the preset's
bundled MIT plate (``plate.bundled_id`` or the preset default; ``plate.asset_id`` is ignored), badge "Quick";
``generated`` = procedural effect + the ``raw_plate`` of a succeeded ``vfx.generate_plate`` job of this project for the
same preset (``plate.mode == "generated"``, ``plate.asset_id``), badge "Generated plate". Render stages never call a
provider: a revision that only changes scale/intensity/timing reuses the track and plate jobs as they are.

Stages (each stores durable assets; a restarted job resumes after the last finished stage):
- ``layers``: per-frame effect anchor in output px. ``follow=false`` -> the config anchor (uv x canvas) on every
  frame; ``follow=true`` -> the track job's points (uv01 x canvas). Invalid track frames follow the preset's
  ``invalid_policy``: ``hide`` draws no effect that frame, ``freeze`` holds the last valid position. Subject masks
  come from the track job (Ruling R2): when the render needs them (``masks_needed``: ``behind_subject``,
  ``split_glow``) the track job is required even with ``follow=false`` and its masks must carry the config's
  ``masks_key`` (else ``mask_required``). The plate is resolved here too: its source, frame count and the plate frame
  index of every effect-window frame (``plate_indices``, Ruling R3: trim / retime / loop only when the bundled plate
  is loopable and its seam passes ``loop_seam_ok``; otherwise ``plate_length``).
- ``composite``: streams the work-clip frames (one in flight) through ``composite_frame`` into ``Mp4Writer`` and an
  ``IntegrityAccumulator``; per-frame ROI size/box and changed pixel count (pre-encode, vs the source frame) are kept.
  The proxy masks (<= 120 x 640x360 bytes) are held in memory and upscaled per frame with ``upscale_mask`` (soft):
  ``behind_subject``/``split_glow`` restore the subject inside it, Energy Aura draws its contour band from the
  temporally smoothed masks (``smooth_masks``) plus the anchor glow. Occlusion QA = max over frames of
  ``subject_core_cover`` (fraction of subject-core pixels changed > 2/255; core = ``masks.subject_core``).
  The plate is matted on the fly (``PlateFrames``, one plate frame decoded at a time, ``config.plate.matte``), a
  bundled plate is tinted by the palette, then ``place_plate`` puts it behind the effect around the anchor
  (``pocket_vfx.plate``); it is occluded by the subject like the effect.
  An outside-ROI integrity failure fails the job (``roi_integrity``, Ruling R13): no video is stored.
- ``encode``: final video (the audio plan of ``pocket_vfx.audio`` - kept original audio and/or the preset's bundled
  SFX aligned to the effect peak - is muxed into a new ``render_video``; no audio -> the silent composite is the
  deliverable), output contract probe, ``cover`` PNG of the peak frame and the ``roi_qa`` JSON.
"""

from __future__ import annotations

import uuid

import numpy as np
from oc_api.models import Asset, Job
from oc_api.pocket_vfx.jobs import (
    config_masks_key,
    config_track_key,
    masks_needed,
    track_masks,
)
from open_creative.media.video import Mp4Writer, decode_frames, probe
from open_creative.pocket_vfx.audio import mux, plan_audio
from open_creative.pocket_vfx.composite import FrameInputs, composite_frame
from open_creative.pocket_vfx.integrity import IntegrityAccumulator, subject_core_cover
from open_creative.pocket_vfx.masks import (
    CORE_ERODE_PX,
    MaskFormatError,
    read_masks_zip,
    smooth_masks,
    subject_core,
    upscale_mask,
)
from open_creative.pocket_vfx.matte import (
    MatteConfig,
    PlateLengthError,
    loop_seam_ok,
    plate_indices,
)
from open_creative.pocket_vfx.plate import (
    PLATE_GAIN,
    PLATE_SIDE,
    PlateFrames,
    aura_roi,
    bundled_entry,
    bundled_path,
    impact_weight,
    place_plate,
    plate_envelope,
    restrict,
    tint_matte,
    window_frames,
)
from open_creative.pocket_vfx.presets import load_palettes, load_presets
from open_creative.pocket_vfx.procedural import EffectLayer, render_effect
from open_creative.pocket_vfx.timeline import EffectWindow, effect_window
from open_creative.pocket_vfx.track import anchor_positions
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

BADGES = {"quick": {"label": "Quick", "label_ko": "Quick"},
          "generated": {"label": "Generated plate", "label_ko": "생성 plate"}}
EFFECT_PARAMS = ("scale", "intensity", "palette", "rotation_deg", "squash")


def generated_plate(run: JobRun, cfg) -> Asset:
    """``plate.asset_id`` of a ``generated`` render: a ``raw_plate`` of this project for the effect's preset, made
    by a succeeded vfx.generate_plate job (the API checks the same before the job is created)."""
    if cfg.plate.mode != "generated" or cfg.plate.asset_id is None:
        raise StageFailed("plate_required", "Generate a plate (or pick it) before a Generated plate render.")
    asset = run.db.get(Asset, uuid.UUID(str(cfg.plate.asset_id)))
    if asset is None or asset.project_id != run.project.id or asset.role != "raw_plate" or asset.state != "ready":
        raise StageFailed("plate_mismatch", "The selected plate is not a generated plate of this project.")
    if (asset.meta_json or {}).get("preset_id") != cfg.effect.preset_id:
        raise StageFailed("plate_mismatch", "The selected plate was generated for another effect.")
    job = run.db.get(Job, asset.job_id) if asset.job_id else None
    if job is None or job.kind != "vfx.generate_plate" or job.state != "succeeded":
        raise StageFailed("plate_mismatch", "The selected plate has not finished generating.")
    return asset


def _first_last(frames) -> tuple[np.ndarray, np.ndarray]:
    first = last = None
    for f in frames:
        first = f if first is None else first
        last = f
    if first is None:
        raise StageFailed("plate_length", "The plate has no frames.")
    return first, last


def resolve_plate(run: JobRun, cfg, profile: str, preset: dict, window: EffectWindow, w: int,
                  h: int) -> dict | None:
    """The plate of this render: source, frames, and the plate frame of each effect-window frame (None: the preset
    has no bundled plate -> procedural only)."""
    portrait = h > w
    if profile == "quick":
        entry = bundled_entry(preset, cfg.plate.bundled_id)
        if entry is None:
            if cfg.plate.bundled_id is not None:
                raise StageFailed("plate_mismatch", "This plate belongs to another effect.")
            return None
        path = bundled_path(entry, portrait)
        if not path.is_file():
            raise StageFailed("plate_missing", f"The bundled plate {entry['id']} is not installed on this server.")
        doc = {"source": "bundled", "bundled_id": entry["id"], "portrait": portrait, "asset_id": None,
               "tint": cfg.effect.palette, "mock": False}
        loopable = bool(entry["loopable"])
    else:
        asset = generated_plate(run, cfg)
        path = run.fetch(asset, "plate.mp4")
        doc = {"source": "generated", "bundled_id": None, "portrait": portrait, "asset_id": str(asset.id),
               "tint": None, "mock": bool((asset.meta_json or {}).get("mock"))}
        loopable = False  # LTX output is not made to loop
    info = probe(path)
    p, n = info["frame_count"], window_frames(window)
    seam_ok = None
    if n > 2 * p and loopable:
        seam_ok = loop_seam_ok(*_first_last(decode_frames(path)))
    try:
        indices = plate_indices(p, n, loopable=loopable, seam_ok=bool(seam_ok))
    except PlateLengthError as e:
        raise StageFailed("plate_length", f"The {p}-frame plate cannot cover the {n}-frame effect "
                          "(retime 0.5-2x only). Shorten the effect or generate a new plate.") from e
    return {**doc, "frames": p, "size": [info["width"], info["height"]], "seam_ok": seam_ok,
            "indices": indices, "matte": {"threshold": cfg.plate.matte.threshold, "knee": cfg.plate.matte.knee}}


class PlateLayers:
    """Per-frame plate ``EffectLayer`` of a resolved plate (``resolve_plate``), placed around the anchor."""

    def __init__(self, run: JobRun, plate: dict | None, preset_id: str, params: dict, window: EffectWindow,
                 w: int, h: int):
        self.plate, self.preset_id, self.params, self.window, self.w, self.h = plate, preset_id, params, window, w, h
        self.frames = None
        if plate is None:
            return
        if plate["source"] == "bundled":
            entry = bundled_entry(load_presets()[preset_id], plate["bundled_id"])
            path = bundled_path(entry, plate["portrait"])
        else:
            path = run.fetch(run.asset(plate["asset_id"]), "plate.mp4")
        self.frames = PlateFrames(lambda: decode_frames(path), MatteConfig(**plate["matte"]))
        self.palette = load_palettes()[plate["tint"]] if plate["tint"] else None
        self.size = min(w, h) * params["scale"]
        self.weight = impact_weight(round(PLATE_SIDE * self.size)) if preset_id == "impact_wave_v1" else None

    def at(self, i: int, anchor, subject: np.ndarray | None) -> EffectLayer | None:
        """``subject``: the output-size mask an Energy Aura plate is confined around (its contour band)."""
        if self.frames is None or not self.window.start <= i <= self.window.end:
            return None
        gain = self.params["intensity"] * plate_envelope(self.window, i) * PLATE_GAIN
        aura = self.preset_id == "energy_aura_v1"
        center = tuple(anchor) if anchor is not None else None
        if center is None and aura and subject is not None and (subject > 0.5).any():
            ys, xs = np.nonzero(subject > 0.5)
            center = ((xs.min() + xs.max() + 1) / 2.0, (ys.min() + ys.max() + 1) / 2.0)
        if gain <= 0 or center is None:
            return None
        rgb, alpha = self.frames.get(self.plate["indices"][i - self.window.start])
        if self.palette is not None:
            rgb = tint_matte(rgb, alpha, self.palette)
        layer = place_plate(rgb, alpha, center, PLATE_SIDE * self.size, self.w, self.h, gain, self.weight)
        if aura and layer is not None:
            layer = restrict(layer, aura_roi(subject, tuple(anchor) if anchor is not None else None, self.size,
                                             self.w, self.h))
        return layer


def load_masks(run: JobRun, subject: dict | None, n: int) -> list | None:
    """The proxy masks of a render plan's ``subject_masks`` (None: the render had no masks)."""
    if subject is None:
        return None
    try:
        seq = read_masks_zip(run.fetch(run.asset(subject["masks_zip_asset_id"]), "masks.zip"), expect_frames=n,
                             expect_size=tuple(subject["size"]))
    except (MaskFormatError, OSError, ValueError) as e:
        raise StageFailed("mask_required", "The subject masks could not be read. Track again.") from e
    return seq.masks


class FrameComposer:
    """Per-frame composite inputs of a render plan (``layers`` stage document). The render and the layers export
    both build their frames here, so the exported layers are the ones that were composited."""

    def __init__(self, run: JobRun, cfg, cfg_json: dict, plan: dict, window: EffectWindow, w: int, h: int, n: int):
        self.cfg, self.w, self.h = cfg, w, h
        self.anchors = plan["anchors"]
        self.params = {k: cfg_json["effect"][k] for k in EFFECT_PARAMS}
        self.plates = PlateLayers(run, plan["plate"], cfg.effect.preset_id, self.params, window, w, h)
        self.window = window
        self.masks = load_masks(run, plan["subject_masks"], n)
        self.occlude = self.masks is not None and cfg.effect.layer_order != "in_front"
        aura = cfg.effect.preset_id == "energy_aura_v1"
        self.band_masks = smooth_masks(self.masks, radius=1) if self.masks is not None and aura else None

    def subject_at(self, i: int) -> np.ndarray | None:
        """The output-size soft subject mask of frame ``i`` (None: the render had no masks)."""
        return upscale_mask(self.masks[i], self.w, self.h) if self.masks is not None else None

    def inputs(self, i: int, frame: np.ndarray) -> tuple[FrameInputs, np.ndarray | None]:
        """``(composite inputs, occluding subject mask or None)`` of frame ``i``."""
        anchor = self.anchors[i]
        subject = self.subject_at(i) if self.occlude else None
        band = upscale_mask(self.band_masks[i], self.w, self.h) if self.band_masks is not None else None
        effect = None
        if anchor is not None or band is not None:
            effect = render_effect(self.cfg.effect.preset_id, self.params, self.window, i,
                                   tuple(anchor) if anchor is not None else None, self.w, self.h,
                                   self.cfg.plate.seed, band)
        inp = FrameInputs(source=frame, effect=effect, plate=self.plates.at(i, anchor, band), subject_mask=subject,
                          front_glow=None, roi_limit=None)
        return inp, subject


def final_video(run: JobRun, cfg, video_id: str, work: Asset, preset: dict,
                window: EffectWindow) -> tuple[str, dict | None]:
    """``(asset id, audio summary)`` of the deliverable MP4. Without audio (no kept original, no SFX) the silent
    composite is the deliverable and the summary is None; otherwise the plan is muxed into a new ``render_video``."""
    source = derived_asset(run, work, "source_audio")
    source_path = run.fetch(source, "source_audio.m4a") if source and cfg.audio.keep_original else None
    plan = plan_audio(cfg, preset, window, source_path)
    if plan is None:
        return video_id, None
    if plan.sfx is not None and not plan.sfx.is_file():
        raise StageFailed("sfx_missing", "The effect sound is not available on this server.")
    composite = run.asset(video_id)
    out = run.work_dir / "render_audio.mp4"
    mux(run.fetch(composite, "render_silent.mp4"), plan, out)
    summary = {"original_kept": plan.original is not None, "sfx": plan.sfx is not None,
               "sfx_delay_ms": plan.sfx_delay_ms if plan.sfx else None,
               "sfx_gain_db": plan.sfx_gain_db if plan.sfx else None, "duration_ms": plan.duration_ms}
    meta = {**composite.meta_json, "has_audio": True, "audio": summary}
    return run.store_asset("render_video", out, "video/mp4", source_asset_id=work.id, meta=meta), summary


def _anchors(track: dict | None, cfg, w: int, h: int, n: int, policy: str) -> list[list[float] | None]:
    return anchor_positions(track, (cfg.anchor.x, cfg.anchor.y), w, h, n, policy)


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
    if profile == "generated":
        generated_plate(run, cfg)  # fail before any work
    work = fresh_work_clip(run, cfg, cfg_json)
    w, h, n = canvas(cfg)
    preset = load_presets()[cfg.effect.preset_id]
    policy = preset.get("invalid_policy", "hide")
    window = effect_window(cfg_json["effect"], cfg.trim.duration_ms)
    if cfg.effect.preset_id == "blink_out_v1":
        raise StageFailed("mask_required", "Blink Out needs subject masks; track the subject first.")

    needs_masks = masks_needed(cfg, preset)

    def layers_stage(stage):
        run.transition("running", "layers")
        track, track_job, subject = None, None, None
        if cfg.anchor.follow or needs_masks or opts.get("track_job_id"):
            track_job = source_job(run, opts.get("track_job_id"), ("vfx.track",), "track_required",
                                   "Track the anchor before rendering a following effect." if cfg.anchor.follow
                                   else "Track the subject before rendering an effect behind it.")
            clip = ready_asset(run, cfg.video_asset_id, "clip", "video_required")
            if (track_job.result_json or {}).get("track_key") != config_track_key(cfg, clip):
                raise StageFailed("track_stale", "The clip, anchor, corrections or subject prompts changed after "
                                  "tracking. Track again.")
            if cfg.anchor.follow:
                track = json_asset(run, track_job.result_json["track_asset_id"])
            m = track_masks(track_job, config_masks_key(cfg, clip))
            if m is not None:
                subject = {"masks_zip_asset_id": m["masks_zip_asset_id"], "size": m["size"],
                           "masks_key": m["masks_key"], "diagnostics": m.get("diagnostics") or []}
        if needs_masks and subject is None:
            raise StageFailed("mask_required", "This effect needs a subject mask. Select the subject and track "
                              "again.")
        motion_asset = derived_asset(run, work, "motion_report")
        motion = json_asset(run, motion_asset.id) if motion_asset else None
        plate = resolve_plate(run, cfg, profile, preset, window, w, h)
        doc = {"schema_version": 1, "canvas": [w, h], "frame_count": n, "space": "output_px",
               "window": {"start": window.start, "peak": window.peak, "end": window.end},
               "follow": cfg.anchor.follow, "invalid_policy": policy,
               "track_job_id": str(track_job.id) if track_job else None,
               "anchors": _anchors(track, cfg, w, h, n, policy), "plate": plate, "subject_masks": subject,
               "tracking_warnings": _tracking_warnings(track, window, policy, motion, preset)}
        return [store_json(run, "render_layers", doc, source_asset_id=work.id,
                           meta={"revision": run.job.input_revision})]

    (layers_id,) = run.ensure_stage("layers", layers_stage)

    def composite_stage(stage):
        run.transition("running", "composite")
        plan = json_asset(run, layers_id)
        composer = FrameComposer(run, cfg, cfg_json, plan, window, w, h, n)
        occlude = composer.occlude
        acc = IntegrityAccumulator()
        roi_px, roi_bbox, changed_px, cover = [], [], [], []
        out_path = run.work_dir / "render.mp4"
        src = run.fetch(work, "work.mp4")
        with Mp4Writer(out_path, w, h, fps=24) as writer:
            for i, frame in enumerate(with_heartbeat(run, decode_frames(src), 5)):
                if i >= n:
                    break
                inp, subject = composer.inputs(i, frame)
                out, roi = composite_frame(inp, cfg.effect.layer_order)
                writer.write(out)
                acc.add(frame, out, roi)
                if occlude:
                    cover.append(subject_core_cover(out, frame, subject_core(subject)))
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
        occlusion_qa = ({"max": max(cover), "worst_frame": int(np.argmax(cover)), "frames": len(cover),
                         "core_erode_px": CORE_ERODE_PX} if cover else None)
        plate = plan["plate"]
        meta = {"revision": run.job.input_revision, "render_profile": profile, "label": BADGES[profile]["label"],
                "plate_source": plate["source"] if plate else "procedural",
                "plate_asset_id": plate["asset_id"] if plate else None,
                "bundled_plate_id": plate["bundled_id"] if plate else None, "preset_id": cfg.effect.preset_id,
                "layer_order": cfg.effect.layer_order, "occlusion": occlude, "occlusion_qa": occlusion_qa,
                "has_audio": False, "mock": bool(plate and plate["mock"]), "integrity": integrity,
                "frame_stats": {"roi_px": roi_px, "roi_bbox": roi_bbox, "changed_px": changed_px,
                                "core_cover": cover or None}}
        return [run.store_asset("render_video", out_path, "video/mp4", source_asset_id=work.id, meta=meta)]

    (composite_id,) = run.ensure_stage("composite", composite_stage)

    def encode_stage(stage):
        run.transition("postprocessing", "encode")
        video_id, audio = final_video(run, cfg, composite_id, work, preset, window)
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
              "layer_order": meta["layer_order"], "occlusion_qa": meta["occlusion_qa"], "max_outside_err_allowed": 1}
        return [video_id, run.store_asset("cover", cover, "image/png", source_asset_id=video_id,
                                          meta={"frame": window.peak, "revision": run.job.input_revision}),
                store_json(run, "roi_qa", qa, source_asset_id=video_id,
                           meta={"status": meta["integrity"]["status"], "has_audio": audio is not None,
                                       "audio": audio})]

    video_id, cover_id, qa_id = run.ensure_stage("encode", encode_stage)
    plan, meta = json_asset(run, layers_id), run.asset(composite_id).meta_json
    qa_meta = run.asset(qa_id).meta_json
    badge = {"render_profile": profile, **BADGES[profile], "plate_source": meta["plate_source"], "mock": meta["mock"]}
    return [layers_id, video_id, cover_id, qa_id], {
        "render_profile": profile, "label": meta["label"], "badge": badge, "roi_qa": meta["integrity"],
        "occlusion_qa": (meta["occlusion_qa"] or {}).get("max"), "occlusion": meta["occlusion"],
        "mask_warnings": (plan["subject_masks"] or {}).get("diagnostics") or [],
        "tracking_warnings": plan["tracking_warnings"], "plate_source": meta["plate_source"],
        "plate_asset_id": meta["plate_asset_id"], "bundled_plate_id": meta["bundled_plate_id"],
        "frame_count": n, "mock": meta["mock"], "has_audio": bool(qa_meta["has_audio"]), "audio": qa_meta["audio"],
        "render_video_asset_id": video_id, "cover_asset_id": cover_id, "roi_qa_asset_id": qa_id,
        "layers_asset_id": layers_id, "track_job_id": plan["track_job_id"], "revision": run.job.input_revision,
        "width": w, "height": h,
    }
