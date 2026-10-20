"""Procedural effect layers: Impact Wave ring + dust, Energy Aura glow + particles, Blink Out particles.

Conventions:
- Output is a full-frame premultiplied layer in LINEAR RGB (``rgb_premult`` HxWx3 float32,
  ``alpha`` HxW float32 in [0, 1]); colours come from the preset palette ramp
  edge -> mid -> core, so ``rgb_premult <= alpha * max(palette)``.
- Pixel ``(row, col)`` sits at coordinate ``(x=col, y=row)``; ``anchor_px`` uses the same
  coordinates of the output canvas. ``bbox`` is ``(x0, y0, x1, y1)`` with exclusive ends,
  the tight box of ``alpha > 0`` (alpha below ``ALPHA_FLOOR`` is zeroed), or the layer is
  ``None`` when nothing is visible.
- ``rotation_deg`` turns the Impact ellipse's major axis clockwise on screen (y down).
- Deterministic in ``(seed, frame)``: everything born once (particles, flicker waves) comes
  from ``default_rng(seed)``; per-frame jitter only from ``default_rng(seed*100003 + frame)``.
- Every sub-layer is computed inside its own clipped window (never per particle over the
  full frame) and stacked with premultiplied ``over``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .presets import load_palettes, load_presets
from .timeline import EffectWindow

ALPHA_FLOOR = 1.0 / 1024
MAX_DUST = 300
_DUST = 260
_AURA_PARTICLES = 110
_BLINK_PARTICLES = 280

# (x0, y0, rgb_premult crop, alpha crop)
_Patch = tuple[int, int, np.ndarray, np.ndarray]


@dataclass
class EffectLayer:
    rgb_premult: np.ndarray
    alpha: np.ndarray
    bbox: tuple[int, int, int, int] | None


def smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


def impact_curve(window: EffectWindow, frame: int, params: dict, short_side: float) -> tuple[float, float] | None:
    """Ring ``(radius_px, alpha)`` at ``frame``; ``None`` outside ``[peak, end]``."""
    if frame < window.peak or frame > window.end:
        return None
    s = min((frame - window.peak) / max(window.end - window.peak, 1), 1.0)
    radius = params["scale"] * 0.35 * short_side * (1.0 - (1.0 - s) ** 3)
    return radius, params["intensity"] * (1.0 - s) ** 1.5


def render_effect(
    preset_id: str,
    params: dict,
    window: EffectWindow,
    frame: int,
    anchor_px: tuple[float, float] | None,
    out_w: int,
    out_h: int,
    seed: int,
    mask: np.ndarray | None,
) -> EffectLayer | None:
    presets = load_presets()
    if preset_id not in presets:
        raise ValueError(f"unknown preset {preset_id!r}")
    p = {**presets[preset_id]["defaults"], **params}
    palettes = load_palettes()
    if p["palette"] not in palettes:
        raise ValueError(f"unknown palette {p['palette']!r}")
    pal = {k: np.asarray(v, np.float32) for k, v in palettes[p["palette"]].items()}
    pal["lut"] = _ramp_lut(pal)
    m = _binary_mask(mask, out_w, out_h) if mask is not None else None
    if preset_id == "impact_wave_v1":
        if anchor_px is None:
            raise ValueError("impact_wave needs anchor_px")
        patches = _impact(p, pal, window, frame, anchor_px, out_w, out_h, seed)
    elif preset_id == "energy_aura_v1":
        if m is None and anchor_px is None:
            raise ValueError("energy_aura needs a mask or anchor_px")
        patches = _aura(p, pal, window, frame, anchor_px, out_w, out_h, seed, m)
    else:  # blink_out_v1
        if m is None:
            raise ValueError("blink_out needs a subject mask")
        patches = _blink(p, pal, window, frame, out_w, out_h, seed, m)
    return _compose(patches, out_w, out_h)


# --------------------------------------------------------------------------- Impact Wave


def _impact(p, pal, window, frame, anchor, w, h, seed) -> list[_Patch | None]:
    if frame < window.start or frame > window.end:
        return []
    short, scale, intensity = min(w, h), p["scale"], p["intensity"]
    cx, cy = float(anchor[0]), float(anchor[1])
    r_flash = 0.13 * short * scale
    if frame < window.peak:  # charge glow ramping from start; frame `start` is already visible
        t = smoothstep((frame - window.start + 1) / (window.peak - window.start + 1))
        return [_glow(cx, cy, r_flash * (0.35 + 0.65 * t), intensity * t, t, pal, w, h)]
    # impact flash: fastest decay that still reads at 24 fps, shorter for short windows
    tau = min(max((window.end - window.peak) / 4.0, 0.75), 3.0)
    k = math.exp(-(frame - window.peak) / tau)
    patches = [_glow(cx, cy, r_flash, intensity * k, k, pal, w, h)]
    radius, ring_a = impact_curve(window, frame, p, short)
    s = min((frame - window.peak) / max(window.end - window.peak, 1), 1.0)
    squash, rot = float(p["squash"]), math.radians(float(p["rotation_deg"]))
    patches.append(_ring(cx, cy, radius, ring_a, s, short * scale, squash, rot, pal, w, h))
    patches += _impact_dust(p, window, frame, s, cx, cy, short, squash, rot, seed, pal, w, h)
    return patches


def _ring(cx, cy, radius, amp, s, size, squash, rot, pal, w, h) -> _Patch | None:
    """Ground ring: sharp leading edge, soft trailing edge, faint wide halo; hot crest."""
    if amp <= 0 or radius <= 0:
        return None
    width = 0.04 * size
    sig_out, sig_in, sig_halo = 0.35 * width, 0.85 * width, 1.5 * width
    reach = radius + 3.0 * sig_halo
    c, sn = math.cos(rot), math.sin(rot)
    hx = math.hypot(reach * c, reach * squash * sn)
    hy = math.hypot(reach * sn, reach * squash * c)
    box = _box(cx - hx, cy - hy, cx + hx, cy + hy, w, h)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    xs = np.arange(x0, x1, dtype=np.float32) - np.float32(cx)
    ys = np.arange(y0, y1, dtype=np.float32)[:, None] - np.float32(cy)
    u = xs * np.float32(c) + ys * np.float32(sn)
    v = (ys * np.float32(c) - xs * np.float32(sn)) / np.float32(squash)
    d = np.sqrt(u * u + v * v) - np.float32(radius)
    sig = np.where(d > 0, np.float32(sig_out), np.float32(sig_in))
    crest = np.exp(-((d / sig) ** 2))
    halo = np.exp(-((d / np.float32(sig_halo)) ** 2))
    alpha = np.float32(amp) * (crest + 0.18 * halo)
    heat = crest**1.5 * np.float32(0.35 + 0.65 * (1.0 - s))
    return _shade(x0, y0, alpha, heat, pal)


def _impact_dust(p, window, frame, s, cx, cy, short, squash, rot, seed, pal, w, h) -> list[_Patch | None]:
    scale, intensity = p["scale"], p["intensity"]
    rng = np.random.default_rng(seed)
    n = _DUST
    theta = rng.uniform(0.0, 2.0 * math.pi, n)
    born = 0.5 * rng.random(n) ** 2  # most dust leaves with the early, fast front
    life = rng.uniform(0.25, 0.6, n)
    follow = rng.uniform(0.8, 1.08, n)
    spread = rng.normal(0.0, 0.35, n) * 0.04 * short * scale
    lift = rng.random(n) ** 2 * 0.08 * short * scale
    bright = rng.uniform(0.35, 1.0, n)
    big = rng.random(n) < 0.25
    jitter = np.random.default_rng(seed * 100003 + frame).uniform(0.7, 1.0, n)

    age = s - born
    vis = (age >= 0) & (age < life)
    if not vis.any():
        return []
    u = np.where(vis, age / life, 1.0)
    r_birth = scale * 0.35 * short * (1.0 - (1.0 - born) ** 3)
    r_now = scale * 0.35 * short * (1.0 - (1.0 - s) ** 3)
    rho = r_birth + (r_now - r_birth) * follow + spread
    lu, lv = rho * np.cos(theta), rho * np.sin(theta) * squash
    c, sn = math.cos(rot), math.sin(rot)
    px = cx + lu * c - lv * sn
    py = cy + lu * sn + lv * c - lift * (1.0 - (1.0 - u) ** 2)
    a = np.where(
        vis,
        intensity * 0.75 * bright * (1.0 - u) ** 1.5 * np.minimum(1.0, 0.3 + 10.0 * u) * jitter,
        0.0,
    )
    heat = 0.25 + 0.6 * (1.0 - u) * bright
    sigma = max(0.8, 1.1 * scale * short / 720.0)
    return _particles(px, py, a, heat, big, sigma, pal, w, h)


# --------------------------------------------------------------------------- Energy Aura


def _aura_envelope(window: EffectWindow, frame: int) -> float:
    if frame < window.start or frame > window.end:
        return 0.0
    if frame < window.peak:
        return 0.8 * smoothstep((frame - window.start + 1) / (window.peak - window.start + 1))
    s = (frame - window.peak) / max(window.end - window.peak, 1)
    surge = 0.8 + 0.2 * math.exp(-(frame - window.peak) / 4.0)
    return surge * (1.0 - smoothstep((s - 0.65) / 0.35))


def _aura(p, pal, window, frame, anchor, w, h, seed, m) -> list[_Patch | None]:
    env = _aura_envelope(window, frame)
    if env <= 0:
        return []
    short, scale = min(w, h), p["scale"]
    size = short * scale
    rng = np.random.default_rng(seed)
    waves = _wave_params(rng, size)
    jitter = np.random.default_rng(seed * 100003 + frame)
    amp = p["intensity"] * env * float(jitter.uniform(0.88, 1.0))
    has_mask = m is not None and bool(m.any())
    if not has_mask and anchor is None:
        return []
    mask = m if has_mask else None
    patches: list[_Patch | None] = []
    if mask is not None:
        patches.append(_aura_band(mask, amp, waves, frame, size, pal, w, h))
    if anchor is not None:
        patches.append(_aura_local(float(anchor[0]), float(anchor[1]), amp, waves, frame, size, pal, w, h))
    patches += _aura_particles(p, window, frame, env, anchor, mask, rng, jitter, size, pal, w, h)
    return patches


def _contour_band(mask: np.ndarray, inner_px: int, outer_px: int) -> np.ndarray:
    """``dilate(M, outer) - erode(M, inner)`` with a Gaussian feather, full-frame float32.

    Computed inside the mask's padded bbox. (Task 10 moves this to ``masks.contour_band``.)
    """
    m = (np.asarray(mask) > 0.5).astype(np.uint8)
    out = np.zeros(m.shape, np.float32)
    box = _mask_box(m)
    if box is None:
        return out
    feather = max(1.0, (inner_px + outer_px) / 4.0)
    pad = outer_px + math.ceil(4 * feather) + 1
    x0, y0, x1, y1 = _pad_box(box, pad, m.shape[1], m.shape[0])
    crop = m[y0:y1, x0:x1]
    dil = cv2.dilate(crop, _disk(outer_px))
    ero = cv2.erode(crop, _disk(inner_px)) if inner_px > 0 else crop
    out[y0:y1, x0:x1] = cv2.GaussianBlur((dil - ero).astype(np.float32), (0, 0), feather)
    return out


def _aura_band(m, amp, waves, frame, size, pal, w, h) -> _Patch | None:
    inner = max(1, round(0.008 * size))
    outer = max(2, round(0.03 * size))
    sig_halo = 0.045 * size
    band_full = _contour_band(m, inner, outer)
    box = _mask_box(m)
    x0, y0, x1, y1 = _pad_box(box, outer + math.ceil(4 * sig_halo) + 2, w, h)
    band = band_full[y0:y1, x0:x1]
    halo = _wide_blur(band, sig_halo)
    halo = np.minimum(halo * np.float32(math.sqrt(2 * math.pi) * sig_halo / (inner + outer)), 1.0)
    # the glow never fills the silhouette: it fades to 0 within 2*inner px inside the contour
    depth = cv2.distanceTransform(m[y0:y1, x0:x1], cv2.DIST_L2, 3)
    keep = np.clip(1.0 - depth / np.float32(2 * inner), 0.0, 1.0) ** 2
    n = _wave_field(waves, x0, y0, x1, y1, frame)
    alpha = np.float32(amp) * keep * (0.85 * band * (0.55 + 0.45 * n) + 0.45 * halo * (0.4 + 0.6 * n))
    heat = np.clip(0.95 * band * (0.5 + 0.5 * n) + 0.3 * halo, 0.0, 1.0)
    return _shade(x0, y0, alpha, heat, pal)


def _wide_blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur with a large sigma, computed at 1/4 resolution (a smooth halo needs no more)."""
    hh, ww = img.shape
    if sigma < 8 or min(hh, ww) < 16:
        return cv2.GaussianBlur(img, (0, 0), sigma)
    small = cv2.resize(img, ((ww + 3) // 4, (hh + 3) // 4), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / 4.0)
    return cv2.resize(small, (ww, hh), interpolation=cv2.INTER_LINEAR)


def _aura_local(cx, cy, amp, waves, frame, size, pal, w, h) -> _Patch | None:
    r = 0.12 * size
    box = _box(cx - 3 * r, cy - 3 * r, cx + 3 * r, cy + 3 * r, w, h)
    if box is None or amp <= 0:
        return None
    x0, y0, x1, y1 = box
    d2 = _radial2(cx, cy, r, box)
    g, core = np.exp(-d2), np.exp(-d2 * 9.0)
    n = _wave_field(waves, x0, y0, x1, y1, frame)
    alpha = np.float32(amp) * (0.75 * g * (0.55 + 0.45 * n) + 0.35 * core)
    heat = np.clip(0.55 * g * n + 0.6 * core, 0.0, 1.0)
    return _shade(x0, y0, alpha, heat, pal)


def _aura_particles(p, window, frame, env, anchor, m, rng, jitter, size, pal, w, h) -> list[_Patch | None]:
    n = _AURA_PARTICLES
    theta = rng.uniform(-math.pi, math.pi, n)
    phase = rng.random(n)
    life = rng.uniform(12.0, 30.0, n)
    speed = rng.uniform(0.5, 1.3, n) * 0.006 * size
    lift = rng.uniform(0.0, 0.4, n) * 0.006 * size
    start_r = rng.uniform(0.2, 0.6, n) * 0.12 * size
    bright = rng.uniform(0.4, 1.0, n)
    big = rng.random(n) < 0.2
    flick = jitter.uniform(0.7, 1.0, n)

    age = np.mod(frame - window.start + phase * life, life)
    u = age / life
    dx, dy = np.cos(theta), np.sin(theta)
    if anchor is not None:
        px, py = anchor[0] + dx * start_r, anchor[1] + dy * start_r
    else:
        px, py = np.zeros(n), np.zeros(n)
    if m is not None:
        bx, by, ox, oy = _boundary_at(m, theta)
        from_mask = np.ones(n, bool) if anchor is None else (np.arange(n) % 3 != 0)
        px, py = np.where(from_mask, bx, px), np.where(from_mask, by, py)
        dx, dy = np.where(from_mask, ox, dx), np.where(from_mask, oy, dy)
    px = px + dx * age * speed
    py = py + dy * age * speed - age * lift
    a = p["intensity"] * env * 0.8 * bright * np.sin(math.pi * u) ** 1.5 * flick
    if m is not None:  # particles that drift back over the silhouette are dropped
        a = np.where(_inside(m, px, py), 0.0, a)
    heat = 0.1 + 0.9 * (1.0 - u)
    sigma = max(0.8, 1.0 * size / 720.0)
    return _particles(px, py, a, heat, big, sigma, pal, w, h)


def _wave_params(rng: np.random.Generator, size: float) -> np.ndarray:
    """4 travelling sine waves (kx, ky, omega, phase): seeded, temporally coherent flicker."""
    k = 4
    ang = rng.uniform(0.0, 2.0 * math.pi, k)
    wavenum = 2.0 * math.pi / (size * rng.uniform(0.12, 0.3, k))
    omega = 2.0 * math.pi / rng.uniform(10.0, 28.0, k) * rng.choice([-1.0, 1.0], k)
    phase = rng.uniform(0.0, 2.0 * math.pi, k)
    return np.stack([wavenum * np.cos(ang), wavenum * np.sin(ang), omega, phase], axis=1)


def _wave_field(waves: np.ndarray, x0, y0, x1, y1, frame) -> np.ndarray:
    """Noise in [0, 1] over a window; separable: sin(a + b) = sin a cos b + cos a sin b."""
    xs = np.arange(x0, x1, dtype=np.float64)
    ys = np.arange(y0, y1, dtype=np.float64)
    acc = np.zeros((y1 - y0, x1 - x0), np.float32)
    for kx, ky, om, ph in waves:
        ax = kx * xs + om * frame + ph
        by = ky * ys
        acc += np.outer(np.cos(by), np.sin(ax)).astype(np.float32)
        acc += np.outer(np.sin(by), np.cos(ax)).astype(np.float32)
    return np.clip(0.5 + 0.35 * acc, 0.0, 1.0)


# --------------------------------------------------------------------------- Blink Out


def _blink(p, pal, window, frame, w, h, seed, m) -> list[_Patch | None]:
    if frame < window.peak or frame > window.end or not m.any():
        return []
    size = min(w, h) * p["scale"]
    s = (frame - window.peak) / max(window.end - window.peak, 1)
    rng = np.random.default_rng(seed)
    n = _BLINK_PARTICLES
    theta = rng.uniform(-math.pi, math.pi, n)
    born = 0.7 * rng.random(n) ** 0.8
    life = rng.uniform(0.18, 0.4, n)
    travel = rng.uniform(0.015, 0.06, n) * size
    lift = rng.uniform(0.01, 0.04, n) * size
    bright = rng.uniform(0.5, 1.0, n)
    big = rng.random(n) < 0.2
    jitter = np.random.default_rng(seed * 100003 + frame).uniform(0.75, 1.0, n)

    age = s - born
    vis = (age >= 0) & (age < life)
    if not vis.any():
        return []
    u = np.where(vis, age / life, 1.0)
    bx, by, ox, oy = _boundary_at(m, theta)
    ease = 1.0 - (1.0 - u) ** 2
    px = bx + ox * travel * ease
    py = by + oy * travel * ease - lift * u
    a = np.where(
        vis,
        p["intensity"] * bright * (1.0 - u) ** 1.2 * np.minimum(1.0, 0.3 + 8.0 * u) * jitter,
        0.0,
    )
    heat = 0.3 + 0.7 * (1.0 - u)
    sigma = max(1.0, 1.25 * size / 720.0)
    return _particles(px, py, a, heat, big, sigma, pal, w, h)


# --------------------------------------------------------------------------- shared helpers


def _binary_mask(mask: np.ndarray, w: int, h: int) -> np.ndarray:
    a = np.asarray(mask)
    if a.shape != (h, w):
        raise ValueError(f"mask shape {a.shape} != {(h, w)}")
    if a.dtype == np.bool_:
        b = a
    elif a.dtype == np.uint8:
        b = a > 127
    else:
        b = a > 0.5
    return b.astype(np.uint8)


def _box(x0: float, y0: float, x1: float, y1: float, w: int, h: int) -> tuple[int, int, int, int] | None:
    """Integer window covering ``[x0, x1] x [y0, y1]`` clipped to the frame; None if empty."""
    bx0, by0 = max(math.floor(x0), 0), max(math.floor(y0), 0)
    bx1, by1 = min(math.ceil(x1) + 1, w), min(math.ceil(y1) + 1, h)
    return (bx0, by0, bx1, by1) if bx0 < bx1 and by0 < by1 else None


def _mask_box(m: np.ndarray) -> tuple[int, int, int, int] | None:
    rows, cols = np.flatnonzero(m.any(axis=1)), np.flatnonzero(m.any(axis=0))
    if rows.size == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def _pad_box(box, pad: int, w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return max(x0 - pad, 0), max(y0 - pad, 0), min(x1 + pad, w), min(y1 + pad, h)


def _disk(r: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def _radial2(cx: float, cy: float, r: float, box) -> np.ndarray:
    x0, y0, x1, y1 = box
    xs = (np.arange(x0, x1, dtype=np.float32) - np.float32(cx)) / np.float32(r)
    ys = (np.arange(y0, y1, dtype=np.float32) - np.float32(cy)) / np.float32(r)
    return ys[:, None] ** 2 + xs[None, :] ** 2


def _glow(cx, cy, r, amp, heat_amp, pal, w, h) -> _Patch | None:
    """Round emissive glow (Gaussian body + tight hot core)."""
    if amp <= 0 or r <= 0:
        return None
    box = _box(cx - 3 * r, cy - 3 * r, cx + 3 * r, cy + 3 * r, w, h)
    if box is None:
        return None
    d2 = _radial2(cx, cy, r, box)
    g, core = np.exp(-d2), np.exp(-d2 * 9.0)
    alpha = np.float32(amp) * (0.8 * g + 0.2 * core)
    heat = np.float32(heat_amp) * (0.5 * g + 0.5 * core)
    return _shade(box[0], box[1], alpha, heat, pal)


def _ramp_lut(pal: dict) -> np.ndarray:
    """256-entry palette colour for heat in [0, 1]: edge -> mid (0..0.35) -> core (0.35..1)."""
    h = np.linspace(0.0, 1.0, 256, dtype=np.float32)[:, None]
    t1 = np.clip(h / 0.35, 0.0, 1.0)
    t2 = np.clip((h - 0.35) / 0.65, 0.0, 1.0)
    lut = pal["edge"] + (pal["mid"] - pal["edge"]) * t1 + (pal["core"] - pal["mid"]) * t2
    vmax = max(float(v.max()) for v in pal.values())
    return np.clip(lut, 0.0, vmax).astype(np.float32)


def _shade(x0: int, y0: int, alpha: np.ndarray, heat: np.ndarray, pal: dict) -> _Patch:
    a = np.clip(alpha, 0.0, 1.0).astype(np.float32)
    idx = (np.clip(heat, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    rgb = pal["lut"][idx]
    rgb *= a[..., None]
    return x0, y0, rgb, a


def _particles(px, py, a, heat, big, sigma, pal, w, h) -> list[_Patch | None]:
    """Soft round particles (wide dim halo + small/big hot cores) as ONE patch over their window.

    Bilinear point splats, Gaussian-blurred per size class; each class peaks at its alpha.
    """
    keep = a > 0
    px, py, a, heat, big = px[keep], py[keep], a[keep], heat[keep], big[keep]
    if px.size == 0:
        return []
    classes = (  # (subset, sigma, alpha gain, heat gain)
        (np.ones(px.size, bool), 4.0 * sigma, 0.22, 0.6),
        (~big, sigma, 1.0, 1.0),
        (big, 2.0 * sigma, 1.0, 1.0),
    )
    pad = math.ceil(4 * 4.0 * sigma) + 2
    # the window may extend past the frame by `pad` so off-screen particles still bleed in
    gx0 = max(math.floor(px.min()) - pad, -pad)
    gy0 = max(math.floor(py.min()) - pad, -pad)
    gx1 = min(math.ceil(px.max()) + pad + 1, w + pad)
    gy1 = min(math.ceil(py.max()) + pad + 1, h + pad)
    x0, y0, x1, y1 = max(gx0, 0), max(gy0, 0), min(gx1, w), min(gy1, h)
    if gx0 >= gx1 or gy0 >= gy1 or x0 >= x1 or y0 >= y1:
        return []
    gw, gh = gx1 - gx0, gy1 - gy0
    ix, iy = np.floor(px).astype(np.int64), np.floor(py).astype(np.int64)
    fx, fy = px - ix, py - iy
    corners = (
        (0, 0, (1 - fx) * (1 - fy)),
        (1, 0, fx * (1 - fy)),
        (0, 1, (1 - fx) * fy),
        (1, 1, fx * fy),
    )
    sl = (slice(y0 - gy0, y1 - gy0), slice(x0 - gx0, x1 - gx0))
    tot_a = np.zeros((y1 - y0, x1 - x0), np.float32)
    tot_h = np.zeros_like(tot_a)
    for sub, sig, gain_a, gain_h in classes:
        if not sub.any():
            continue
        acc_a = np.zeros((gh, gw), np.float32)
        acc_h = np.zeros((gh, gw), np.float32)
        weight = a * gain_a * 2.0 * math.pi * sig * sig  # blurred impulse peak == alpha
        for ddx, ddy, wgt in corners:
            cx, cy = ix + ddx - gx0, iy + ddy - gy0
            ok = sub & (cx >= 0) & (cx < gw) & (cy >= 0) & (cy < gh)
            val = (wgt * weight)[ok]
            np.add.at(acc_a, (cy[ok], cx[ok]), val.astype(np.float32))
            np.add.at(acc_h, (cy[ok], cx[ok]), (val * heat[ok] * gain_h).astype(np.float32))
        tot_a += cv2.GaussianBlur(acc_a, (0, 0), sig)[sl]
        tot_h += cv2.GaussianBlur(acc_h, (0, 0), sig)[sl]
    return [_shade(x0, y0, tot_a, tot_h / np.maximum(tot_a, 1e-6), pal)]


def _boundary_at(m: np.ndarray, theta: np.ndarray):
    """Outer-contour point of mask ``m`` at each angle around its centroid, + outward unit dir."""
    box = _mask_box(m)
    x0, y0, x1, y1 = box
    contours, _ = cv2.findContours(np.ascontiguousarray(m[y0:y1, x0:x1]), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    pts = np.concatenate([c.reshape(-1, 2) for c in contours]).astype(np.float64) + (
        x0,
        y0,
    )
    mom = cv2.moments(m[y0:y1, x0:x1], binaryImage=True)
    cx, cy = x0 + mom["m10"] / mom["m00"], y0 + mom["m01"] / mom["m00"]
    ang = np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx)
    order = np.argsort(ang, kind="stable")
    ang, pts = ang[order], pts[order]
    idx = np.searchsorted(ang, theta) % len(ang)
    bx, by = pts[idx, 0], pts[idx, 1]
    ox, oy = bx - cx, by - cy
    norm = np.maximum(np.hypot(ox, oy), 1e-6)
    return bx, by, ox / norm, oy / norm


def _inside(m: np.ndarray, px: np.ndarray, py: np.ndarray) -> np.ndarray:
    h, w = m.shape
    ix, iy = np.round(px).astype(np.int64), np.round(py).astype(np.int64)
    ok = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
    out = np.zeros(px.shape, bool)
    out[ok] = m[iy[ok], ix[ok]] > 0
    return out


def _compose(patches: list[_Patch | None], w: int, h: int) -> EffectLayer | None:
    """Stack patches bottom-to-top with premultiplied ``over`` into a full-frame layer."""
    patches = [q for q in patches if q is not None]
    if not patches:
        return None
    rgb = np.zeros((h, w, 3), np.float32)
    alpha = np.zeros((h, w), np.float32)
    ux0 = uy0 = 1 << 30
    ux1 = uy1 = -1
    for x0, y0, prgb, pa in patches:
        ph, pw = pa.shape
        sl = (slice(y0, y0 + ph), slice(x0, x0 + pw))
        rgb[sl] = prgb + rgb[sl] * (1.0 - pa[..., None])
        alpha[sl] = pa + alpha[sl] * (1.0 - pa)
        ux0, uy0, ux1, uy1 = (
            min(ux0, x0),
            min(uy0, y0),
            max(ux1, x0 + pw),
            max(uy1, y0 + ph),
        )
    region = (slice(uy0, uy1), slice(ux0, ux1))
    ra, rr = alpha[region], rgb[region]
    dead = ra < ALPHA_FLOOR
    ra[dead] = 0.0
    rr[dead] = 0.0
    rows, cols = np.flatnonzero(ra.any(axis=1)), np.flatnonzero(ra.any(axis=0))
    if rows.size == 0:
        return None
    bbox = (
        ux0 + int(cols[0]),
        uy0 + int(rows[0]),
        ux0 + int(cols[-1]) + 1,
        uy0 + int(rows[-1]) + 1,
    )
    return EffectLayer(rgb_premult=rgb, alpha=alpha, bbox=bbox)
