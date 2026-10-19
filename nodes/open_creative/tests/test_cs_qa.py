from pathlib import Path

import cv2
import numpy as np
from open_creative.camera_sketch import qa
from open_creative.camera_sketch.qa import motion_report

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "camera-sketch"
W, H, N = 320, 180, 30
TARGET = (160, 90)


def _base():
    im = cv2.imread(str(FIX / "room.png"))
    return cv2.resize(im, (W + 40, H + 40), interpolation=cv2.INTER_AREA)


def _seq(dx_per_frame=1.0, n=N):
    base = _base()
    out = []
    for i in range(n):
        m = np.float32([[1, 0, -20 - dx_per_frame * i], [0, 1, -20]])
        out.append(cv2.warpAffine(base, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))
    return out


def test_identical_is_ok():
    g = _seq()
    r = motion_report(iter(g), iter(g), TARGET, None)
    assert r["status"] == "ok", r
    assert r["tracked_points"] >= qa.MIN_TRACKED
    assert r["target_drift_px"] < 0.5
    assert r["similarity_mean"] > 0.95
    assert r["reasons"] == []


def test_flat_gray_is_unknown_not_zero():
    flat = [np.full((H, W, 3), 128, np.uint8) for _ in range(10)]
    r = motion_report(iter(flat), iter(flat), TARGET, None)
    assert r["status"] == "unknown"
    assert r["reasons"] == ["low_texture_generated", "low_texture_geometry"]
    assert r["target_drift_px"] is None and r["similarity_mean"] is None


def test_target_replaced_by_noise_is_check():
    geo = _seq()
    gen = [f.copy() for f in geo]
    rng = np.random.default_rng(0)
    half = int(0.2 * H) // 2 + 6
    x, y = TARGET
    for i in range(15, N):
        gen[i][y - half : y + half, x - half : x + half] = rng.integers(0, 256, (2 * half, 2 * half, 3), np.uint8)
    r = motion_report(iter(gen), iter(geo), TARGET, None)
    assert r["status"] == "check", r
    assert r["reasons"]


def test_drifting_generated_is_check():
    r = motion_report(iter(_seq(4.0)), iter(_seq(1.0)), TARGET, None)
    assert r["status"] == "check"
    assert r["target_drift_px"] > qa.DRIFT_CHECK_REL * H


def test_geometry_untrackable_is_unknown():
    flat = [np.full((H, W, 3), 90, np.uint8) for _ in range(5)]
    r = motion_report(iter(_seq(n=5)), iter(flat), TARGET, None)
    assert r["status"] == "unknown"
    assert r["reasons"] == ["low_texture_geometry"]


def test_accepts_generators():
    g = _seq()
    assert motion_report((f for f in g), (f for f in g), TARGET, None)["status"] == "ok"


def _noise(seq, start, half=80):
    rng = np.random.default_rng(1)
    out = [f.copy() for f in seq]
    x, y = TARGET
    for i in range(start, len(seq)):
        out[i][y - half : y + half, x - half : x + half] = rng.integers(0, 256, (2 * half, 2 * half, 3), np.uint8)
    return out


def test_late_change_last_20pct_is_check():
    geo = _seq(0.0, n=60)
    r = motion_report(iter(_noise(geo, 48)), iter(geo), TARGET, None)
    assert r["status"] == "check", r
    assert "subject_change" in r["reasons"]
    assert r["similarity_window_min"] < qa.SIMILARITY_CHECK


def test_15_frame_noise_clear_margin():
    geo = _seq(0.0)
    r = motion_report(iter(_noise(geo, 15)), iter(geo), TARGET, None)
    assert r["status"] == "check" and "subject_change" in r["reasons"]
    assert r["similarity_window_min"] < qa.SIMILARITY_CHECK - 0.15


def test_tracking_lost_generated_is_unknown(monkeypatch):
    # Generated tracker loses its points mid-sequence while window similarity stays high.
    geo = _seq(0.0, n=20)
    orig = qa._Tracker.step
    calls = {}

    def step(self, gray):
        k = id(self)
        calls[k] = calls.get(k, 0) + 1
        if calls[k] > 5 and k == first[0]:
            self.lost = True
            return None
        return orig(self, gray)

    first = [None]
    orig_init = qa._Tracker.__init__

    def init(self, *a, **kw):
        orig_init(self, *a, **kw)
        if first[0] is None:
            first[0] = id(self)  # generated tracker is constructed first

    monkeypatch.setattr(qa._Tracker, "__init__", init)
    monkeypatch.setattr(qa._Tracker, "step", step)
    r = motion_report(iter(geo), iter(geo), TARGET, None)
    assert r["status"] == "unknown"
    assert r["reasons"] == ["tracking_lost_generated"]
    assert r["target_drift_px"] is None and r["similarity_mean"] is None


def test_tracking_lost_geometry_is_unknown():
    seq = _seq(0.0)
    geo = seq[:5] + [np.full((H, W, 3), 90, np.uint8)] * 5
    r = motion_report(iter(seq[:10]), iter(geo), TARGET, None)
    assert r["status"] == "unknown" and r["reasons"] == ["tracking_lost_geometry"]


def test_drift_before_loss_is_check():
    geo = _seq(1.0, n=20)
    gen = _seq(4.0, n=20)
    gen = gen[:10] + [np.full((H, W, 3), 90, np.uint8)] * 10
    r = motion_report(iter(gen), iter(geo), TARGET, None)
    assert r["status"] == "check" and "target_drift" in r["reasons"]
    assert "tracking_lost_generated" in r["reasons"]


def test_no_frames_shape_length_dtype():
    g = _seq(n=5)
    assert motion_report(iter([]), iter([]), TARGET)["reasons"] == ["no_frames"]
    assert motion_report(iter(g), iter(g[:3]), TARGET)["reasons"] == ["length_mismatch"]
    assert motion_report(iter(g[:3]), iter(g), TARGET)["reasons"] == ["length_mismatch"]
    small = [cv2.resize(f, (160, 90)) for f in g]
    assert motion_report(iter(g), iter(small), TARGET)["reasons"] == ["shape_mismatch"]
    f32 = [f.astype(np.float32) for f in g]
    assert motion_report(iter(f32), iter(g), TARGET)["reasons"] == ["unsupported_frame"]
    gray = [f[:, :, 0] for f in g]
    assert motion_report(iter(g), iter(gray), TARGET)["reasons"] == ["unsupported_frame"]
