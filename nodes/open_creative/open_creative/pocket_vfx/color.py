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


def srgb_to_linear(u8: np.ndarray) -> np.ndarray:
    return _LUT[np.asarray(u8, dtype=np.uint8)]


def linear_to_srgb(f: np.ndarray) -> np.ndarray:
    return np.searchsorted(_MID, np.clip(np.asarray(f, dtype=np.float64), 0.0, 1.0)).astype(np.uint8)


def over_premult(dst_lin: np.ndarray, src_rgb_premult: np.ndarray, src_a: np.ndarray) -> np.ndarray:
    """``src + dst * (1 - a)``; ``src_a`` broadcasts over the channel axis if it is HxW."""
    a = np.asarray(src_a, dtype=np.float32)
    if a.ndim == np.ndim(dst_lin) - 1:
        a = a[..., None]
    return (src_rgb_premult + dst_lin * (1.0 - a)).astype(np.float32)
