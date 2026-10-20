"""Pocket VFX job kinds: option schemas, pre-submit validation (spec §11) and the track reuse key."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from open_creative.comfy.profiles import GPU_SECONDS_CAP
from open_creative.pocket_vfx.keys import model_rev, track_key
from open_creative.pocket_vfx.presets import load_presets
from open_creative.pocket_vfx.timeline import frame_count, frame_index, timing_errors

from ..apps import JobPlan
from ..errors import ApiError
from ..models import Asset, Job, Project
from ..product_lock.jobs import _ready_asset
from .config import SEED_MAX, PocketVfxConfig


class _Opts(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyOptions(_Opts):
    pass


class PlateOptions(_Opts):
    seed: int | None = Field(None, ge=0, le=SEED_MAX)  # None: the config's plate.seed


class RenderOptions(_Opts):
    track_job_id: uuid.UUID | None = None
    render_profile: Literal["quick", "generated"]


OPTION_MODELS: dict[str, type[_Opts]] = {
    "vfx.analyze": EmptyOptions,  # trim comes from the pinned revision
    "vfx.track": EmptyOptions,
    "vfx.generate_plate": PlateOptions,
    "vfx.render": RenderOptions,
}

STAGES = {
    "vfx.analyze": ["normalize", "proxy", "filmstrip", "audio", "motion"],
    "vfx.track": ["track", "masks"],
    "vfx.generate_plate": ["plate", "matte"],
    "vfx.render": ["layers", "composite", "encode"],
}

# Codes of ``validate`` that block the track job (it does not depend on effect timing, plates or the clean plate).
_TRACK_PREREQUISITES = ("video_required", "asset_not_ready", "trim_range", "analysis_required", "analysis_stale",
                        "anchor_range", "frame_range")


def parse_options(kind: str, options: dict) -> _Opts:
    try:
        return OPTION_MODELS[kind].model_validate(options)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise ApiError(422, "invalid_options", first["msg"], field=f"options.{loc}" if loc else "options") from None


def masks_needed(cfg: PocketVfxConfig, preset: dict) -> bool:
    """Whether vfx.track runs its (GPU) masks stage."""
    return cfg.effect.layer_order != "in_front" or bool(preset["input_requirements"].get("subject_mask"))


def source_sha(clip: Asset) -> str | None:
    return (clip.meta_json or {}).get("original_sha256") or clip.sha256


def config_track_key(cfg: PocketVfxConfig, clip: Asset) -> str | None:
    sha = source_sha(clip)
    return track_key(cfg.model_dump(mode="json"), sha, model_rev(cfg.model_profile)) if sha else None


def analysis_matches(work: Asset, config: dict) -> bool:
    """The work clip was analyzed from the config's clip, trim and aspect (``config`` is the JSON dump)."""
    meta = work.meta_json or {}
    return (str(meta.get("source_asset_id")) == str(config.get("video_asset_id"))
            and meta.get("trim") == config["trim"] and meta.get("aspect") == config["output"]["aspect"])


def clip_duration_ms(clip: Asset) -> int | None:
    return clip.duration_ms or ((clip.meta_json or {}).get("source") or {}).get("duration_ms")


def plate_length_errors(cfg: PocketVfxConfig, *_args) -> list[dict]:
    """Plate frames vs effect window (Ruling R3). Stub: Task 12 fills it with ``retime_factor``/``plate_length``."""
    return []


def _err(code: str, field: str, message: str) -> dict:
    return {"code": code, "field": field, "message": message}


def _asset(db: DbSession, project: Project, asset_id, role: str) -> Asset | None:
    a = db.get(Asset, asset_id) if asset_id else None
    return a if a is not None and a.project_id == project.id and a.role == role else None


def _input_errors(db: DbSession, project: Project, cfg: PocketVfxConfig) -> tuple[list[dict], Asset | None]:
    """Clip, trim and analysis checks; returns the errors and the ready clip (if any)."""
    errors: list[dict] = []
    clip = _asset(db, project, cfg.video_asset_id, "clip")
    if clip is None:
        errors.append(_err("video_required", "config.video_asset_id", "Upload a clip first."))
    elif clip.state != "ready":
        errors.append(_err("asset_not_ready", "config.video_asset_id", "The clip has not passed inspection yet."))
        clip = None
    if clip is not None:
        dur = clip_duration_ms(clip)
        if dur is not None and cfg.trim.source_start_ms + cfg.trim.duration_ms > dur:
            errors.append(_err("trim_range", "config.trim.duration_ms", "The trim runs past the end of the clip."))
    work = _asset(db, project, cfg.work_asset_id, "work_clip")
    if cfg.work_asset_id is None:
        errors.append(_err("analysis_required", "config.work_asset_id", "Prepare the clip before continuing."))
    elif work is None or work.state != "ready" or not analysis_matches(work, cfg.model_dump(mode="json")):
        errors.append(_err("analysis_stale", "config.work_asset_id",
                           "The clip, trim or aspect changed after the clip was prepared. Prepare it again."))
    return errors, clip


def _frame_errors(cfg: PocketVfxConfig) -> list[dict]:
    n = frame_count(cfg.trim.duration_ms)
    errors = []
    if cfg.anchor.frame >= n:
        errors.append(_err("anchor_range", "config.anchor.frame", f"The anchor frame must be below {n}."))
    for name in ("corrections", "subject_prompts", "mask_edits"):
        for i, item in enumerate(getattr(cfg, name)):
            if item.frame >= n:
                errors.append(_err("frame_range", f"config.{name}.{i}.frame", f"Frames must be below {n}."))
    return errors


