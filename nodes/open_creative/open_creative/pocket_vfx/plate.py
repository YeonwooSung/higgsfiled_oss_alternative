"""Effect plates (spec §7, §8; Ruling R3): plate shapes, the procedural emissive plate generator (bundled MIT plates
and the mock ``vfx_plate_v1``), streamed matte access by plate index, and placement on the output canvas.

Placement: the matted plate (premultiplied linear RGB + alpha, plate resolution) is cover-fitted into a square of side
``scale * 0.9 * short side`` centred on the anchor (aspect kept, never stretched), clipped to the frame, and scaled by
``intensity * plate_envelope``. Impact Wave attenuates the plate's lower half (the front semicircle) with a vertical
soft gradient so the dust reads as behind the ring; Energy Aura multiplies the plate alpha by its ROI (contour band of
the subject mask union a region around the anchor). The result is a behind layer for ``composite_frame``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from pathlib import Path

import cv2
import numpy as np

from ..media.video import cover_fit
from .color import linear_to_srgb
from .masks import contour_band
from .matte import MatteConfig, matte_sequence
from .presets import presets_path
from .procedural import ALPHA_FLOOR, EffectLayer, smoothstep
from .timeline import EffectWindow

GENERATED_SIZES = {"16:9": (768, 448), "9:16": (448, 768)}  # LTX: multiples of 32
BUNDLED_SIZES = GENERATED_SIZES
BUNDLED_FRAMES = 49
MIN_GENERATED_FRAMES = 9
PLATE_SIDE = 0.9  # square side = scale * PLATE_SIDE * output short side
PLATE_GAIN = 0.9
IMPACT_FRONT_FLOOR = 0.15  # plate weight left in the front (lower) half of an Impact Wave
_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
STYLES = ("dust", "wisps", "particles")
BLACK_CUT = 0.03  # linear emission below this is background (the matte's alpha < 0.05 region)


def window_frames(window: EffectWindow) -> int:
    return window.end - window.start + 1


def generated_plate_frames(window_frames: int) -> int:
    """Smallest LTX-legal length ``8k + 1`` covering the window, at least 9 frames (Ruling R3)."""
    return max(MIN_GENERATED_FRAMES, 8 * math.ceil((max(window_frames, 1) - 1) / 8) + 1)


def plate_prompt(preset: dict, palette: str) -> str:
    return preset["plate_prompt"].replace(preset.get("prompt_palette_placeholder") or "{palette}", palette)


def bundled_entry(preset: dict, bundled_id: str | None) -> dict | None:
    """The preset's bundled plate ``bundled_id`` (None: the preset default, its first plate); None without plates."""
    plates = preset.get("bundled_plates") or []
    if bundled_id is None:
        return plates[0] if plates else None
    return next((p for p in plates if p["id"] == bundled_id), None)


def bundled_path(entry: dict, portrait: bool) -> Path:
    """Repo file of a bundled plate (paths come from the presets JSON, never from user input)."""
    return presets_path().parent / entry["file_portrait" if portrait else "file"]


# --------------------------------------------------------------------------- procedural emissive plates


def _splat(canvas: np.ndarray, x: np.ndarray, y: np.ndarray, r: np.ndarray, v: np.ndarray) -> None:
    for xi, yi, ri, vi in zip(x, y, r, v, strict=True):
        if vi > 1e-3:
            cv2.circle(canvas, (round(xi * 16), round(yi * 16)), max(1, round(ri * 16)), float(vi), -1,
                       cv2.LINE_AA, 4)


