"""Shared loading of the locked product for a pinned revision."""

from __future__ import annotations

import numpy as np

from open_creative.media.image import load_mask_png, normalize_image
from open_creative.product_lock.composite import LayerConfig
from open_creative.product_lock.locked import LockedProduct
from open_creative.product_lock.masks import rasterize_boxes
from open_creative.product_lock.pipeline import prepare_locked_product
from open_creative.product_lock.prompts import load_presets

from ..stages import JobRun, StageFailed


def load_locked(run: JobRun, cfg) -> LockedProduct:
    product = run.asset(cfg.product_asset_id)
    mask_asset = run.asset(cfg.mask_asset_id)
    img = normalize_image(run.ctx.storage.get_bytes(product.object_key))
    mask = load_mask_png(run.ctx.storage.get_bytes(mask_asset.object_key))
    if mask.shape != img.rgb.shape[:2]:
        raise StageFailed("mask_dimension_mismatch", "Mask size must match the product image.")
    user: np.ndarray | None = None
    if cfg.core_mask_asset_id:
        core_asset = run.asset(cfg.core_mask_asset_id)
        user = load_mask_png(run.ctx.storage.get_bytes(core_asset.object_key)) >= 0.5
        if user.shape != mask.shape:
            raise StageFailed("mask_dimension_mismatch", "Core mask size must match the product image.")
    if cfg.protect_boxes:
        boxes, _ = rasterize_boxes([b.model_dump() for b in cfg.protect_boxes], mask.shape[1], mask.shape[0])
        user = boxes if user is None else user | boxes
    t = cfg.transform
    return prepare_locked_product(img.rgb, mask, user, center_x=t.center_x, center_y=t.center_y,
                                  width_ratio=t.width_ratio, feather_px=t.feather_px, aspect=cfg.output.aspect)


def layer_config(cfg) -> LayerConfig:
    preset = load_presets()[cfg.scene.preset_id]
    return LayerConfig(brightness=cfg.lighting.background_brightness, shadow_enabled=cfg.shadow.enabled,
                       shadow_opacity=cfg.shadow.opacity, shadow_blur_px=cfg.shadow.blur_px,
                       particles=preset.particles, seed=cfg.seed)