_TIMING_MESSAGES = {"timing_order": "Effect start, peak and end must be in order.",
                    "timing_range": "The effect must stay inside the trimmed clip."}


def latest_fresh_track(db: DbSession, project: Project, key: str) -> Job | None:
    jobs = db.scalars(select(Job).where(Job.project_id == project.id, Job.kind == "vfx.track",
                                        Job.state == "succeeded").order_by(Job.created_at.desc()).limit(50))
    return next((j for j in jobs if (j.result_json or {}).get("track_key") == key), None)


def invalid_at(track: Job, frame: int) -> bool:
    """``invalid_intervals`` are inclusive ``[first, last]`` frame pairs (track_to_json)."""
    return any(a <= frame <= b for a, b in (track.result_json or {}).get("invalid_intervals") or [])


def validate(db: DbSession, project: Project, cfg: PocketVfxConfig, *, track: Job | None = None) -> dict:
    """Pre-render checks (spec §11). ``track`` is the track the render will use; default: the newest fresh one."""
    preset = load_presets()[cfg.effect.preset_id]
    blocking, clip = _input_errors(db, project, cfg)
    warnings: list[dict] = []
    blocking += [_err(e["code"], f"config.effect.{e['field']}", _TIMING_MESSAGES[e["code"]])
                 for e in timing_errors(cfg.effect.model_dump(), cfg.trim.duration_ms)]
    blocking += _frame_errors(cfg)
    if track is None and clip is not None:
        key = config_track_key(cfg, clip)
        track = latest_fresh_track(db, project, key) if key else None
    if track is not None and invalid_at(track, frame_index(cfg.effect.peak_ms)):
        e = _err("track_invalid_at_peak", "config.effect.peak_ms",
                 "Tracking is lost at the peak frame. Add a correction or move the peak.")
        (blocking if cfg.anchor.follow else warnings).append(e)
    bundled = {p["id"] for p in preset["bundled_plates"]}
    if cfg.plate.bundled_id is not None and cfg.plate.bundled_id not in bundled:
        blocking.append(_err("plate_mismatch", "config.plate.bundled_id", "This plate belongs to another effect."))
    blocking += plate_length_errors(cfg)
    if preset["input_requirements"].get("clean_plate"):
        plate = _asset(db, project, cfg.clean_plate_asset_id, "clean_plate")
        if plate is None:
            blocking.append(_err("clean_plate_required", "config.clean_plate_asset_id",
                                 "Blink Out needs an empty background frame from the same camera position."))
        elif plate.state != "ready":
            blocking.append(_err("asset_not_ready", "config.clean_plate_asset_id",
                                 "The background frame has not passed inspection yet."))
    return {"valid": not blocking, "blocking_errors": blocking, "warnings": warnings}


def _raise_first(errors: list[dict], codes: tuple[str, ...]) -> None:
    for e in errors:
        if e["code"] in codes:
            raise ApiError(422, e["code"], e["message"], field=e["field"])


def _check_track_job(db: DbSession, project: Project, cfg: PocketVfxConfig, track_id: uuid.UUID) -> Job:
    field = "options.track_job_id"
    track = db.get(Job, track_id)
    if track is None or track.project_id != project.id or track.kind != "vfx.track":
        raise ApiError(422, "track_not_found", "Track job not found.", field=field)
    if track.state != "succeeded":
        raise ApiError(422, "track_not_finished", "Tracking has not finished.", field=field)
    clip = _ready_asset(db, project, cfg.video_asset_id, "clip", "video_required", "config.video_asset_id")
    key = config_track_key(cfg, clip)
    if key is None or (track.result_json or {}).get("track_key") != key:
        raise ApiError(422, "track_stale", "The clip, anchor, corrections or subject prompts changed after "
                       "tracking. Track again.", field=field)
    return track


def plan_job(db: DbSession, storage, project: Project, cfg: PocketVfxConfig, kind: str, options: dict,
             *, validate_assets: bool = True) -> JobPlan:
    if kind not in OPTION_MODELS:
        raise ApiError(422, "invalid_kind", f"kind {kind!r} is not available", field="kind")
    opts = parse_options(kind, options)
    data = opts.model_dump(mode="json")
    stages, uses_gpu = list(STAGES[kind]), kind == "vfx.generate_plate"
    if kind == "vfx.track":
        uses_gpu = masks_needed(cfg, load_presets()[cfg.effect.preset_id])
        stages = stages if uses_gpu else ["track"]
    if kind == "vfx.generate_plate" and opts.seed is None:
        data["seed"] = cfg.plate.seed
    reserved = GPU_SECONDS_CAP[kind] if uses_gpu else 0
    if validate_assets:
        if kind == "vfx.analyze":
            errors, _ = _input_errors(db, project, cfg)
            _raise_first(errors, ("video_required", "asset_not_ready", "trim_range"))
        elif kind == "vfx.track":
            errors, _ = _input_errors(db, project, cfg)
            _raise_first(errors + _frame_errors(cfg), _TRACK_PREREQUISITES)
        else:
            track = None
            if kind == "vfx.render":
                if cfg.anchor.follow and opts.track_job_id is None:
                    raise ApiError(422, "track_required", "Track the anchor before rendering a following effect.",
                                   field="options.track_job_id")
                if opts.track_job_id is not None:
                    track = _check_track_job(db, project, cfg, opts.track_job_id)
            report = validate(db, project, cfg, track=track)
            if not report["valid"]:
                raise ApiError(422, "validation_failed", report["blocking_errors"][0]["message"],
                               field=report["blocking_errors"][0]["field"],
                               details={"blocking_errors": report["blocking_errors"]})
    return JobPlan(kind, data, stages, uses_gpu, reserved)
