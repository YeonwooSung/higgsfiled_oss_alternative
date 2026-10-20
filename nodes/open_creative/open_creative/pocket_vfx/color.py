"""sRGB <-> linear LUTs and premultiplied-alpha ``over`` (blend in linear, convert at the end)."""

from __future__ import annotations

import numpy as np


def _build() -> tuple[np.ndarray, np.ndarray]:
    c = np.arange(256, dtype=np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return lin.astype(np.float32), lin


_LUT, _LUT64 = _build()
# Midpoints between consecutive linear values: nearest-neighbour inverse (exact round trip).
_MID = (_LUT64[:-1] + _LUT64[1:]) / 2.0
# Fast exact inverse: bins of 1/4096 are narrower than the smallest gap between midpoints
# (~3e-4 near black), so each bin holds at most one midpoint. ``_BIN_CODE[b]`` = number of
# midpoints below the bin start; one comparison against the next midpoint finishes the job.
_BINS = 4096
_BIN_CODE = np.searchsorted(_MID, np.arange(_BINS + 1, dtype=np.float64) / _BINS, side="left").astype(np.uint8)
_NEXT_MID = np.append(_MID, np.inf)


def srgb_to_linear(u8: np.ndarray) -> np.ndarray:
    return _LUT[np.asarray(u8, dtype=np.uint8)]


def linear_to_srgb(f: np.ndarray) -> np.ndarray:
    """Nearest sRGB code (== ``searchsorted(_MID, clip(f, 0, 1))``) without a per-pixel binary search."""
    x = np.asarray(f)
    if not np.issubdtype(x.dtype, np.floating):
        x = x.astype(np.float64)
    x = np.clip(x, 0.0, 1.0)
    code = _BIN_CODE[(x * _BINS).astype(np.intp)]
    return code + (x > _NEXT_MID[code])


def over_premult(dst_lin: np.ndarray, src_rgb_premult: np.ndarray, src_a: np.ndarray) -> np.ndarray:
    """``src + dst * (1 - a)``; ``src_a`` broadcasts over the channel axis if it is HxW."""
    a = np.asarray(src_a, dtype=np.float32)
    if a.ndim == np.ndim(dst_lin) - 1:
        a = a[..., None]
    return (src_rgb_premult + dst_lin * (1.0 - a)).astype(np.float32)
