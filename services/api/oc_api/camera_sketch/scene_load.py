"""Load a project's scene + depth assets into a compiler ``SceneInfo`` (shared by compile and guide jobs).

Which to use:
- ``scene_info_from_meta``: metadata only (no pixel decoding). Compile endpoint -> ``compile_path``.
- ``load_scene_info(..., max_side=None)``: full-resolution depth -> ``prepare_z`` -> ``z_norm``; use this for
  guide/cinematic jobs (Task 8/10) that render at full size.
- ``load_scene_info(..., max_side=N)``: depth downsampled (area) to N px long side BEFORE ``prepare_z``;
  an estimate-only path (hole-ratio preview).
Editor fields: ``target.depth_scale`` scales the target only (``target_world = ray * depth_scale``);
``scene.depth_contrast`` is ``z_norm ** contrast`` (target stays 1); ``scene.edge_protection`` e maps to
``edge_rel_threshold = 0.12 - 0.08 e`` (0.5 -> 0.08), exposed in the extras for mesh building.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import cv2
import numpy as np

from open_creative.camera_sketch.camera import intrinsics
from open_creative.camera_sketch.path import SceneInfo
from open_creative.camera_sketch.scene import DepthConvention, decode_depth_png16, depth_to_z, prepare_z
from open_creative.media.image import normalize_image

from ..errors import ApiError
from ..models import Asset


@dataclass
class SceneExtras:
    z_norm: np.ndarray  # target patch median == 1 (then ** depth_contrast)
    K_src: np.ndarray  # intrinsics for z_norm's own resolution
    src_w: int
    src_h: int
    target_z_raw: float
    convention: DepthConvention
    edge_rel_threshold: float
    scene_rgb: np.ndarray | None = None


def edge_rel_threshold(edge_protection: float) -> float:
    return 0.12 - 0.08 * float(edge_protection)


def check_depth_lineage(depth_asset: Asset | None, scene_asset_id) -> None:
    """The depth asset must be a ``depth`` asset derived from exactly this scene asset."""
    if depth_asset is None:
        return
    src = (depth_asset.meta_json or {}).get("source_asset_id")
    if (depth_asset.role != "depth" or scene_asset_id is None or src is None
            or str(src) != str(scene_asset_id)):
        raise ApiError(422, "depth_stale", "The depth map does not belong to the current scene image.",
                       field="config.depth_asset_id")


def _convention(meta: dict) -> DepthConvention:
    c = meta.get("convention") or {}
    try:
        return DepthConvention(kind=c["kind"], scale=float(c.get("scale", 1.0)),
                               offset=float(c.get("offset", 0.0)), eps=float(c.get("eps", 1e-6)))
    except (KeyError, TypeError, ValueError):
        raise ApiError(422, "depth_invalid", "The depth map has no valid convention.") from None


def scene_info_from_meta(scene_asset: Asset, depth_asset: Asset, cfg) -> SceneInfo:
    """``SceneInfo`` from asset metadata only; the target sits at scene z = ``depth_scale``."""
    check_depth_lineage(depth_asset, scene_asset.id)
    conv = _convention(depth_asset.meta_json or {})
    if not scene_asset.width or not scene_asset.height:
        raise ApiError(422, "asset_not_found", "Scene image has no size.")
    w, h = int(scene_asset.width), int(scene_asset.height)
    target = (cfg.target.u, cfg.target.v)
    K = intrinsics(cfg.camera.focal_length_mm_equiv, w, h, cfg.camera.sensor_width_mm_assumed)
    ray = np.linalg.inv(K) @ np.array([target[0] * w, target[1] * h, 1.0])
    ethr = edge_rel_threshold(cfg.scene.edge_protection)
    params = {"depth_scale": float(cfg.target.depth_scale), "depth_contrast": float(cfg.scene.depth_contrast),
              "edge_rel_threshold": ethr}
    return SceneInfo(K_src=K, src_w=w, src_h=h, target_world=ray * float(cfg.target.depth_scale),
                     target_uv01=target, depth_convention=conv, depth_params=params)


def load_scene_info(storage, scene_asset: Asset, depth_asset: Asset, cfg, *, max_side: int | None = None,
                    with_image: bool = False) -> tuple[SceneInfo, SceneExtras]:
    scene = scene_info_from_meta(scene_asset, depth_asset, cfg)
    meta = depth_asset.meta_json or {}
    conv = scene.depth_convention
    try:
        d = decode_depth_png16(storage.get_bytes(depth_asset.object_key), meta)
    except (ValueError, KeyError, TypeError):
        raise ApiError(422, "depth_invalid", "The depth map could not be decoded.") from None
    if d.shape != (scene.src_h, scene.src_w):
        raise ApiError(422, "depth_invalid", "The depth map size does not match the scene image.")
    K = scene.K_src
    if max_side is not None and max(d.shape) > max_side:
        f = max_side / max(d.shape)
        nw, nh = max(1, round(scene.src_w * f)), max(1, round(scene.src_h * f))
        d = cv2.resize(d, (nw, nh), interpolation=cv2.INTER_AREA)
        K = K * np.array([[nw / scene.src_w], [nh / scene.src_h], [1.0]])
    try:
        z_norm, z_raw = prepare_z(depth_to_z(d, conv), scene.target_uv01)
    except ValueError:
        raise ApiError(422, "depth_invalid", "The depth map has no usable values.") from None
    if cfg.scene.depth_contrast != 1.0:
        z_norm = np.power(z_norm, np.float32(cfg.scene.depth_contrast)).astype(np.float32)  # target stays 1
    rgb = normalize_image(storage.get_bytes(scene_asset.object_key)).rgb if with_image else None
    h, w = z_norm.shape
    return scene, SceneExtras(z_norm, K, w, h, z_raw, conv, scene.depth_params["edge_rel_threshold"], rgb)