def _age(phase: float, birth: np.ndarray, life: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Particle age in [0, 1) and visibility for a cycle position ``phase`` (periodic: frame P == frame 0)."""
    age = np.mod(phase - birth, 1.0) / life
    return age, age < 1.0


def _dust(rng, w, h, frames) -> Callable[[int], np.ndarray]:
    u, cx, cy = min(w, h), w / 2, h / 2
    n = 140
    theta = rng.uniform(-math.pi, 0.0, n)  # mostly upward (screen y down)
    speed = rng.uniform(0.15, 0.42, n) * u
    birth, life = rng.random(n), rng.uniform(0.35, 0.8, n)
    size = rng.uniform(0.006, 0.02, n) * u
    bright = rng.uniform(0.25, 0.9, n)
    spark = rng.random(n) < 0.3

    def frame(t: int) -> np.ndarray:
        canvas = np.zeros((h, w), np.float32)
        age, vis = _age(t / frames, birth, life)
        ease = 1.0 - (1.0 - np.clip(age, 0, 1)) ** 2
        x = cx + np.cos(theta) * speed * ease
        y = cy + np.sin(theta) * speed * ease + 0.12 * u * age ** 2
        fade = np.sin(math.pi * np.clip(age, 0, 1)) ** 1.5
        r = np.where(spark, 0.35 * size, size * (0.6 + 1.4 * age))
        _splat(canvas, x[vis], y[vis], r[vis], (bright * fade * np.where(spark, 1.3, 0.6))[vis])
        return canvas

    return frame


def _wisps(rng, w, h, frames) -> Callable[[int], np.ndarray]:
    u, cx, cy = min(w, h), w / 2, h / 2
    k = 7
    r0 = rng.uniform(0.14, 0.32, k) * u
    amp = rng.uniform(0.02, 0.06, k) * u
    lobes = rng.integers(2, 5, k)
    turns = rng.choice([-1, 1], k) * rng.integers(1, 3, k)  # whole turns per cycle: periodic
    start = rng.uniform(0, 2 * math.pi, k)
    span = rng.uniform(0.6, 1.6, k)
    bright = rng.uniform(0.5, 1.0, k)
    s = np.linspace(0.0, 1.0, 64)

    def frame(t: int) -> np.ndarray:
        canvas = np.zeros((h, w), np.float32)
        ph = 2 * math.pi * t / frames
        for i in range(k):
            a = start[i] + turns[i] * ph + span[i] * s
            rr = r0[i] + amp[i] * np.sin(lobes[i] * a + 3 * ph)
            pts = np.stack([cx + rr * np.cos(a), cy + rr * np.sin(a)], axis=1)
            v = float(bright[i] * (0.65 + 0.35 * math.sin(ph * 2 + i)))
            cv2.polylines(canvas, [np.round(pts * 16).astype(np.int32)], False, v, 2, cv2.LINE_AA, 4)
        return canvas

    return frame


def _particles(rng, w, h, frames) -> Callable[[int], np.ndarray]:
    u, cx, cy = min(w, h), w / 2, h / 2
    n = 180
    rad = np.sqrt(rng.random(n)) * 0.33 * u
    ang = rng.uniform(0, 2 * math.pi, n)
    x0, y0 = cx + rad * np.cos(ang), cy + rad * np.sin(ang)
    drift = rng.uniform(0.04, 0.16, n) * u
    sway = rng.uniform(-0.03, 0.03, n) * u
    birth, life = rng.random(n), rng.uniform(0.3, 0.7, n)
    size = rng.uniform(0.003, 0.009, n) * u
    bright = rng.uniform(0.4, 1.0, n)

    def frame(t: int) -> np.ndarray:
        canvas = np.zeros((h, w), np.float32)
        age, vis = _age(t / frames, birth, life)
        x = x0 + sway * np.sin(math.pi * age)
        y = y0 - drift * age
        fade = np.sin(math.pi * np.clip(age, 0, 1))
        _splat(canvas, x[vis], y[vis], size[vis], (bright * fade)[vis])
        return canvas

    return frame


_GENERATORS = {"dust": _dust, "wisps": _wisps, "particles": _particles}


def palette_tint(lum: np.ndarray, palette: dict) -> np.ndarray:
    """Linear luminance (HxW, straight or premultiplied) -> linear RGB ``lum * lerp(mid, core, heat)``."""
    mid, core = np.asarray(palette["mid"], np.float32), np.asarray(palette["core"], np.float32)
    heat = np.clip(lum, 0.0, 1.0)[..., None]
    return (lum[..., None] * (mid + (core - mid) * heat)).astype(np.float32)


def procedural_plate(style: str, seed: int, w: int, h: int, frames: int,
                     palette: dict | None = None) -> Iterator[np.ndarray]:
    """Deterministic emissive plate on pure black (sRGB uint8 HxWx3 frames), periodic over ``frames`` so a loop
    seam is one ordinary frame step. Gray unless ``palette`` (linear ``mid``/``core``) tints it."""
    if style not in _GENERATORS:
        raise ValueError(f"unknown plate style {style!r}")
    draw = _GENERATORS[style](np.random.default_rng(seed), w, h, frames)
    for t in range(frames):
        canvas = draw(t)
        glow = canvas + 0.9 * cv2.GaussianBlur(canvas, (0, 0), 0.012 * min(w, h))
        lum = 1.0 - np.exp(-1.8 * glow)  # linear emission in [0, 1)
        # cut the faint glow tails to pure black: an emissive plate on an untextured black background
        lum = (np.maximum(lum - BLACK_CUT, 0.0) / (1.0 - BLACK_CUT)).astype(np.float32)
        rgb = palette_tint(lum, palette) if palette is not None else np.repeat(lum[..., None], 3, axis=2)
        yield linear_to_srgb(rgb).astype(np.uint8)


# --------------------------------------------------------------------------- matte access + placement


class PlateFrames:
    """Matted plate frames by plate index, streamed from ``open_frames()`` (one frame decoded at a time). Indices
    usually increase (trim/retime); a smaller index (loop wrap) restarts the decode."""

    def __init__(self, open_frames: Callable[[], Iterator[np.ndarray]], cfg: MatteConfig):
        self._open, self._cfg = open_frames, cfg
        self._it: Iterator | None = None
        self._idx, self._cur = -1, None

    def get(self, idx: int) -> tuple[np.ndarray, np.ndarray]:
        if self._it is None or idx < self._idx:
            self._it, self._idx = iter(matte_sequence(self._open(), self._cfg)), -1
        while self._idx < idx:
            try:
                self._cur = next(self._it)
            except StopIteration:
                raise ValueError(f"the plate has no frame {idx}") from None
            self._idx += 1
        return self._cur


def plate_envelope(window: EffectWindow, frame: int) -> float:
    """Plate strength over the effect window: ramps in to the peak, fades out over the last 40 % after it."""
    if frame < window.start or frame > window.end:
        return 0.0
    if frame < window.peak:
        return smoothstep((frame - window.start + 1) / (window.peak - window.start + 1))
    s = (frame - window.peak) / max(window.end - window.peak, 1)
    return 1.0 - smoothstep((s - 0.6) / 0.4)


def impact_weight(side: int) -> np.ndarray:
    """Vertical soft gradient over the placed square: 1 in the upper half, ``IMPACT_FRONT_FLOOR`` low in the front."""
    t = (np.arange(side, dtype=np.float32) + 0.5) / side * 2.0 - 1.0  # -1 top .. 1 bottom, 0 = anchor row
    ramp = np.clip((t + 0.1) / 0.5, 0.0, 1.0)
    w = 1.0 - (1.0 - IMPACT_FRONT_FLOOR) * ramp * ramp * (3.0 - 2.0 * ramp)
    return w[:, None].astype(np.float32)


def place_plate(rgb_premult: np.ndarray, alpha: np.ndarray, center: tuple[float, float], side: float,
                out_w: int, out_h: int, gain: float = 1.0, weight: np.ndarray | None = None) -> EffectLayer | None:
    """Cover-fit the plate into a ``side`` square centred on ``center`` (output px), clipped to the frame."""
    s = max(2, round(side))
    if gain <= 0:
        return None
    patch = cover_fit(np.dstack([rgb_premult, alpha]).astype(np.float32), s, s) * np.float32(gain)
    if weight is not None:
        patch *= weight[..., None] if weight.ndim == 2 else weight
    x0, y0 = round(center[0] - s / 2), round(center[1] - s / 2)
    cx0, cy0, cx1, cy1 = max(x0, 0), max(y0, 0), min(x0 + s, out_w), min(y0 + s, out_h)
    if cx0 >= cx1 or cy0 >= cy1:
        return None
    rgb = np.zeros((out_h, out_w, 3), np.float32)
    a = np.zeros((out_h, out_w), np.float32)
    crop = patch[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]
    rgb[cy0:cy1, cx0:cx1] = crop[..., :3]
    a[cy0:cy1, cx0:cx1] = np.clip(crop[..., 3], 0.0, 1.0)
    return _finish(rgb, a)


def restrict(layer: EffectLayer | None, roi: np.ndarray) -> EffectLayer | None:
    """Multiply a layer (premultiplied: colour and alpha alike) by a full-frame weight in [0, 1]."""
    if layer is None:
        return None
    return _finish(layer.rgb_premult * roi[..., None], layer.alpha * roi)


def aura_roi(subject: np.ndarray | None, anchor: tuple[float, float] | None, size: float, w: int,
             h: int) -> np.ndarray:
    """Energy Aura plate ROI in [0, 1]: the subject's contour band (wider than the procedural band) union a soft
    disc around the anchor (spec §8: plate limited to the band and the anchor-local region)."""
    roi = np.zeros((h, w), np.float32)
    if subject is not None and (subject > 0.5).any():
        band = contour_band(subject > 0.5, max(1, round(0.01 * size)), max(2, round(0.06 * size)))
        peak = float(band.max())
        if peak > 0:
            roi = np.minimum(band / peak, 1.0).astype(np.float32)
    if anchor is not None:
        r = 0.2 * size
        x0, y0 = max(int(anchor[0] - 3 * r), 0), max(int(anchor[1] - 3 * r), 0)
        x1, y1 = min(int(anchor[0] + 3 * r) + 1, w), min(int(anchor[1] + 3 * r) + 1, h)
        if x0 < x1 and y0 < y1:
            yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            disc = np.exp(-((xx - anchor[0]) ** 2 + (yy - anchor[1]) ** 2) / (2 * r * r)).astype(np.float32)
            roi[y0:y1, x0:x1] = np.maximum(roi[y0:y1, x0:x1], disc)
    return roi


def tint_matte(rgb_premult: np.ndarray, alpha: np.ndarray, palette: dict) -> np.ndarray:
    """Bundled (gray emissive) plate -> palette colour: premultiplied linear luma times ``lerp(mid, core, heat)``
    with heat = straight luma, so the result stays premultiplied (<= alpha)."""
    lum = rgb_premult @ _LUMA
    heat = np.clip(lum / np.maximum(alpha, 1e-6), 0.0, 1.0)
    mid, core = np.asarray(palette["mid"], np.float32), np.asarray(palette["core"], np.float32)
    return (lum[..., None] * (mid + (core - mid) * heat[..., None])).astype(np.float32)


def _finish(rgb: np.ndarray, a: np.ndarray) -> EffectLayer | None:
    keep = a >= ALPHA_FLOOR
    if not keep.any():
        return None
    a = np.where(keep, a, 0.0).astype(np.float32)
    rgb = np.where(keep[..., None], rgb, 0.0).astype(np.float32)
    ys, xs = np.nonzero(keep)
    return EffectLayer(rgb, a, (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
