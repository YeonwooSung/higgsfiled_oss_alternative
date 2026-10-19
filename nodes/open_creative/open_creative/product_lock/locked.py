"""The locked product: original pixels resampled once into output space.

Resampling is premultiplied (rgb*alpha and alpha are resized separately, then
un-premultiplied) to avoid light/dark fringes from transparent source pixels.
The protected core keeps alpha = 1 so composite output equals `rgb` there.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .geometry import Placement
from .masks import INSIDE_THRESHOLD


@dataclass(frozen=True)
class LockedProduct:
    rgb: np.ndarray  # uint8 (h, w, 3), un-premultiplied
    alpha: np.ndarray  # float32 (h, w), 1.0 on core
    core: np.ndarray  # bool (h, w)
    placement: Placement


def build_locked_product(
    rgb: np.ndarray,
    mask: np.ndarray,
    core: np.ndarray,
    placement: Placement,
    feather_px: float = 0.0,
) -> LockedProduct:
    if rgb.shape[:2] != mask.shape or mask.shape != core.shape:
        raise ValueError("rgb, mask and core must share dimensions")
    if not 0 <= feather_px <= 4:
        raise ValueError("feather_px must be within [0, 4]")
    size = (placement.w, placement.h)
    interp = cv2.INTER_AREA if placement.w < rgb.shape[1] else cv2.INTER_LINEAR

    mask = np.clip(mask.astype(np.float32), 0.0, 1.0)
    premul = rgb.astype(np.float32) * mask[..., None]
    premul_t = cv2.resize(premul, size, interpolation=interp)
    alpha_t = np.clip(cv2.resize(mask, size, interpolation=interp), 0.0, 1.0)

    safe = np.where(alpha_t > 1e-6, alpha_t, 1.0)[..., None]
    rgb_t = np.where(alpha_t[..., None] > 1e-6, premul_t / safe, 0.0)
    rgb_t = np.clip(np.rint(rgb_t), 0, 255).astype(np.uint8)

    core_t = cv2.resize(core.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST).astype(bool)
    core_t &= alpha_t >= INSIDE_THRESHOLD

    if feather_px > 0:
        blurred = cv2.GaussianBlur(alpha_t, (0, 0), sigmaX=feather_px)
        alpha_t = np.minimum(alpha_t, blurred)
    alpha_t = alpha_t.astype(np.float32)
    alpha_t[core_t] = 1.0
    return LockedProduct(rgb=rgb_t, alpha=alpha_t, core=core_t, placement=placement)


def expected_reference(locked: LockedProduct) -> tuple[np.ndarray, np.ndarray]:
    """Reference pixels (rgb, core) that every composed frame must reproduce on the core."""
    return locked.rgb, locked.core
