"""Load a project's scene + depth assets into a compiler ``SceneInfo`` (shared by compile and guide jobs).

Which to use:
- ``scene_info_from_meta``: metadata only (no pixel decoding). Compile endpoint -> ``compile_path``.
- ``load_scene_info(..., max_side=None)``: full-resolution depth -> ``prepare_z`` -> ``z_norm``; use this for
  guide/cinematic jobs (Task 8/10) that render at full size.
- ``load_scene_info(..., max_side=N)``: depth downsampled (area) to N px long side BEFORE ``prepare_z``;
  an estimate-only path (hole-ratio preview) for older depth assets without a ``depth_lowres`` copy.
- ``load_estimate_extras``: the compile endpoint's hole estimate. Reads only the ``depth_lowres`` asset recorded
  in the depth meta (``lowres_asset_id``; same downsample as ``max_side=LOWRES_DEPTH_MAX_SIDE``) and keeps the
  prepared z in a small per-process LRU; falls back to the full-resolution decode when there is none.
Editor fields: ``target.depth_scale`` scales the target only (``target_world = ray * depth_scale``);
``scene.depth_contrast`` is ``z_norm ** contrast`` (target stays 1); ``scene.edge_protection`` e maps to
``edge_rel_threshold = 0.12 - 0.08 e`` (0.5 -> 0.08), exposed in the extras for mesh building.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from open_creative.camera_sketch.camera import intrinsics
from open_creative.camera_sketch.path import SceneInfo
from open_creative.camera_sketch.scene import (
    LOWRES_DEPTH_MAX_SIDE,
    DepthConvention,
    decode_depth_png16,
    depth_to_z,
    downsample_depth,
    lowres_depth_size,
    prepare_z,
)
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


def _prepared_z(d: np.ndarray, conv: DepthConvention, target_uv01, depth_contrast: float) -> tuple[np.ndarray, float]:
    try:
        z_norm, z_raw = prepare_z(depth_to_z(d, conv), target_uv01)
    except ValueError:
        raise ApiError(422, "depth_invalid", "The depth map has no usable values.") from None
    if depth_contrast != 1.0:
        z_norm = np.power(z_norm, np.float32(depth_contrast)).astype(np.float32)  # target stays 1
    return z_norm, z_raw


def _scaled_K(scene: SceneInfo, w: int, h: int) -> np.ndarray:
    return scene.K_src * np.array([[w / scene.src_w], [h / scene.src_h], [1.0]])


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
        d = downsample_depth(d, max_side)
        K = _scaled_K(scene, d.shape[1], d.shape[0])
    z_norm, z_raw = _prepared_z(d, conv, scene.target_uv01, float(cfg.scene.depth_contrast))
    rgb = normalize_image(storage.get_bytes(scene_asset.object_key)).rgb if with_image else None
    h, w = z_norm.shape
    return scene, SceneExtras(z_norm, K, w, h, z_raw, conv, scene.depth_params["edge_rel_threshold"], rgb)


# -- estimate-only low-resolution path (compile endpoint) ------------------------------------------------------

LOWRES_CACHE_SIZE = 32
_lowres_cache: OrderedDict[tuple, tuple[np.ndarray, float]] = OrderedDict()
_lowres_lock = threading.Lock()


def clear_lowres_cache() -> None:
    with _lowres_lock:
        _lowres_cache.clear()


def lowres_depth_asset(db, depth_asset: Asset) -> Asset | None:
    """The ready ``depth_lowres`` asset recorded in the depth meta, or None (older depth assets)."""
    lid = (depth_asset.meta_json or {}).get("lowres_asset_id")
    try:
        a = db.get(Asset, uuid.UUID(str(lid))) if lid else None
    except ValueError:
        return None
    if (a is None or a.project_id != depth_asset.project_id or a.role != "depth_lowres" or a.state != "ready"
            or str((a.meta_json or {}).get("source_depth_asset_id")) != str(depth_asset.id)):
        return None
    return a


def load_estimate_extras(storage, scene: SceneInfo, depth_asset: Asset, lowres_asset: Asset | None,
                         scene_asset: Asset, cfg) -> SceneExtras:
    """Low-resolution prepared z for the hole estimate (long side <= ``LOWRES_DEPTH_MAX_SIDE``).

    With a ``depth_lowres`` asset only that asset is read, and the prepared z is cached per
    (lowres_asset_id, target u/v, depth_scale, depth_contrast, edge_protection); without one the full-resolution
    depth is decoded and downsampled (same result up to png16 quantization).
    """
    conv, ethr = scene.depth_convention, scene.depth_params["edge_rel_threshold"]
    if lowres_asset is None:
        _, extras = load_scene_info(storage, scene_asset, depth_asset, cfg, max_side=LOWRES_DEPTH_MAX_SIDE)
        return extras
    meta = lowres_asset.meta_json or {}
    if str(meta.get("source_asset_id")) != str(scene_asset.id):
        raise ApiError(422, "depth_stale", "The depth map does not belong to the current scene image.",
                       field="config.depth_asset_id")
    key = (str(lowres_asset.id), float(cfg.target.u), float(cfg.target.v), float(cfg.target.depth_scale),
           float(cfg.scene.depth_contrast), float(cfg.scene.edge_protection))
    with _lowres_lock:
        hit = _lowres_cache.get(key)
        if hit is not None:
            _lowres_cache.move_to_end(key)
    if hit is None:
        try:
            d = decode_depth_png16(storage.get_bytes(lowres_asset.object_key), meta)
        except (ValueError, KeyError, TypeError):
            raise ApiError(422, "depth_invalid", "The depth map could not be decoded.") from None
        w, h = lowres_depth_size(scene.src_w, scene.src_h, LOWRES_DEPTH_MAX_SIDE)
        if d.shape != (h, w):
            raise ApiError(422, "depth_invalid", "The depth map size does not match the scene image.")
        hit = _prepared_z(d, conv, scene.target_uv01, float(cfg.scene.depth_contrast))
        with _lowres_lock:
            _lowres_cache[key] = hit
            _lowres_cache.move_to_end(key)
            while len(_lowres_cache) > LOWRES_CACHE_SIZE:
                _lowres_cache.popitem(last=False)
    z_norm, z_raw = hit
    h, w = z_norm.shape
    return SceneExtras(z_norm, _scaled_K(scene, w, h), w, h, z_raw, conv, ethr)
