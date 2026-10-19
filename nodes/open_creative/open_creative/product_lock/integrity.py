"""Protected-core integrity checks.

The preencode check compares lossless composed frames against the expected
reference produced by the same transform; it is the implementation-correctness
gate (max channel error <= 1/255). Postencode metrics are diagnostics only.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import cv2
import numpy as np

from .locked import LockedProduct, expected_reference

PREENCODE_THRESHOLD = 1


@dataclass(frozen=True)
class FrameCheck:
    index: int
    max_error: int
    core_pixels: int


def _visible_reference(locked: LockedProduct, out_w: int, out_h: int):
    ref, core = expected_reference(locked)
    fy, fx, py, px = locked.placement.visible(out_w, out_h)
    vis_core = core[py, px]
    return fy, fx, vis_core, ref[py, px][vis_core].astype(np.int16)


class CoreChecker:
    def __init__(self, locked: LockedProduct, out_w: int, out_h: int):
        self._fy, self._fx, self._core, self._ref = _visible_reference(locked, out_w, out_h)
        self._count = int(self._core.sum())
        self._max = 0
        self._frames = 0
        self._failed: list[int] = []

    def check(self, frame: np.ndarray, index: int) -> FrameCheck:
        self._frames += 1
        if self._count == 0:
            return FrameCheck(index, 0, 0)
        got = frame[self._fy, self._fx][self._core].astype(np.int16)
        err = int(np.abs(got - self._ref).max())
        self._max = max(self._max, err)
        if err > PREENCODE_THRESHOLD:
            self._failed.append(index)
        return FrameCheck(index, err, self._count)

    def report(self) -> dict:
        core_valid = self._count > 0
        return {
            "core_valid": core_valid,
            "core_pixel_count": self._count,
            "frames_checked": self._frames,
            "preencode_max_error": self._max if core_valid else None,
            "failed_frames": self._failed[:50],
            "threshold": PREENCODE_THRESHOLD,
            "passed": core_valid and self._frames > 0 and not self._failed,
        }


def _ssim(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    a, b = a.astype(np.float64), b.astype(np.float64)
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)  # noqa: E731
    mu_a, mu_b = blur(a), blur(b)
    var_a = blur(a * a) - mu_a**2
    var_b = blur(b * b) - mu_b**2
    cov = blur(a * b) - mu_a * mu_b
    return ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a**2 + mu_b**2 + c1) * (var_a + var_b + c2))


def postencode_metrics(
    decoded_frames: Iterable[np.ndarray], locked: LockedProduct, out_w: int, out_h: int
) -> dict:
    """Diagnostics of the core after lossy encoding: mean/max abs error and SSIM (luma)."""
    fy, fx, core, ref = _visible_reference(locked, out_w, out_h)
    ref_img = np.zeros((fy.stop - fy.start, fx.stop - fx.start, 3), np.uint8)
    ref_img[core] = ref.astype(np.uint8)
    ref_luma = cv2.cvtColor(ref_img, cv2.COLOR_RGB2GRAY)
    n, total_err, max_err, ssims = 0, 0.0, 0, []
    for frame in decoded_frames:
        n += 1
        if not core.any():
            continue
        crop = frame[fy, fx]
        diff = np.abs(crop[core].astype(np.int16) - ref)
        total_err += float(diff.mean())
        max_err = max(max_err, int(diff.max()))
        crop_masked = np.zeros_like(crop)
        crop_masked[core] = crop[core]
        s = _ssim(cv2.cvtColor(crop_masked, cv2.COLOR_RGB2GRAY), ref_luma)
        ssims.append(float(s[core].mean()))
    valid = n > 0 and bool(core.any())
    return {
        "frames": n,
        "mean_abs_error": total_err / n if valid else None,
        "max_error": max_err if valid else None,
        "ssim_mean": float(np.mean(ssims)) if ssims else None,
    }
