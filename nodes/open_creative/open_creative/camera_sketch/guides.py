"""Control guides for the cinematic stage: fixed-range depth guide, model-shape pad/crop.

- Depth guide: one ``DepthGuideRange`` per shot (frame 0 valid z, 1st/99th percentile), never
  per-frame min-max. Inverse-depth mapping, near = white:
  ``g = clip((1/z - 1/far) / (1/near - 1/far), 0, 1) * 255`` (rounded). Invalid pixels take
  the value of the nearest valid pixel (``DEPTH_FILL_STRATEGY``, recorded in the trace).
- Model shape: frames are edge-padded (centered; extra row/col goes to the bottom/right) to
  the model's size and the last frame is repeated up to the model's frame count;
  ``crop_from_model`` takes the same centered window and the first ``n`` frames, so
  pad -> crop round-trips exactly.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import numpy as np

from .render import Frame, nearest_valid_index

DEPTH_FILL_STRATEGY = "nearest_valid_l2_distance_transform"
DEPTH_RANGE_PCT = (1.0, 99.0)


@dataclass(frozen=True)
class DepthGuideRange:
    near: float
    far: float

    def __post_init__(self) -> None:
        if not (np.isfinite(self.near) and np.isfinite(self.far) and 0 < self.near < self.far):
            raise ValueError("depth guide range needs 0 < near < far")

    @classmethod
    def from_frame(cls, frame: Frame, pct: tuple[float, float] = DEPTH_RANGE_PCT) -> DepthGuideRange:
        """Range from ``frame``'s valid z (frame 0 of the shot). A flat frame gets
        ``far = near * 1.01`` so the mapping stays defined (everything maps to white)."""
        z = frame.z[frame.valid & np.isfinite(frame.z)].astype(np.float64)
        if not z.size:
            raise ValueError("frame has no valid depth")
        near, far = (float(v) for v in np.percentile(z, pct))
        if far <= near * (1.0 + 1e-6):
            far = near * 1.01
        return cls(near, far)

    def to_dict(self) -> dict:
        return {"near": self.near, "far": self.far, "mapping": "inverse_depth_near_white",
                "percentiles": list(DEPTH_RANGE_PCT), "fill": DEPTH_FILL_STRATEGY}


def depth_guide(frame: Frame, rng: DepthGuideRange) -> tuple[np.ndarray, str]:
    """``(HxW uint8 guide, fill strategy name)`` for one frame with the shot-fixed range."""
    valid = frame.valid & np.isfinite(frame.z)
    g = np.zeros(frame.z.shape, np.float64)
    if valid.any():
        inv = 1.0 / frame.z[valid].astype(np.float64)
        lo, hi = 1.0 / rng.far, 1.0 / rng.near
        g[valid] = np.clip((inv - lo) / (hi - lo), 0.0, 1.0) * 255.0
        g = np.rint(g)
        if not valid.all():
            g = g.ravel()[nearest_valid_index(valid)]
    return g.astype(np.uint8), DEPTH_FILL_STRATEGY


def _pads(size: int, model: int) -> tuple[int, int]:
    d = model - size
    return d // 2, d - d // 2


def pad_to_model(
    frames: Iterable[np.ndarray], model_w: int, model_h: int, model_n: int
) -> Iterator[np.ndarray]:
    """Edge-pad each frame (HxW or HxWxC) to ``model_h x model_w`` and repeat the last
    frame until ``model_n`` frames were yielded. Streams."""
    last = None
    count = 0
    for f in frames:
        f = np.asarray(f)
        h, w = f.shape[:2]
        if w > model_w or h > model_h:
            raise ValueError("frame larger than the model shape")
        count += 1
        if count > model_n:
            raise ValueError("more frames than the model frame count")
        (t, b), (lft, r) = _pads(h, model_h), _pads(w, model_w)
        pad = [(t, b), (lft, r)] + [(0, 0)] * (f.ndim - 2)
        last = np.pad(f, pad, mode="edge")
        yield last
    if last is None:
        raise ValueError("no frames")
    while count < model_n:
        count += 1
        yield last


def crop_from_model(
    frames: Iterable[np.ndarray], out_w: int, out_h: int, n: int = 120
) -> Iterator[np.ndarray]:
    """Inverse of ``pad_to_model``: centered ``out_h x out_w`` crop of the first ``n`` frames.
    Raises if fewer than ``n`` frames arrive or a frame is smaller than the output."""
    count = 0
    for f in frames:
        if count >= n:
            break
        f = np.asarray(f)
        h, w = f.shape[:2]
        if w < out_w or h < out_h:
            raise ValueError("frame smaller than the output shape")
        t, lft = _pads(out_h, h)[0], _pads(out_w, w)[0]
        count += 1
        yield np.ascontiguousarray(f[t : t + out_h, lft : lft + out_w])
    if count < n:
        raise ValueError(f"expected {n} frames, got {count}")
