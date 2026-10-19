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
    assert "low_texture" in r["reasons"]
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


def test_accepts_generators_and_is_fast():
    import time

    t = time.time()
    g = _seq()
    motion_report((f for f in g), (f for f in g), TARGET, None)
    assert time.time() - t < 3
