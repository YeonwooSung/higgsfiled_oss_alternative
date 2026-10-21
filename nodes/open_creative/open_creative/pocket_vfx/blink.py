"""Blink Out with a user clean plate (spec §8 Blink Out): the subject dissolves into the empty-background photo.

The background revealed inside the subject is the user's own photo of the empty scene (the clean plate), never a
restored or generated background. It only works with a fixed camera and a plate of the same viewpoint:

- ``load_clean_plate``: the inspected (EXIF-normalized) image, cover-fit to the output canvas with the same
  ``cover_fit`` the work clip went through, so a plate shot at the source resolution/orientation lines up.
- ``clean_plate_alignment``: ORB features of the plate and of sampled work-clip frames, on the background only
  (outside the dilated subject mask), matched and fitted with a RANSAC homography plate -> frame. The error of a
  frame is the median displacement ``|H(p) - p|`` over the inlier background points, in output px. ``ok`` only when
  every sampled frame has >= ``ALIGN_MIN_INLIERS`` inliers and its error is <= ``ALIGN_MAX_ERR_PX``; too few
  inliers on any frame is ``unknown`` (never a pass); a camera the analysis did not find ``fixed`` is a mismatch
  without trying.
- ``dissolve_alpha``: a seeded, temporally fixed noise field (equalized to uniform in [0, 1]) is thresholded by the
  progress ``t`` (0 at the peak, 1 at the end): a pixel turns into the plate once the threshold passes its noise
  value, over a soft ``DISSOLVE_SOFTNESS`` ramp. ``t = 0`` gives exactly 0 and ``t = 1`` exactly 1, so frames up to
  the peak are untouched and from the end on the subject area is the clean plate. The subject mask is dilated by
  ``DISSOLVE_DILATE_REL`` of the short side first so the mask's soft rim does not leave a ghost outline.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from ..media.video import cover_fit
from .masks import disk_kernel
from .timeline import EffectWindow

ALIGN_MAX_ERR_PX = 2.0  # output px: median background displacement plate -> frame that still counts as aligned
ALIGN_MIN_INLIERS = 12
ALIGN_SAMPLES = 6  # work-clip frames checked (evenly spaced, first and last included)
ALIGN_RANSAC_PX = 3.0
ALIGN_MASK_DILATE_REL = 0.03  # subject mask dilation (of the short side) before taking the background
ORB_FEATURES = 2000
ORB_FAST_THRESHOLD = 7  # as motion.py: the OpenCV default 20 finds too few corners on low-contrast texture
DISSOLVE_SOFTNESS = 0.08  # width of the threshold ramp in noise units
DISSOLVE_DILATE_REL = 0.006  # ~4 px at 720p: covers the soft mask rim
_NOISE_OCTAVES = ((0.05, 0.55), (0.02, 0.3), (0.008, 0.15))  # (cell size of the short side, weight)


def load_clean_plate(path: str | Path, out_w: int, out_h: int) -> np.ndarray:
    """uint8 HxWx3 sRGB clean plate at the output size (EXIF orientation applied, cover-fit like the work clip)."""
    with Image.open(path) as im:
        rgb = np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
    return np.ascontiguousarray(cover_fit(rgb, out_w, out_h))


def alignment_frames(n: int) -> list[int]:
    """The work-clip frames ``clean_plate_alignment`` checks: ``ALIGN_SAMPLES`` evenly spaced over ``0..n-1``."""
    if n <= 0:
        return []
    return sorted({round(k) for k in np.linspace(0, n - 1, min(ALIGN_SAMPLES, n)).tolist()})


def _background(shape, mask) -> np.ndarray:
    h, w = shape[:2]
    if mask is None:
        return np.full((h, w), 255, np.uint8)
    r = max(1, round(ALIGN_MASK_DILATE_REL * min(h, w)))
    subject = cv2.dilate((np.asarray(mask) > 0.5).astype(np.uint8), disk_kernel(r))
    return np.where(subject > 0, 0, 255).astype(np.uint8)


def _frame_error(orb, matcher, plate_gray, gray, mask) -> tuple[float, int] | None:
    """``(median |H(p) - p|, inliers)`` of the plate -> frame homography on the background, None if too few."""
    bg = _background(gray.shape, mask)
    kp0, des0 = orb.detectAndCompute(plate_gray, bg)
    kp1, des1 = orb.detectAndCompute(gray, bg)
    if des0 is None or des1 is None or len(kp0) < ALIGN_MIN_INLIERS or len(kp1) < ALIGN_MIN_INLIERS:
        return None
    matches = matcher.match(des0, des1)
    if len(matches) < ALIGN_MIN_INLIERS:
        return None
    src = np.float32([kp0[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([kp1[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    H, inl = cv2.findHomography(src, dst, cv2.RANSAC, ALIGN_RANSAC_PX)
    if H is None or inl is None or int(inl.sum()) < ALIGN_MIN_INLIERS:
        return None
    p = src[inl.ravel() == 1]
    err = np.linalg.norm((cv2.perspectiveTransform(p, H) - p)[:, 0, :], axis=1)
    return float(np.median(err)), int(inl.sum())


def clean_plate_alignment(plate_rgb: np.ndarray, frames_gray: Sequence[np.ndarray],
                          masks: Sequence[np.ndarray] | None, camera_motion: str | None = None) -> dict:
    """``{status: ok|mismatch|unknown, reason, median_err_px, inliers, frames}`` of the output-size plate against
    the given output-size gray frames (``masks``: their subject masks, bool / 0-1 / soft, or None).

    ``median_err_px`` is the worst frame's median background displacement and ``inliers`` the fewest RANSAC
    inliers of a frame. ``reason``: ``aligned`` | ``offset`` (error above ``ALIGN_MAX_ERR_PX``) |
    ``too_few_inliers`` (``unknown``: textureless, unrelated scene or the subject covers the background) |
    ``camera_not_fixed`` (``camera_motion`` given and not ``fixed``: no alignment is attempted)."""
    if camera_motion is not None and camera_motion != "fixed":
        return {"status": "mismatch", "reason": "camera_not_fixed", "median_err_px": None, "inliers": None,
                "frames": 0}
    unknown = {"status": "unknown", "reason": "too_few_inliers", "median_err_px": None, "inliers": None,
               "frames": len(frames_gray)}
    if not len(frames_gray):
        return unknown
    plate_gray = cv2.cvtColor(np.asarray(plate_rgb, np.uint8), cv2.COLOR_RGB2GRAY)
    orb = cv2.ORB_create(nfeatures=ORB_FEATURES, fastThreshold=ORB_FAST_THRESHOLD)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    errs, inliers = [], []
    for k, gray in enumerate(frames_gray):
        if gray.shape != plate_gray.shape:
            raise ValueError(f"frame shape {gray.shape} != plate {plate_gray.shape}")
        r = _frame_error(orb, matcher, plate_gray, gray, None if masks is None else masks[k])
        if r is None:
            return unknown
        errs.append(r[0])
        inliers.append(r[1])
    err = float(max(errs))
    ok = err <= ALIGN_MAX_ERR_PX
    return {"status": "ok" if ok else "mismatch", "reason": "aligned" if ok else "offset",
            "median_err_px": round(err, 3), "inliers": int(min(inliers)), "frames": len(errs)}


def dissolve_progress(window: EffectWindow, frame: int) -> float:
    """0 up to the peak, rising linearly to 1 at the end, 1 after it."""
    if frame < window.peak:
        return 0.0
    if frame >= window.end:  # also the peak frame of a window with end == peak
        return 1.0
    return (frame - window.peak) / (window.end - window.peak)


@lru_cache(maxsize=4)
def _noise(seed: int, w: int, h: int) -> np.ndarray:
    """Smooth value noise over the frame, equalized to a uniform distribution in [0, 1] (so the revealed area grows
    linearly with the threshold). Fixed in time: a revealed pixel stays revealed."""
    rng = np.random.default_rng(seed)
    short = min(w, h)
    acc = np.zeros((h, w), np.float32)
    for rel, weight in _NOISE_OCTAVES:
        cell = max(2.0, rel * short)
        gw, gh = math.ceil(w / cell) + 2, math.ceil(h / cell) + 2
        grid = rng.random((gh, gw), dtype=np.float32)
        big = cv2.resize(grid, (round(gw * cell), round(gh * cell)), interpolation=cv2.INTER_CUBIC)
        acc += np.float32(weight) * big[:h, :w]
    flat = acc.ravel()
    ranks = np.empty(flat.size, np.float32)
    ranks[np.argsort(flat, kind="stable")] = np.linspace(0.0, 1.0, flat.size, dtype=np.float32)
    out = ranks.reshape(h, w)
    out.flags.writeable = False
    return out


def dissolve_alpha(mask: np.ndarray, frame: int, window: EffectWindow, seed: int) -> np.ndarray:
    """float32 HxW in [0, 1]: how much of the clean plate replaces the source at ``frame`` (``mask``: the
    output-size soft subject mask). 0 everywhere up to the peak; from the end on the dilated mask itself."""
    m = np.clip(np.asarray(mask, np.float32), 0.0, 1.0)
    h, w = m.shape
    t = dissolve_progress(window, frame)
    if t <= 0.0 or not m.any():
        return np.zeros((h, w), np.float32)
    r = max(1, round(DISSOLVE_DILATE_REL * min(w, h)))
    m = cv2.dilate(m, disk_kernel(r))
    if t >= 1.0:
        return m
    e = np.float32(DISSOLVE_SOFTNESS)
    thr = np.clip((np.float32(t) * (1 + e) - _noise(int(seed), w, h)) / e, 0.0, 1.0)
    return (m * thr).astype(np.float32)
