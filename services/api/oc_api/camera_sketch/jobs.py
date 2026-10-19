"""Camera Sketch job kinds: option schemas, pre-submit validation, geometry hash (spec §11, §12)."""

from __future__ import annotations

import hashlib
import json
import uuid

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session as DbSession

from open_creative.camera_sketch.path import compile_path
from open_creative.comfy.profiles import GPU_SECONDS_CAP

from ..apps import JobPlan
from ..errors import ApiError
from ..models import Job, Project, ProjectRevision
from ..product_lock.jobs import _ready_asset
from .config import OUTPUT_SIZES, CameraSketchConfig
from .scene_load import scene_info_from_meta


class _Opts(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyOptions(_Opts):
    pass


class CinematicOptions(_Opts):
    guide_job_id: uuid.UUID


OPTION_MODELS: dict[str, type[_Opts]] = {
    "camera.analyze": EmptyOptions,
    "camera.guide": EmptyOptions,
    "camera.cinematic": CinematicOptions,
}

STAGES = {
    "camera.analyze": ["depth"],
    "camera.guide": ["compile", "render", "encode"],
    "camera.cinematic": ["prepare", "generate", "postprocess", "qa"],
}


def parse_options(kind: str, options: dict) -> _Opts:
    try:
        return OPTION_MODELS[kind].model_validate(options)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise ApiError(422, "invalid_options", first["msg"], field=f"options.{loc}" if loc else "options") from None


def geometry_hash(cfg: CameraSketchConfig) -> str:
    """Hash of everything that changes the guide geometry. Generation settings are deliberately excluded."""
    d = cfg.model_dump(mode="json")
    geom = {k: d[k] for k in ("scene_asset_id", "depth_asset_id", "target", "camera", "path", "output")}
    geom["scene"] = {"depth_contrast": d["scene"]["depth_contrast"],
                     "edge_protection": d["scene"]["edge_protection"]}
    canon = json.dumps(geom, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def build_path_cfg(cfg: CameraSketchConfig, preset: str | None = None, sketch: list[dict] | None = None,
                   overrides: dict | None = None) -> dict:
    """Config -> ``compile_path`` path dict (shared by the compile endpoint and job validation).

    ``overrides`` (easing / lateral_ratio / push_ratio / arc_deg; ``None`` values ignored) win; otherwise the
    pinned revision's value applies, but only to its own preset.
    """
    ov = {k: v for k, v in (overrides or {}).items() if v is not None}
    preset = preset or cfg.path.preset
    if sketch is None:
        sketch = [p.model_dump() for p in cfg.path.sketch_points]
    path_cfg = {"preset": preset, "easing": ov.get("easing") or cfg.path.easing, "sketch_points": sketch}
    for k in ("lateral_ratio", "push_ratio", "arc_deg"):
        if k in ov:
            path_cfg[k] = ov[k]
        elif preset == cfg.path.preset:
            path_cfg[k] = getattr(cfg.path, k)
    return path_cfg


def _scene_assets(db: DbSession, project: Project, cfg: CameraSketchConfig, need_depth: bool):
    scene = _ready_asset(db, project, cfg.scene_asset_id, "scene", "scene_required", "config.scene_asset_id")
    depth = None
    if need_depth:
        depth = _ready_asset(db, project, cfg.depth_asset_id, "depth", "depth_missing", "config.depth_asset_id")
    return scene, depth


def _check_guide_path(db: DbSession, project: Project, cfg: CameraSketchConfig) -> None:
    scene, depth = _scene_assets(db, project, cfg, True)
    info = scene_info_from_meta(scene, depth, cfg)  # also checks depth lineage
    out_w, out_h = OUTPUT_SIZES[cfg.output.aspect]
    compiled = compile_path(build_path_cfg(cfg), info, out_w, out_h)
    if compiled.status == "invalid":
        raise ApiError(422, "path_invalid", "Camera path is invalid: " + ", ".join(compiled.reasons),
                       field="config.path")


def _check_guide_job(db: DbSession, project: Project, cfg: CameraSketchConfig, guide_id: uuid.UUID) -> None:
    guide = db.get(Job, guide_id)
    field = "options.guide_job_id"
    if guide is None or guide.project_id != project.id or guide.kind != "camera.guide":
        raise ApiError(422, "guide_not_found", "Guide job not found.", field=field)
    if guide.state != "succeeded":
        raise ApiError(422, "guide_not_finished", "Guide has not finished.", field=field)
    rev = db.get(ProjectRevision, (project.id, guide.input_revision))
    if rev is None:
        raise ApiError(422, "guide_stale", "The guide's revision no longer exists.", field=field)
    guide_cfg = CameraSketchConfig.model_validate(rev.config_json)
    if geometry_hash(guide_cfg) != geometry_hash(cfg):
        raise ApiError(422, "guide_stale", "The camera path changed after the guide was rendered. Render the "
                       "guide again.", field=field)
    expected = cfg.output.duration_ms * cfg.output.fps // 1000
    if (guide.result_json or {}).get("frame_count") != expected:
        raise ApiError(422, "frame_count_mismatch", f"The guide does not have {expected} frames.", field=field)


def plan_job(db: DbSession, storage, project: Project, cfg: CameraSketchConfig, kind: str, options: dict,
             *, validate_assets: bool = True) -> JobPlan:
    if kind not in OPTION_MODELS:
        raise ApiError(422, "invalid_kind", f"kind {kind!r} is not available", field="kind")
    opts = parse_options(kind, options)
    data = opts.model_dump(mode="json")
    reserved = GPU_SECONDS_CAP[kind]
    if validate_assets:
        if kind == "camera.analyze":
            _scene_assets(db, project, cfg, False)
        elif kind == "camera.guide":
            _check_guide_path(db, project, cfg)
        else:
            _scene_assets(db, project, cfg, True)
            _check_guide_job(db, project, cfg, opts.guide_job_id)
    return JobPlan(kind, data, list(STAGES[kind]), kind != "camera.guide", reserved)
