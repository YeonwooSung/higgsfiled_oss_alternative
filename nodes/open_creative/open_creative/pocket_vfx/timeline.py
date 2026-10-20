"""Frame mapping and effect timing. The single Python definition of ``frame_index``."""

from __future__ import annotations

import math
from dataclasses import dataclass

FPS = 24


def frame_index(t_ms: float) -> int:
    """Half-up rounding of ``t_ms`` to a 24 fps frame index."""
    return math.floor(t_ms * FPS / 1000 + 0.5)


def frame_count(duration_ms: float) -> int:
    return frame_index(duration_ms)


@dataclass(frozen=True)
class EffectWindow:
    start: int
    peak: int
    end: int


def effect_window(effect: dict, duration_ms: float) -> EffectWindow:
    """Frame indices of start/peak/end, clamped to the last valid frame ``N - 1``."""
    last = max(frame_count(duration_ms) - 1, 0)
    return EffectWindow(*(min(max(frame_index(effect[k]), 0), last) for k in ("start_ms", "peak_ms", "end_ms")))


def timing_errors(effect: dict, duration_ms: float) -> list[dict]:
    """Violations of ``0 <= start <= peak <= end <= duration``: ``[{code, field}]``."""
    s, p, e = effect["start_ms"], effect["peak_ms"], effect["end_ms"]
    errors: list[dict] = []
    if s > p:
        errors.append({"code": "timing_order", "field": "start_ms"})
    if p > e:
        errors.append({"code": "timing_order", "field": "peak_ms"})
    if s < 0:
        errors.append({"code": "timing_range", "field": "start_ms"})
    if e > duration_ms:
        errors.append({"code": "timing_range", "field": "end_ms"})
    return errors
