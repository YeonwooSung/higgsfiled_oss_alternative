"""Deterministic composite: C_t = A_T * P_T + (1 - A_T) * B_t."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import numpy as np

from .layers import ParticleKind, add_particles, adjust_brightness, apply_shadow, contact_shadow
from .locked import LockedProduct


@dataclass(frozen=True)
class LayerConfig:
    brightness: float = 0.0
    shadow_enabled: bool = True
    shadow_opacity: float = 0.28
    shadow_blur_px: float = 18
    particles: ParticleKind = "none"
    seed: int = 0


def composite_frame(
    background: np.ndarray, locked: LockedProduct, shadow: np.ndarray | None
) -> np.ndarray:
    out_h, out_w = background.shape[:2]
    out = apply_shadow(background, shadow) if shadow is not None else background.copy()
    fy, fx, py, px = locked.placement.visible(out_w, out_h)
    a = locked.alpha[py, px][..., None]
    p = locked.rgb[py, px].astype(np.float32)
    b = out[fy, fx].astype(np.float32)
    out[fy, fx] = np.clip(np.rint(a * p + (1.0 - a) * b), 0, 255).astype(np.uint8)
    return out


def composite_sequence(
    backgrounds: Iterable[np.ndarray], locked: LockedProduct, cfg: LayerConfig
) -> Iterator[np.ndarray]:
    shadow = None
    for t, bg in enumerate(backgrounds):
        if shadow is None and cfg.shadow_enabled and cfg.shadow_opacity > 0:
            shadow = contact_shadow(locked, bg.shape[1], bg.shape[0], cfg.shadow_opacity, cfg.shadow_blur_px)
        layer = adjust_brightness(bg, cfg.brightness)
        layer = add_particles(layer, t, cfg.seed, cfg.particles)
        yield composite_frame(layer, locked, shadow)
