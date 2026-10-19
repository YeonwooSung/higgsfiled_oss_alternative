from __future__ import annotations

import time
import uuid

import cv2
import numpy as np
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DbSession

from open_creative.camera_sketch.path import compile_path, load_presets, simplify_sketch  # noqa: F401
from open_creative.camera_sketch.render import hole_ratio, rasterize
from open_creative.camera_sketch.scene import build_mesh

from ..apps_registry import validate_config
from ..deps import current_session, get_db, owned_project
from ..errors import ApiError
from ..models import Asset, ProjectRevision
from ..models import Session as SessionRow
from .config import OUTPUT_SIZES, SketchPoint
from .scene_load import load_scene_info

router = APIRouter()

COMPILE_BUDGET_S = 2.0
HOLE_RESERVE_S = 0.5  # time the 160x90 estimate is assumed to need
HOLE_W, HOLE_H, HOLE_FRAMES = 160, 90, 6


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lateral_ratio: float | None = None
    push_ratio: float | None = None
    arc_deg: float | None = None
    easing: str | None = None
    focal_length_mm_equiv: float | None = Field(None, ge=35.0, le=70.0)


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    sketch: list[SketchPoint] = Field(default_factory=list, max_length=128)
    preset: str
    parameters: Parameters = Parameters()


@router.get("/camera-sketch/presets")
def presets():
    return {"presets": list(load_presets().values())}


def _hole_estimate(extras, scene, compiled, out_w: int, out_h: int) -> float:
    """Max hole ratio over ``HOLE_FRAMES`` sampled frames rendered at 160x90 (or 90x160)."""
    lw, lh = (HOLE_W, HOLE_H) if out_w >= out_h else (HOLE_H, HOLE_W)
    sc = max(lw / extras.src_w, lh / extras.src_h)
    sw, sh = max(1, round(extras.src_w * sc)), max(1, round(extras.src_h * sc))
    z = cv2.resize(extras.z_norm, (sw, sh), interpolation=cv2.INTER_AREA)
    K_src = extras.K_src * np.array([[sw / extras.src_w], [sh / extras.src_h], [1.0]])
    mesh = build_mesh(z, K_src, 1)
    colors = np.zeros((len(mesh.vertices), 3), np.uint8)
    K = np.asarray(compiled.K, dtype=np.float64) * np.array([[lw / out_w], [lh / out_h], [1.0]])
    n = len(compiled.samples)
    idx = sorted({round(i * (n - 1) / (HOLE_FRAMES - 1)) for i in range(HOLE_FRAMES)})
    return max(hole_ratio(rasterize(mesh, colors, K, np.asarray(compiled.samples[i]["T_cw"], float), lw, lh))
               for i in idx)


@router.post("/projects/{project_id}/camera-path/compile")
def compile_camera_path(project_id: str, body: CompileRequest, request: Request,
                        db: DbSession = Depends(get_db), session: SessionRow = Depends(current_session)):
    t0 = time.monotonic()
    project = owned_project(project_id, db, session)
    if project.app_slug != "camera-sketch":
        raise ApiError(422, "invalid_app", "This endpoint is only for Camera Sketch projects.")
    rev = db.get(ProjectRevision, (project.id, body.revision))
    if rev is None:
        raise ApiError(422, "revision_not_found", "Revision does not exist.", field="revision")
    cfg = validate_config(project.app_slug, rev.config_json)
    if cfg.scene_asset_id is None or cfg.depth_asset_id is None:
        raise ApiError(422, "depth_missing", "Analyze the scene before compiling a camera path.")
    scene_asset, depth_asset = db.get(Asset, cfg.scene_asset_id), db.get(Asset, cfg.depth_asset_id)
    if (scene_asset is None or depth_asset is None or scene_asset.project_id != project.id
            or depth_asset.project_id != project.id or scene_asset.state != "ready"
            or depth_asset.state != "ready"):
        raise ApiError(422, "asset_not_found", "Scene or depth asset is not ready.")
    p = body.parameters
    if p.focal_length_mm_equiv is not None:
        cfg.camera.focal_length_mm_equiv = p.focal_length_mm_equiv
    storage = request.app.state.storage
    scene, extras = load_scene_info(storage, scene_asset, depth_asset, cfg)
    out_w, out_h = OUTPUT_SIZES[cfg.output.aspect]
    path_cfg = {"preset": body.preset, "easing": p.easing or cfg.path.easing,
                "sketch_points": [pt.model_dump() for pt in body.sketch]}
    for k in ("lateral_ratio", "push_ratio", "arc_deg"):
        v = getattr(p, k)
        if v is not None:
            path_cfg[k] = v
        elif body.preset == cfg.path.preset:
            path_cfg[k] = getattr(cfg.path, k)  # pinned revision's value applies to its own preset
    compiled = compile_path(path_cfg, scene, out_w, out_h)
    hole, deferred = None, False
    if compiled.status != "invalid" and compiled.samples:
        if COMPILE_BUDGET_S - (time.monotonic() - t0) > HOLE_RESERVE_S:
            hole = _hole_estimate(extras, scene, compiled, out_w, out_h)
            compiled.diagnostics["hole_ratio"] = hole
        else:
            deferred = True
    return {"path": compiled.to_dict(), "control_points": compiled.control_points,
            "warnings": list(compiled.reasons), "status": compiled.status,
            "hole_ratio": hole, "deferred": deferred}
