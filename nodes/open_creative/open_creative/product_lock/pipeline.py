"""End-to-end deterministic render: locked product → composite → core check → MP4."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..media.video import encode_mp4
from .composite import LayerConfig, composite_sequence
from .geometry import output_size, place_product
from .integrity import CoreChecker
from .locked import LockedProduct, build_locked_product
from .masks import DEFAULT_EROSION_PX, compute_core


def prepare_locked_product(
    rgb: np.ndarray,
    mask: np.ndarray,
    user_core: np.ndarray | None,
    *,
    center_x: float,
    center_y: float,
    width_ratio: float,
    feather_px: float,
    aspect: str,
    draft: bool = False,
    erosion_px: int = DEFAULT_EROSION_PX,
) -> LockedProduct:
    out_w, out_h = output_size(aspect, draft=draft)
    core = compute_core(mask, user_core, erosion_px)
    placement = place_product(rgb.shape[1], rgb.shape[0], out_w, out_h, center_x, center_y, width_ratio)
    return build_locked_product(rgb, mask, core, placement, feather_px)


@dataclass
class RenderResult:
    frames: int
    report: dict
    cover: np.ndarray


def render_to_mp4(
    backgrounds: Iterable[np.ndarray],
    locked: LockedProduct,
    cfg: LayerConfig,
    mp4_path: str | Path,
    out_w: int,
    out_h: int,
    fps: int = 24,
) -> RenderResult:
    """Every lossless frame is checked against the expected reference before encoding."""
    checker = CoreChecker(locked, out_w, out_h)
    cover: list[np.ndarray] = []

    def checked() -> Iterator[np.ndarray]:
        for i, frame in enumerate(composite_sequence(backgrounds, locked, cfg)):
            checker.check(frame, i)
            if i == 0:
                cover.append(frame.copy())
            yield frame

    n = encode_mp4(checked(), mp4_path, out_w, out_h, fps=fps)
    if not cover:
        raise ValueError("no frames rendered")
    return RenderResult(frames=n, report=checker.report(), cover=cover[0])
