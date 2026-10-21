"""Emissive plate -> premultiplied linear RGBA, matte QA, plate->window retime/loop mapping (spec §7)."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import numpy as np

from .color import linear_to_srgb, srgb_to_linear

_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
_BG_ALPHA = 0.05  # "background" = alpha below this
BG_STD_MAX = 0.03  # std of display-referred luma in the background
FLICKER_MAX = 0.08  # mean |d alpha| between consecutive frames
COVERAGE_MIN = 0.002  # 0.2 % of pixels
SEAM_MAX = 6.0 / 255.0
RETIME_MIN, RETIME_MAX = 0.5, 2.0


class PlateLengthError(ValueError):
    def __init__(self, plate_frames: int, window_frames: int, message: str | None = None):
        self.plate_frames = plate_frames
        self.window_frames = window_frames
        super().__init__(message or f"plate of {plate_frames} frames cannot cover a {window_frames}-frame window")


@dataclass(frozen=True)
class MatteConfig:
    threshold: float = 0.06
    knee: float = 0.04


def _smoothstep(lo: float, hi: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - lo) / max(hi - lo, 1e-9), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _matte(rgb_u8: np.ndarray, cfg: MatteConfig) -> tuple[np.ndarray, np.ndarray, float, float]:
    """-> (straight linear rgb with black level removed, alpha, bg luma std, bg luma mean)."""
    lin = srgb_to_linear(rgb_u8)
    y = lin @ _LUMA
    alpha = _smoothstep(cfg.threshold - cfg.knee, cfg.threshold + cfg.knee, y).astype(np.float32)
    bg = alpha < _BG_ALPHA
    if bg.any():
        black = np.percentile(lin[bg], 5, axis=0).astype(np.float32)
        luma = linear_to_srgb(y[bg]).astype(np.float32) / 255.0
        bg_std, bg_mean = float(luma.std()), float(luma.mean())
    else:
        black, bg_std, bg_mean = np.zeros(3, np.float32), 0.0, 0.0
    return np.maximum(lin - black, 0.0).astype(np.float32), alpha, bg_std, bg_mean


def matte_frame(rgb_u8: np.ndarray, threshold: float = 0.06, knee: float = 0.04) -> tuple[np.ndarray, np.ndarray]:
    """One sRGB uint8 frame -> (linear premultiplied RGB HxWx3, alpha HxW)."""
    straight, alpha, _, _ = _matte(rgb_u8, MatteConfig(threshold, knee))
    return straight * alpha[..., None], alpha


class MatteStats:
    """Accumulates per-frame diagnostics for ``matte_qa`` (O(1) memory)."""

    def __init__(self) -> None:
        self._bg_std = 0.0
        self._bg_means: list[float] = []
        self._flicker: list[float] = []
        self._coverage = 0.0
        self._prev: np.ndarray | None = None

    def add(self, alpha: np.ndarray, bg_std: float, bg_mean: float) -> None:
        self._bg_std = max(self._bg_std, bg_std)
        self._bg_means.append(bg_mean)
        self._coverage = max(self._coverage, float((alpha > _BG_ALPHA).mean()))
        if self._prev is not None:
            self._flicker.append(float(np.abs(alpha - self._prev).mean()))
        self._prev = alpha

    def summary(self) -> dict:
        drift = float(np.std(self._bg_means)) if len(self._bg_means) > 1 else 0.0
        return {"bg_nonuniformity": max(self._bg_std, drift),
                "flicker": float(np.mean(self._flicker)) if self._flicker else 0.0,
                "coverage": self._coverage}


def matte_sequence(frames: Iterable[np.ndarray], cfg: MatteConfig | None = None,
                   stats: MatteStats | None = None) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Stream ``(rgb_premult_lin, alpha)`` with zero-phase [1,2,1]/4 temporal alpha smoothing (edges replicate).

    Only a window of 3 frames is held; ``stats`` (if given) sees the unsmoothed alpha.
    """
    cfg = cfg or MatteConfig()
    win: deque[tuple[np.ndarray, np.ndarray]] = deque(maxlen=3)  # (straight rgb, raw alpha)

    def emit(prev, cur, nxt):
        a = (prev[1] + 2.0 * cur[1] + nxt[1]) * 0.25
        return cur[0] * a[..., None], a

    first = True
    for f in frames:
        straight, alpha, bg_std, bg_mean = _matte(np.asarray(f, dtype=np.uint8), cfg)
        if stats is not None:
            stats.add(alpha, bg_std, bg_mean)
        win.append((straight, alpha))
        if first:
            win.appendleft(win[0])  # replicate the first frame as the missing predecessor
            first = False
        if len(win) == 3:
            yield emit(win[0], win[1], win[2])
    if first:
        return
    yield emit(win[1], win[2], win[2]) if len(win) == 3 else emit(win[0], win[0], win[0])


def matte_qa(stats: dict) -> dict:
    reasons = []
    if stats["bg_nonuniformity"] > BG_STD_MAX:
        reasons.append("halo_risk")
    if stats["flicker"] > FLICKER_MAX:
        reasons.append("flicker")
    if stats["coverage"] < COVERAGE_MIN:
        reasons.append("empty_plate")
    return {"status": "check" if reasons else "ok", "bg_nonuniformity": stats["bg_nonuniformity"],
            "flicker": stats["flicker"], "coverage": stats["coverage"], "reasons": reasons}


def retime_factor(plate_frames: int, window_frames: int) -> float:
    if plate_frames < 1 or window_frames < 1:
        raise ValueError("frame counts must be >= 1")
    return plate_frames / window_frames


def retime_indices(plate_frames: int, window_frames: int) -> list[int]:
    """Nearest-frame indices stretching the whole plate over the window (factor within 0.5-2.0)."""
    f = retime_factor(plate_frames, window_frames)
    if not RETIME_MIN <= f <= RETIME_MAX:
        raise PlateLengthError(plate_frames, window_frames,
                               f"retime factor {f:.3f} outside {RETIME_MIN}-{RETIME_MAX}")
    if window_frames == 1:
        return [0]
    step = (plate_frames - 1) / (window_frames - 1)
    return [int(np.floor(k * step + 0.5)) for k in range(window_frames)]


def loop_seam_ok(first: np.ndarray, last: np.ndarray) -> bool:
    d = np.abs(np.asarray(first, np.float32) - np.asarray(last, np.float32)).mean() / 255.0
    return bool(d <= SEAM_MAX + 1e-9)


def loop_indices(plate_frames: int, window_frames: int, *, seam_ok: bool) -> list[int]:
    if not seam_ok:
        raise PlateLengthError(plate_frames, window_frames, "loop seam QA failed")
    return [k % plate_frames for k in range(window_frames)]


def plate_indices(plate_frames: int, window_frames: int, *, loopable: bool = False,
                  seam_ok: bool = False) -> list[int]:
    """Plate frame index for each window frame (R3): trim, retime, or loop."""
    if plate_frames < 1 or window_frames < 1:
        raise ValueError("frame counts must be >= 1")
    if plate_frames > 2 * window_frames:
        return list(range(window_frames))
    if window_frames <= 2 * plate_frames:
        return retime_indices(plate_frames, window_frames)
    if loopable and seam_ok:
        return loop_indices(plate_frames, window_frames, seam_ok=True)
    raise PlateLengthError(plate_frames, window_frames)
