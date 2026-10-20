"""Layer assembly over the untouched source frame (spec §8 레이어 순서, §14 원본 보존).

Order: Source -> behind layers (plate, then effect) -> subject restored -> optional front glow.
- ``behind_subject``: after the behind layers, the source is re-blended inside the subject mask
  (``out = out * (1 - M) + source * M`` in linear), so the subject core is never touched.
- ``in_front``: no masking at all (``front_glow`` is not drawn).
- ``split_glow``: behind layers as in ``behind_subject``, then ``front_glow`` on top, clipped to
  the behind layers' own footprint ``dilate(alpha union > 1/1024, 2 px)`` taken before the
  subject occlusion (so the glow can sit in front of the subject there), and to ``roi_limit``.

Blending is premultiplied in linear RGB; sRGB conversion happens once at the end.
``roi = dilate(drawn alpha > 1/1024, 2 px)`` with the behind alpha after occlusion; every pixel outside it is a verbatim
copy of the source bytes (never round-tripped), which is what guarantees the outside-ROI check.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

import cv2
import numpy as np

from .color import linear_to_srgb, over_premult, srgb_to_linear
from .procedural import EffectLayer

LAYER_ORDERS = ("behind_subject", "in_front", "split_glow")
ALPHA_FLOOR = 1.0 / 1024
ROI_DILATE_PX = 2
_DISK = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * ROI_DILATE_PX + 1, 2 * ROI_DILATE_PX + 1))


@dataclass
class FrameInputs:
    source: np.ndarray  # uint8 HxWx3 sRGB
    effect: EffectLayer | None
    plate: EffectLayer | None  # already placed/retimed by the caller; drawn under ``effect``
    subject_mask: np.ndarray | None  # float32 HxW in [0, 1] at output resolution
    front_glow: EffectLayer | None  # split_glow only
    roi_limit: np.ndarray | None  # bool HxW: where the front glow is allowed


def composite_frame(inp: FrameInputs, layer_order: str) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(out uint8 HxWx3, roi bool HxW)``."""
    if layer_order not in LAYER_ORDERS:
        raise ValueError(f"unknown layer_order {layer_order!r}")
    src = np.asarray(inp.source)
    if src.dtype != np.uint8 or src.ndim != 3 or src.shape[2] != 3:
        raise ValueError("source must be uint8 HxWx3")
    h, w = src.shape[:2]
    behind = [layer for layer in (inp.plate, inp.effect) if layer is not None]
    glow = inp.front_glow if layer_order == "split_glow" else None
    for layer in [*behind, *([glow] if glow is not None else [])]:
        _check_layer(layer, w, h)
    masked = layer_order != "in_front" and inp.subject_mask is not None
    if masked and np.shape(inp.subject_mask) != (h, w):
        raise ValueError(f"subject_mask shape {np.shape(inp.subject_mask)} != {(h, w)}")
    if glow is not None and inp.roi_limit is not None and np.shape(inp.roi_limit) != (h, w):
        raise ValueError(f"roi_limit shape {np.shape(inp.roi_limit)} != {(h, w)}")

    out = src.copy()
    roi = np.zeros((h, w), bool)
    if not behind:  # the front glow lives inside the behind-effect ROI, so nothing to draw
        return out, roi
    # the glow is clipped to the dilated behind footprint and the ROI dilates once more
    box = _union_box([layer.bbox for layer in behind], 2 * ROI_DILATE_PX, w, h)
    if box is None:
        return out, roi
    x0, y0, x1, y1 = box
    sl = (slice(y0, y1), slice(x0, x1))

    src_lin = srgb_to_linear(src[sl])
    lin = src_lin
    keep = np.ones((y1 - y0, x1 - x0), np.float32)  # prod(1 - a): alpha union = 1 - keep
    for layer in behind:
        a = layer.alpha[sl]
        lin = over_premult(lin, layer.rgb_premult[sl], a)
        keep *= 1.0 - a
    a_union = 1.0 - keep
    a_eff = a_union
    if masked:
        m = np.clip(np.asarray(inp.subject_mask, np.float32)[sl], 0.0, 1.0)
        lin = lin * (1.0 - m)[..., None]  # lin is a fresh array here (over_premult returns one)
        lin += src_lin * m[..., None]  # M == 1 restores the source exactly
        a_eff = a_union * (1.0 - m)
    hit = a_eff > ALPHA_FLOOR
    if glow is not None:
        # the effect's own footprint (before occlusion): the glow may cross the subject there
        allowed = _dilate(a_union > ALPHA_FLOOR)
        if inp.roi_limit is not None:
            allowed &= np.asarray(inp.roi_limit, bool)[sl]
        ga = np.where(allowed, glow.alpha[sl], 0.0).astype(np.float32)
        grgb = np.where(allowed[..., None], glow.rgb_premult[sl], 0.0).astype(np.float32)
        lin = over_premult(lin, grgb, ga)
        hit |= ga > ALPHA_FLOOR
    roi_c = _dilate(hit)
    np.copyto(out[sl], linear_to_srgb(lin), where=roi_c[..., None])
    roi[sl] = roi_c
    return out, roi


def composite_sequence(
    frames_iter: Iterable[np.ndarray],
    per_frame_fn: Callable[[int, np.ndarray], FrameInputs],
    layer_order: str,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Lazily composite ``per_frame_fn(index, source)`` for each source frame (one frame in flight)."""
    if layer_order not in LAYER_ORDERS:  # fail at call time, not on the first next()
        raise ValueError(f"unknown layer_order {layer_order!r}")
    return (composite_frame(per_frame_fn(i, frame), layer_order) for i, frame in enumerate(frames_iter))


def _check_layer(layer: EffectLayer, w: int, h: int) -> None:
    if layer.alpha.shape != (h, w) or layer.rgb_premult.shape != (h, w, 3):
        raise ValueError(f"layer shape {layer.alpha.shape} != {(h, w)}")


def _union_box(boxes, pad: int, w: int, h: int) -> tuple[int, int, int, int] | None:
    """Union of layer bboxes (``None`` = unknown -> whole frame), padded and clipped."""
    if any(b is None for b in boxes):
        return 0, 0, w, h
    x0 = max(min(b[0] for b in boxes) - pad, 0)
    y0 = max(min(b[1] for b in boxes) - pad, 0)
    x1 = min(max(b[2] for b in boxes) + pad, w)
    y1 = min(max(b[3] for b in boxes) + pad, h)
    return (x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None


def _dilate(m: np.ndarray) -> np.ndarray:
    return cv2.dilate(m.astype(np.uint8), _DISK) > 0
