"""Load a project's scene + depth assets into a compiler ``SceneInfo`` (shared by compile and guide jobs)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from open_creative.camera_sketch.camera import intrinsics
from open_creative.camera_sketch.path import SceneInfo
from open_creative.camera_sketch.scene import DepthConvention, decode_depth_png16, depth_to_z, prepare_z
from open_creative.media.image import normalize_image

from ..errors import ApiError
from ..models import Asset


@dataclass
class SceneExtras:
    z_norm: np.ndarray  # HxW float32, target patch median == 1 (depth_scale applied as a power)
    K_src: np.ndarray
    src_w: int
    src_h: int
    target_z_raw: float
    convention: DepthConvention
    scene_rgb: np.ndarray | None = None


def check_depth_lineage(scene_asset: Asset | None, depth_asset: Asset | None, scene_asset_id) -> None:
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


def load_scene_info(storage, scene_asset: Asset, depth_asset: Asset, cfg, *,
                    with_image: bool = False) -> tuple[SceneInfo, SceneExtras]:
    """Returns ``(SceneInfo, extras)`` for the pinned config ``cfg`` (focal length, target)."""
    check_depth_lineage(scene_asset, depth_asset, scene_asset.id)
    meta = depth_asset.meta_json or {}
    conv = _convention(meta)
    try:
        d = decode_depth_png16(storage.get_bytes(depth_asset.object_key), meta)
    except (ValueError, KeyError, TypeError):
        raise ApiError(422, "depth_invalid", "The depth map could not be decoded.") from None
    src_h, src_w = d.shape
    if (scene_asset.width, scene_asset.height) != (src_w, src_h):
        raise ApiError(422, "depth_invalid", "The depth map size does not match the scene image.")
    target = (cfg.target.u, cfg.target.v)
    try:
        z_norm, z_raw = prepare_z(depth_to_z(d, conv), target)
    except ValueError:
        raise ApiError(422, "depth_invalid", "The depth map has no usable values.") from None
    if cfg.target.depth_scale != 1.0:
        z_norm = np.power(z_norm, np.float32(cfg.target.depth_scale)).astype(np.float32)  # target stays 1
    K = intrinsics(cfg.camera.focal_length_mm_equiv, src_w, src_h, cfg.camera.sensor_width_mm_assumed)
    ray = np.linalg.inv(K) @ np.array([target[0] * src_w, target[1] * src_h, 1.0])
    scene = SceneInfo(K_src=K, src_w=src_w, src_h=src_h, target_world=ray * 1.0, target_uv01=target,
                      depth_convention=conv)
    rgb = normalize_image(storage.get_bytes(scene_asset.object_key)).rgb if with_image else None
    return scene, SceneExtras(z_norm, K, src_w, src_h, z_raw, conv, rgb)
