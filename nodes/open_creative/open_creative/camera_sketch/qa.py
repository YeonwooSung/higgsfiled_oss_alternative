"""Generated-motion QA for Cinematic results (spec section 8 and 12).

We never claim exact camera control.  We only warn when the subject around the target
seems to change, by comparing the generated video with the deterministic Geometry render:
Lucas-Kanade feature tracks in a window around the target, plus window similarity.
Tracking failure yields ``unknown`` (never a score of 0 and never a pass).
"""

from __future__ import annotations

from typing import Iterable

import cv2
import numpy as np

# Policy defaults, to be tuned on fixtures (not physical constants).
MIN_TRACKED = 8
DRIFT_CHECK_REL = 0.05  # of the output short side
SIMILARITY_CHECK = 0.5  # mean NCC below this -> check
WINDOW_REL = 0.20  # window side, of the output short side

_LK = dict(winSize=(15, 15), maxLevel=2, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))


def _gray(f: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f


def _window(target, shape) -> tuple[int, int, int, int]:
    h, w = shape[:2]
    side = max(8, int(round(WINDOW_REL * min(h, w))))
    side = min(side, h, w)
    x0 = int(np.clip(round(target[0] - side / 2), 0, w - side))
    y0 = int(np.clip(round(target[1] - side / 2), 0, h - side))
    return x0, y0, side, side


class _Tracker:
    """Tracks corners found in the target window of frame 0; keeps only the previous frame."""

    def __init__(self, gray0: np.ndarray, target):
        x0, y0, sw, sh = _window(target, gray0.shape)
        self.side = sw
        self.center = np.array([x0 + sw / 2, y0 + sh / 2], np.float64)
        mask = np.zeros(gray0.shape, np.uint8)
        mask[y0 : y0 + sh, x0 : x0 + sw] = 255
        p = cv2.goodFeaturesToTrack(gray0, 100, 0.001, 2, mask=mask)
        self.pts0 = np.zeros((0, 1, 2), np.float32) if p is None else p
        self.pts = self.pts0.copy()
        self.prev = gray0
        self.lost = len(self.pts0) < MIN_TRACKED

    @property
    def n0(self) -> int:
        return len(self.pts0)

    def step(self, gray: np.ndarray) -> np.ndarray | None:
        """Advance one frame; returns the median displacement from frame 0, or None if lost."""
        if self.lost:
            return None
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(self.prev, gray, self.pts, None, **_LK)
        h, w = gray.shape
        ok = st.ravel() == 1
        ok &= (nxt[:, 0, 0] >= 0) & (nxt[:, 0, 0] < w) & (nxt[:, 0, 1] >= 0) & (nxt[:, 0, 1] < h)
        self.pts, self.pts0, nxt = nxt[ok], self.pts0[ok], nxt[ok]
        self.prev = gray
        if len(self.pts) < MIN_TRACKED:
            self.lost = True
            return None
        return np.median((self.pts - self.pts0)[:, 0, :], axis=0)


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float64).ravel()
    b = b.astype(np.float64).ravel()
    a -= a.mean()
    b -= b.mean()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-6 and nb < 1e-6:
        return 1.0
    if na < 1e-6 or nb < 1e-6:
        return 0.0
    return float(a @ b / (na * nb))


def _patch(gray, center, side):
    return cv2.getRectSubPix(gray, (side, side), (float(center[0]), float(center[1])))


def _report(status, tracked, drift, sim, reasons, sim_min=None):
    return {
        "status": status,
        "tracked_points": int(tracked),
        "target_drift_px": drift,
        "similarity_mean": sim,
        "reasons": reasons,
        # informational only (not gated): worst single-frame window similarity
        "similarity_min": sim_min,
    }


def motion_report(
    generated: Iterable[np.ndarray],
    geometry: Iterable[np.ndarray],
    target_px_out,
    compiled=None,
) -> dict:
    """Compare a generated video with the Geometry render around the target.

    ``target_px_out`` is the (x, y) target pixel in output space on frame 0 (one point, not
    per frame; tracking supplies the motion).  ``compiled`` is accepted for API symmetry
    and is currently unused (sizes come from the frames).  Streams are consumed lazily, two
    consecutive frames per stream at a time.
    """
    gen_it, geo_it = iter(generated), iter(geometry)
    try:
        g0, r0 = _gray(next(gen_it)), _gray(next(geo_it))
    except StopIteration:
        return _report("unknown", 0, None, None, ["no_frames"])
    if g0.shape != r0.shape:
        return _report("unknown", 0, None, None, ["shape_mismatch"])

    gen, geo = _Tracker(g0, target_px_out), _Tracker(r0, target_px_out)
    tracked = min(gen.n0, geo.n0)
    if gen.lost or geo.lost:
        return _report("unknown", tracked, None, None, ["low_texture"])

    short = min(g0.shape[:2])
    side = geo.side
    sims = [_ncc(_patch(g0, gen.center, side), _patch(r0, geo.center, side))]
    drift = 0.0
    gen_lost_at = None
    geo_lost = False
    for fg, fr in zip(gen_it, geo_it):
        g, r = _gray(fg), _gray(fr)
        dg, dr = gen.step(g), geo.step(r)
        if dr is None:
            geo_lost = True
            break
        gcenter = geo.center + dr
        if dg is None:
            if gen_lost_at is None:
                gen_lost_at = len(sims)
            # compare the generated frame at the place the geometry says the target is
            sims.append(_ncc(_patch(g, gcenter, side), _patch(r, gcenter, side)))
            continue
        drift = max(drift, float(np.linalg.norm(dg - dr)))
        sims.append(_ncc(_patch(g, gen.center + dg, side), _patch(r, gcenter, side)))
        tracked = min(tracked, len(gen.pts), len(geo.pts))

    if geo_lost:
        return _report("unknown", tracked, None, None, ["tracking_lost"])
    sim = float(np.mean(sims))
    if gen_lost_at is not None:
        # Points vanished only in the generated video: low window similarity there is itself
        # evidence of a subject change; otherwise we cannot say anything.
        if sim < SIMILARITY_CHECK:
            return _report("check", tracked, None, sim, ["subject_change", "tracking_lost"], float(min(sims)))
        return _report("unknown", tracked, None, None, ["tracking_lost"])

    reasons = []
    if drift > DRIFT_CHECK_REL * short:
        reasons.append("target_drift")
    if sim < SIMILARITY_CHECK:
        reasons.append("low_similarity")
    return _report("check" if reasons else "ok", tracked, drift, sim, reasons, float(min(sims)))
