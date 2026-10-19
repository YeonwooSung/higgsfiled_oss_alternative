"""Background-only layers. None of these may be applied after the product composite."""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from .geometry import Placement
from .locked import LockedProduct

ParticleKind = Literal["none", "bokeh", "neon"]
SHADOW_SQUASH = 0.08


def adjust_brightness(frame: np.ndarray, amount: float) -> np.ndarray:
    if not -0.2 <= amount <= 0.2:
        raise ValueError("background brightness must be within [-0.2, 0.2]")
    if amount == 0:
        return frame
    return np.clip(np.rint(frame.astype(np.float32) * (1.0 + amount)), 0, 255).astype(np.uint8)


def contact_shadow(
    locked: LockedProduct, out_w: int, out_h: int, opacity: float, blur_px: float
) -> np.ndarray:
    """Approximate floor shadow: product alpha squashed toward its base, blurred."""
    if not 0 <= opacity <= 0.6:
        raise ValueError("shadow opacity must be within [0, 0.6]")
    if not 0 <= blur_px <= 40:
        raise ValueError("shadow blur must be within [0, 40]")
    canvas = np.zeros((out_h, out_w), np.float32)
    pl = locked.placement
    sh = max(2, round(pl.h * SHADOW_SQUASH))
    squashed = cv2.resize(locked.alpha, (pl.w, sh), interpolation=cv2.INTER_AREA)
    spl = Placement(x0=pl.x0, y0=pl.y0 + pl.h - sh // 2 - 1, w=pl.w, h=sh)
    fy, fx, py, px = spl.visible(out_w, out_h)
    canvas[fy, fx] = squashed[py, px]
    if blur_px > 0:
        canvas = cv2.GaussianBlur(canvas, (0, 0), sigmaX=blur_px / 2)
        peak = canvas.max()
        if peak > 0:
            canvas /= peak
    return canvas * opacity


def apply_shadow(frame: np.ndarray, shadow: np.ndarray) -> np.ndarray:
    out = frame.astype(np.float32) * (1.0 - shadow[..., None])
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def add_particles(frame: np.ndarray, t: int, seed: int, kind: ParticleKind) -> np.ndarray:
    """Deterministic background particles; position depends only on (seed, t)."""
    if kind == "none":
        return frame
    h, w = frame.shape[:2]
    rng = np.random.default_rng(seed)
    overlay = np.zeros((h, w), np.float32)
    if kind == "bokeh":
        n = 14
        xs, ys = rng.uniform(0, w, n), rng.uniform(0, h, n)
        radii = rng.uniform(0.01, 0.035, n) * max(w, h)
        drift = rng.uniform(-0.6, 0.6, n)
        for x, y, r, d in zip(xs, ys, radii, drift):
            cx = int((x + d * t) % w)
            cy = int((y - 0.4 * t) % h)
            cv2.circle(overlay, (cx, cy), int(r), 0.18, -1, lineType=cv2.LINE_AA)
        overlay = cv2.GaussianBlur(overlay, (0, 0), sigmaX=max(w, h) * 0.006)
        tint = np.array([255, 240, 210], np.float32)
    elif kind == "neon":
        n = 3
        ys = rng.uniform(0.1, 0.5, n) * h
        phases = rng.uniform(0, 2 * np.pi, n)
        for y, ph in zip(ys, phases):
            level = 0.12 + 0.1 * np.sin(ph + t * 2 * np.pi / 120)
            cv2.line(overlay, (0, int(y)), (w - 1, int(y)), float(level), max(2, h // 200))
        overlay = cv2.GaussianBlur(overlay, (0, 0), sigmaX=max(w, h) * 0.004)
        tint = np.array([170, 110, 255], np.float32)
    else:
        raise ValueError(f"unknown particle kind {kind!r}")
    out = frame.astype(np.float32) + overlay[..., None] * tint
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)
