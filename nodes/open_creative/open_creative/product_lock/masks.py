"""Product mask and protected-core computation (source pixel space)."""

from __future__ import annotations

from collections.abc import Iterable

import cv2
import numpy as np

OPAQUE_THRESHOLD = 0.999
INSIDE_THRESHOLD = 0.5
CORE_MIN_FRACTION = 0.02
DEFAULT_EROSION_PX = 3


def mask_from_alpha(alpha_u8: np.ndarray) -> np.ndarray:
    return alpha_u8.astype(np.float32) / 255.0


def rasterize_boxes(boxes: Iterable[dict], width: int, height: int) -> tuple[np.ndarray, bool]:
    """Rasterize normalized `{x,y,w,h}` boxes. Returns (mask, any_box_was_clipped)."""
    out = np.zeros((height, width), bool)
    clipped = False
    for b in boxes:
        x0, y0 = round(b["x"] * width), round(b["y"] * height)
        x1, y1 = round((b["x"] + b["w"]) * width), round((b["y"] + b["h"]) * height)
        cx0, cy0 = min(max(x0, 0), width), min(max(y0, 0), height)
        cx1, cy1 = min(max(x1, 0), width), min(max(y1, 0), height)
        if (cx0, cy0, cx1, cy1) != (x0, y0, x1, y1):
            clipped = True
        out[cy0:cy1, cx0:cx1] = True
    return out, clipped


def compute_core(
    mask: np.ndarray, user_core: np.ndarray | None, erosion_px: int = DEFAULT_EROSION_PX
) -> np.ndarray:
    """core = (eroded opaque product ∪ user-protected area) ∩ product interior."""
    opaque = (mask >= OPAQUE_THRESHOLD).astype(np.uint8)
    if erosion_px > 0:
        kernel = np.ones((2 * erosion_px + 1, 2 * erosion_px + 1), np.uint8)
        opaque = cv2.erode(opaque, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    core = opaque.astype(bool)
    if user_core is not None:
        if user_core.shape != mask.shape:
            raise ValueError("user core mask must match product mask dimensions")
        core |= user_core.astype(bool)
    return core & (mask >= INSIDE_THRESHOLD)


def core_fraction(core: np.ndarray, mask: np.ndarray) -> float:
    product_area = int((mask >= INSIDE_THRESHOLD).sum())
    if product_area == 0:
        return 0.0
    return float(core.sum()) / product_area
