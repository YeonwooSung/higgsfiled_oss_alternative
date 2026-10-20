"""2D anchor point tracking on the proxy clip (spec section 6.2), algorithm ``lk_fb_median_v1``.

Per step, corners are detected in a disk patch around the current point, tracked with pyramidal LK
to the next frame and back (forward/backward check); features whose round-trip error exceeds
``fb_max`` are dropped and the point moves by the median displacement of the kept ones. A step fails
(``valid=False``) when fewer than ``min_features`` survive or when the point leaves the frame. After the first failed
step a run stops: the remaining frames in that direction are invalid until the next keyframe. Invalid
frames are never filled by copying the last coordinate.

Coordinates: ``TrackPoint.x/y`` are proxy pixels. Keyframes (the anchor and the manual corrections)
are passed as the config stores them, ``{frame, x, y}`` in uv01 of the work clip, and converted to
proxy pixels with the frame size. ``track_to_json`` writes uv01 again.

Corrections and segment reuse:
    segs = plan_segments(anchor, corrections, n_frames, window)   # ordered list[Segment]
    keys = segment_keys(frames_sha, anchor, corrections, window, params, n_frames=n_frames)
    pts  = track_segment(frames, segs[i], **params)               # the points of segs[i].lo..hi
    track = assemble(segs, [pts...], n_frames, proxy_wh)
``track_with_corrections`` is exactly that without a cache. ``keys[i]`` identifies ``segs[i]``: it
hashes the segment's bounding keyframe(s), its frame range, the track params and ``frames_sha`` (an
id of the proxy clip chosen by the caller), so an edit to one correction only changes the keys of
the (at most two) segments next to it.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import cv2
import numpy as np

ALGORITHM = "lk_fb_median_v1"
DEFAULT_PARAMS = {"patch_r": None, "max_corners": 40, "fb_max": 3.0, "min_features": 4}
PATCH_REL = 0.04  # default patch radius, of the proxy short side
FULL_CONF_FEATURES = 20  # kept features at which the feature-count term of the confidence saturates

_LK = {"winSize": (21, 21), "maxLevel": 3, "criteria": (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)}


@dataclass(frozen=True)
class TrackPoint:
    frame: int
    x: float  # proxy px
    y: float
    valid: bool
    confidence: float
    fb_error: float  # median forward/backward error of the kept features, proxy px
    n_features: int
    source: str  # "tracked" | "keyframe" | "static"


@dataclass(frozen=True)
class Segment:
    """Frames ``lo..hi`` (inclusive, keyframes excluded) between ``left`` and ``right`` keyframes.

    ``left``/``right`` are ``(frame, u, v)`` or None for the outer one-direction runs. ``lo > hi``
    means the segment has no frames (adjacent keyframes, or a run cut off by the window)."""

    left: tuple[int, float, float] | None
    right: tuple[int, float, float] | None
    lo: int
    hi: int


def _invalid(frame: int, source: str = "tracked") -> TrackPoint:
    return TrackPoint(frame, math.nan, math.nan, False, 0.0, math.nan, 0, source)


def _patch_r(shape, patch_r) -> int:
    return int(patch_r) if patch_r else max(4, round(PATCH_REL * min(shape[:2])))


def _inside(x: float, y: float, shape) -> bool:
    h, w = shape[:2]
    return 0.0 <= x < w and 0.0 <= y < h


def _step(prev, cur, xy, r, max_corners, fb_max, min_features):
    """One LK step from ``prev`` to ``cur``. Returns ``(new_xy, fb_median, n_kept)`` or None on failure."""
    mask = np.zeros(prev.shape[:2], np.uint8)
    cv2.circle(mask, (round(xy[0]), round(xy[1])), r, 255, -1)
    p0 = cv2.goodFeaturesToTrack(prev, max_corners, 0.01, 2, mask=mask)
    if p0 is None or len(p0) < min_features:
        return None
    p1, st1, _ = cv2.calcOpticalFlowPyrLK(prev, cur, p0, None, **_LK)
    pb, st2, _ = cv2.calcOpticalFlowPyrLK(cur, prev, p1, None, **_LK)
    fb = np.linalg.norm((pb - p0)[:, 0, :], axis=1)
    h, w = cur.shape[:2]
    q = p1[:, 0, :]
    ok = (st1.ravel() == 1) & (st2.ravel() == 1) & (fb <= fb_max)
    ok &= (q[:, 0] >= 0) & (q[:, 0] < w) & (q[:, 1] >= 0) & (q[:, 1] < h)
    if ok.sum() < min_features:
        return None
    d = np.median((p1 - p0)[ok, 0, :].astype(np.float64), axis=0)
    nx, ny = xy[0] + d[0], xy[1] + d[1]
    if not _inside(nx, ny, cur.shape):
        return None
    return (float(nx), float(ny)), float(np.median(fb[ok])), int(ok.sum())


def _confidence(n_kept: int, fb_median: float, fb_max: float) -> float:
    return min(1.0, n_kept / FULL_CONF_FEATURES) * max(0.0, 1.0 - fb_median / fb_max)


def _run(frames, start, xy, stop, *, patch_r=None, max_corners=40, fb_max=3.0, min_features=4) -> list[TrackPoint]:
    """Track from keyframe ``start`` (at proxy ``xy``) towards ``stop`` (inclusive, either direction).

    Returns the points of the frames strictly after ``start`` up to ``stop``, in time order."""
    step = 1 if stop >= start else -1
    r = _patch_r(frames[start].shape, patch_r)
    out: list[TrackPoint] = []
    alive = True
    for f in range(start + step, stop + step, step):
        res = _step(frames[f - step], frames[f], xy, r, max_corners, fb_max, min_features) if alive else None
        if res is None:
            alive = False
            out.append(_invalid(f))
            continue
        xy, fbm, n = res
        out.append(TrackPoint(f, xy[0], xy[1], True, _confidence(n, fbm, fb_max), fbm, n, "tracked"))
    return out if step > 0 else out[::-1]


def track_point(frames: Sequence[np.ndarray], anchor_frame: int, anchor_xy, *, patch_r=None, max_corners=40,
                fb_max=3.0, min_features=4, frame_range=None) -> list[TrackPoint]:
    """Forward and backward track from the anchor (proxy px), one point per frame of ``frames``.

    ``frame_range = (lo, hi)`` (inclusive) limits tracking; frames outside it are invalid."""
    n = len(frames)
    lo, hi = (0, n - 1) if frame_range is None else (max(0, frame_range[0]), min(n - 1, frame_range[1]))
    if not lo <= anchor_frame <= hi:
        raise ValueError("anchor_frame must lie in frame_range")
    params = {"patch_r": patch_r, "max_corners": max_corners, "fb_max": fb_max, "min_features": min_features}
    ax, ay = float(anchor_xy[0]), float(anchor_xy[1])
    pts = [_invalid(f) for f in range(n)]
    pts[anchor_frame] = TrackPoint(anchor_frame, ax, ay, True, 1.0, 0.0, 0, "keyframe")
    for p in _run(frames, anchor_frame, (ax, ay), lo, **params) + _run(frames, anchor_frame, (ax, ay), hi, **params):
        pts[p.frame] = p
    return pts


# ------------------------------------------------------------------------------------------ corrections


def _window_range(anchor: dict, n_frames: int, window) -> tuple[int, int]:
    if window is None:
        return 0, n_frames - 1
    a = int(anchor["frame"])
    return max(0, a - int(window)), min(n_frames - 1, a + int(window))


def _keyframes(anchor: dict, corrections, lo: int, hi: int) -> list[tuple[int, float, float]]:
    """Anchor ∪ corrections as sorted ``(frame, u, v)``, unique per frame (a correction wins over the
    anchor), keeping only frames in ``lo..hi``: keyframes outside the clip or the track window are dropped,
    so those frames stay invalid and never start a run."""
    kf = {int(anchor["frame"]): (float(anchor["x"]), float(anchor["y"]))}
    for c in corrections or []:
        kf[int(c["frame"])] = (float(c["x"]), float(c["y"]))
    return [(f, u, v) for f, (u, v) in sorted(kf.items()) if lo <= f <= hi]


def plan_segments(anchor: dict, corrections, n_frames: int, window=None) -> list[Segment]:
    """Segments in time order: the backward run before the first keyframe, one segment between each
    pair of adjacent keyframes, the forward run after the last. Frames are clipped to ±``window``
    around the anchor frame (``track_window_frames``); keyframes themselves are never in a segment.
    Keyframes outside the clip or the window are ignored; with none left there are no segments."""
    lo, hi = _window_range(anchor, n_frames, window)
    kfs = _keyframes(anchor, corrections, lo, hi)
    if not kfs:
        return []
    segs = [Segment(None, kfs[0], lo, min(hi, kfs[0][0] - 1))]
    segs += [Segment(a, b, max(lo, a[0] + 1), min(hi, b[0] - 1)) for a, b in itertools.pairwise(kfs)]
    segs.append(Segment(kfs[-1], None, max(lo, kfs[-1][0] + 1), hi))
    return segs


def _resolved(params: dict) -> dict:
    unknown = set(params) - set(DEFAULT_PARAMS)
    if unknown:
        raise TypeError(f"unknown track params: {sorted(unknown)}")
    return {**DEFAULT_PARAMS, **params}


def segment_keys(frames_sha: str, anchor: dict, corrections, window, params: dict | None = None, *,
                 n_frames: int) -> list[str]:
    """One sha256 per ``plan_segments`` segment, in the same order."""
    p = _resolved(params or {})
    keys = []
    for s in plan_segments(anchor, corrections, n_frames, window):
        canon = json.dumps({"algorithm": ALGORITHM, "frames_sha": frames_sha, "left": s.left, "right": s.right,
                            "lo": s.lo, "hi": s.hi, "params": p}, sort_keys=True, separators=(",", ":"))
        keys.append(hashlib.sha256(canon.encode()).hexdigest())
    return keys


def _px(kf, shape) -> tuple[float, float]:
    h, w = shape[:2]
    return kf[1] * w, kf[2] * h


def _blend(f: int, a: int, b: int, pf: TrackPoint, pb: TrackPoint) -> TrackPoint:
    if not (pf.valid and pb.valid):
        return pf if pf.valid else pb
    t = (f - a) / (b - a)

    def mix(u, v):
        return (1 - t) * u + t * v

    return TrackPoint(f, mix(pf.x, pb.x), mix(pf.y, pb.y), True, mix(pf.confidence, pb.confidence),
                      mix(pf.fb_error, pb.fb_error), round(mix(pf.n_features, pb.n_features)), "tracked")


def track_segment(frames: Sequence[np.ndarray], seg: Segment, **params) -> list[TrackPoint]:
    """Points of frames ``seg.lo..seg.hi``: forward from ``left`` and backward from ``right``, blended
    linearly by time where both are valid, else the valid one (outer runs are single-direction).

    A keyframe only starts a run when it borders the range: one beyond the track window (the range was
    clipped) does not, so no frame outside the window is ever tracked."""
    p = _resolved(params)
    if seg.lo > seg.hi:
        return []
    shape = frames[0].shape
    runs = []
    if seg.left is not None and seg.left[0] == seg.lo - 1:
        runs.append(_run(frames, seg.left[0], _px(seg.left, shape), seg.hi, **p))
    if seg.right is not None and seg.right[0] == seg.hi + 1:
        runs.append(_run(frames, seg.right[0], _px(seg.right, shape), seg.lo, **p))
    if not runs:
        return [_invalid(f) for f in range(seg.lo, seg.hi + 1)]
    if len(runs) == 1:
        return runs[0]
    return [_blend(f, seg.left[0], seg.right[0], a, b) for f, a, b in zip(range(seg.lo, seg.hi + 1), *runs)]


def assemble(segments: Sequence[Segment], seg_points: Sequence[list[TrackPoint]], n_frames: int,
             proxy_wh) -> list[TrackPoint]:
    """Full-length track from per-segment points. The segments' bounding keyframes are exact; frames no
    segment covers (outside the track window, dropped keyframes) are invalid."""
    pw, ph = proxy_wh
    pts = [_invalid(f) for f in range(n_frames)]
    for seg_pts in seg_points:
        for q in seg_pts:
            pts[q.frame] = q
    for seg in segments:
        for kf in (seg.left, seg.right):
            if kf is not None:
                f, u, v = kf
                pts[f] = TrackPoint(f, u * pw, v * ph, True, 1.0, 0.0, 0, "keyframe")
    return pts


def track_with_corrections(frames: Sequence[np.ndarray], anchor: dict, corrections, window=None,
                           **params) -> list[TrackPoint]:
    """Track with the anchor and manual corrections as keyframes (uv01 ``{frame, x, y}`` dicts)."""
    n = len(frames)
    h, w = frames[0].shape[:2]
    segs = plan_segments(anchor, corrections, n, window)
    return assemble(segs, [track_segment(frames, s, **params) for s in segs], n, (w, h))


# ------------------------------------------------------------------------------------------ post-processing


def smooth_track(points: Sequence[TrackPoint], sigma_frames: float = 1.0,
                 max_shift: float | None = None) -> list[TrackPoint]:
    """Zero-phase (non-causal) Gaussian per contiguous valid run; keyframes keep their exact position and
    invalid frames are never filled. At run ends the kernel is truncated and renormalized.

    ``max_shift`` (proxy px, per axis) bounds how far smoothing may move a point from its measurement, so it removes
    sub-pixel jitter without smearing real motion: CFR-resampled clips (duplicated frames of a 15 fps source,
    uneven 30 -> 24 fps steps) and fast hands move in steps of tens of px between frames, which a plain Gaussian
    spreads over the neighbours (and a truncated kernel biases at run ends)."""
    out = list(points)
    if sigma_frames <= 0:
        return out
    rad = max(1, math.ceil(3 * sigma_frames))
    k = np.exp(-0.5 * (np.arange(-rad, rad + 1) / sigma_frames) ** 2)
    i, n = 0, len(points)
    while i < n:
        if not points[i].valid:
            i += 1
            continue
        j = i
        while j < n and points[j].valid:
            j += 1
        xy = np.array([[p.x, p.y] for p in points[i:j]], np.float64)
        m_len = len(xy)

        def conv(a, m_len=m_len):  # centered on every sample, also for runs shorter than the kernel
            return np.convolve(a, k, mode="full")[rad : rad + m_len]

        norm = conv(np.ones(m_len))
        sx, sy = conv(xy[:, 0]) / norm, conv(xy[:, 1]) / norm
        if max_shift is not None:
            sx = xy[:, 0] + np.clip(sx - xy[:, 0], -max_shift, max_shift)
            sy = xy[:, 1] + np.clip(sy - xy[:, 1], -max_shift, max_shift)
        for m, p in enumerate(points[i:j]):
            if p.source != "keyframe":
                out[i + m] = replace(p, x=float(sx[m]), y=float(sy[m]))
        i = j
    return out


def static_track(anchor_xy, n: int) -> list[TrackPoint]:
    """``follow=false``: the anchor position on every frame (proxy px)."""
    x, y = float(anchor_xy[0]), float(anchor_xy[1])
    return [TrackPoint(f, x, y, True, 1.0, 0.0, 0, "static") for f in range(n)]


def invalid_intervals(points: Sequence[TrackPoint]) -> list[list[int]]:
    """Inclusive ``[first, last]`` frame pairs of the invalid runs."""
    out: list[list[int]] = []
    for p in points:
        if p.valid:
            continue
        if out and out[-1][1] == p.frame - 1:
            out[-1][1] = p.frame
        else:
            out.append([p.frame, p.frame])
    return out


def track_to_json(points: Sequence[TrackPoint], proxy_wh, out_wh, params: dict | None = None) -> dict:
    """Track JSON (schema 1). ``points[].x/y`` are uv01 of the work clip (proxy px / proxy size); invalid
    points carry ``null`` coordinates."""
    pw, ph = proxy_wh

    def uv(p: TrackPoint):
        return (round(p.x / pw, 6), round(p.y / ph, 6)) if p.valid else (None, None)

    return {
        "schema_version": 1,
        "proxy_size": [int(pw), int(ph)],
        "output_size": [int(out_wh[0]), int(out_wh[1])],
        "points": [{"frame": p.frame, "x": uv(p)[0], "y": uv(p)[1], "valid": p.valid,
                    "confidence": round(p.confidence, 4)} for p in points],
        "invalid_intervals": invalid_intervals(points),
        "algorithm": ALGORITHM,
        "params": _resolved(params or {}),
    }
