"""Camera stability diagnosis on the proxy clip: is the background fixed, handheld or moving?

ORB features on the background (outside the dilated subject mask) of frame 0 are matched to sampled
frames k and a RANSAC homography frame 0 -> k is fitted. The shift of frame k is the median
displacement ``|H(p) - p|`` over the inlier background points. Thresholds are policy defaults in
proxy px (tunable), not physical constants. Fewer than ``MIN_INLIERS`` on any sampled frame ->
``unknown`` (never a pass).
"""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

FIXED_MAX_PX = 2.0
HANDHELD_MAX_PX = 12.0
MIN_INLIERS = 12
SAMPLES = 12  # sampled frames k (evenly spaced over 1..n-1)
MASK_DILATE_REL = 0.03  # subject mask dilation, of the proxy short side
RANSAC_PX = 3.0
ORB_FAST_THRESHOLD = 7  # the OpenCV default 20 finds too few corners on low-contrast proxy texture


def _background(shape, mask) -> np.ndarray:
    h, w = shape[:2]
    if mask is None:
        return np.full((h, w), 255, np.uint8)
    r = max(1, round(MASK_DILATE_REL * min(h, w)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return np.where(cv2.dilate((np.asarray(mask) > 0).astype(np.uint8), k) > 0, 0, 255).astype(np.uint8)


def _shift(orb, matcher, kp0, des0, gray, mask) -> float | None:
    kp, des = orb.detectAndCompute(gray, _background(gray.shape, mask))
    if des0 is None or des is None or len(kp0) < MIN_INLIERS or len(kp) < MIN_INLIERS:
        return None
    matches = matcher.match(des0, des)
    if len(matches) < MIN_INLIERS:
        return None
    src = np.float32([kp0[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([kp[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    H, inl = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_PX)
    if H is None or inl is None or int(inl.sum()) < MIN_INLIERS:
        return None
    p = src[inl.ravel() == 1]
    return float(np.median(np.linalg.norm((cv2.perspectiveTransform(p, H) - p)[:, 0, :], axis=1)))


def camera_motion(frames_gray: Sequence[np.ndarray], subject_masks: Sequence[np.ndarray] | None = None) -> dict:
    """``{status: fixed|handheld|moving|unknown, median_shift_px, max_shift_px}`` (proxy px; None when unknown)."""
    n = len(frames_gray)
    unknown = {"status": "unknown", "median_shift_px": None, "max_shift_px": None}
    if n < 2:
        return unknown
    mask_of = (lambda i: None) if subject_masks is None else (lambda i: subject_masks[i])
    orb = cv2.ORB_create(nfeatures=1000, fastThreshold=ORB_FAST_THRESHOLD)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    kp0, des0 = orb.detectAndCompute(frames_gray[0], _background(frames_gray[0].shape, mask_of(0)))
    ks = sorted({round(k) for k in np.linspace(1, n - 1, min(SAMPLES, n - 1)).tolist()})
    shifts = []
    for k in ks:
        s = _shift(orb, matcher, kp0, des0, frames_gray[k], mask_of(k))
        if s is None:
            return unknown
        shifts.append(s)
    mx = float(max(shifts))
    status = "fixed" if mx <= FIXED_MAX_PX else "handheld" if mx <= HANDHELD_MAX_PX else "moving"
    return {"status": status, "median_shift_px": float(np.median(shifts)), "max_shift_px": mx}
