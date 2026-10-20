"""Outside-ROI integrity (원본 보존: max channel error <= 1/255 outside the effect ROI) and Occlusion QA."""

from __future__ import annotations

import numpy as np

MAX_OUTSIDE_ERR = 1  # in 8-bit code values
CORE_CHANGE_THRESHOLD = 2  # a core pixel counts as covered when a channel moves by > 2/255


def outside_roi_report(source: np.ndarray, out: np.ndarray, roi: np.ndarray) -> dict:
    """``max_err`` (8-bit code values) and ``changed_outside_px`` over pixels where ``roi`` is False."""
    if source.shape != out.shape or roi.shape != source.shape[:2]:
        raise ValueError("source, out and roi must share the frame size")
    outside = ~np.asarray(roi, bool)
    diff = np.abs(source[outside].astype(np.int16) - out[outside].astype(np.int16)).max(axis=-1, initial=0)
    return {
        "max_err": int(diff.max(initial=0)),
        "changed_outside_px": int(np.count_nonzero(diff)),
    }


class IntegrityAccumulator:
    """Per-frame outside-ROI reports folded into one QA verdict; never ``pass`` on zero frames."""

    def __init__(self) -> None:
        self._frames = 0
        self._max_err = 0
        self._worst_frame: int | None = None

    def add(self, source: np.ndarray, out: np.ndarray, roi: np.ndarray) -> dict:
        rep = outside_roi_report(source, out, roi)
        if self._worst_frame is None or rep["max_err"] > self._max_err:
            self._max_err, self._worst_frame = rep["max_err"], self._frames
        self._frames += 1
        return rep

    def result(self) -> dict:
        if self._frames == 0:
            return {"status": "invalid", "max_err": None, "worst_frame": None, "frames": 0}
        return {
            "status": "pass" if self._max_err <= MAX_OUTSIDE_ERR else "fail",
            "max_err": self._max_err,
            "worst_frame": self._worst_frame,
            "frames": self._frames,
        }


def subject_core_cover(out: np.ndarray, source: np.ndarray, mask_core: np.ndarray) -> float:
    """Fraction of the (already eroded) subject-core pixels changed by > 2/255 in any channel."""
    core = np.asarray(mask_core) > 0.5 if np.asarray(mask_core).dtype != np.bool_ else np.asarray(mask_core)
    if core.shape != source.shape[:2] or out.shape != source.shape:
        raise ValueError("out, source and mask_core must share the frame size")
    n = int(np.count_nonzero(core))
    if n == 0:
        return 0.0
    diff = np.abs(out[core].astype(np.int16) - source[core].astype(np.int16)).max(axis=-1)
    return float(np.count_nonzero(diff > CORE_CHANGE_THRESHOLD)) / n
