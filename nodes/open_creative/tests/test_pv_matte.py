"""Plate matte, retime and loop QA (Pocket VFX §7)."""

from itertools import pairwise

import numpy as np
import pytest
from open_creative.pocket_vfx.matte import (
    MatteConfig,
    MatteStats,
    PlateLengthError,
    loop_indices,
    loop_seam_ok,
    matte_frame,
    matte_qa,
    matte_sequence,
    plate_indices,
    retime_factor,
    retime_indices,
)

H, W = 64, 96


def _bg(rng, sigma=4.0, level=6.0):
    return np.clip(rng.normal(level, sigma, (H, W, 3)), 0, 255)


def _wisp(frame, x0=30, size=12, value=230):
    f = frame.copy()
    f[20:20 + size, x0:x0 + size] = value
    return f


def _run(frames, cfg=None):
    stats = MatteStats()
    out = list(matte_sequence(iter(frames), cfg, stats))
    return out, matte_qa(stats.summary())


def test_black_plate_is_empty():
    frames = [np.zeros((H, W, 3), np.uint8) for _ in range(5)]
    out, qa = _run(frames)
    assert all(float(a.max()) == 0.0 for _, a in out)
    assert qa["status"] == "check" and "empty_plate" in qa["reasons"]


def test_wisp_on_noisy_dark_bg_is_clean():
    rng = np.random.default_rng(0)
    frames = [np.rint(_wisp(_bg(rng), x0=20 + 4 * i)).astype(np.uint8) for i in range(8)]
    out, qa = _run(frames)
    bg = np.ones((H, W), bool)
    for i, (_, a) in enumerate(out):
        x0 = 20 + 4 * i
        bg[:] = True
        bg[18:34, x0 - 6:x0 + 18] = False  # wisp + temporal-smoothing bleed from neighbours
        assert a[bg].max() <= 0.05
    assert qa["status"] == "ok", qa
    assert qa["coverage"] > 0.002


def test_matte_frame_values():
    rng = np.random.default_rng(1)
    rgb, a = matte_frame(np.rint(_wisp(_bg(rng))).astype(np.uint8))
    assert rgb.shape == (H, W, 3) and rgb.dtype == np.float32 and a.shape == (H, W)
    assert a[25, 35] == pytest.approx(1.0) and rgb[25, 35].min() > 0.5
    assert (rgb <= a[..., None] + 1e-6).all() and (rgb >= 0).all()


def test_brightening_bg_is_halo_risk():
    rng = np.random.default_rng(2)
    ramp = np.linspace(0, 1, W)[None, :, None]
    frames = []
    for i in range(10):
        bg = 6 + ramp * (10 + 8 * i)  # spatial gradient that brightens over time, stays below the matte threshold
        frames.append(np.rint(_wisp(np.clip(bg + rng.normal(0, 2, (H, W, 3)), 0, 255))).astype(np.uint8))
    _, qa = _run(frames)
    assert qa["status"] == "check" and "halo_risk" in qa["reasons"]


def test_flicker_detected():
    rng = np.random.default_rng(3)
    frames = [np.rint(_wisp(_bg(rng), size=40, value=230 if i % 2 else 0)).astype(np.uint8) for i in range(8)]
    _, qa = _run(frames)
    assert "flicker" in qa["reasons"] and qa["flicker"] > 0.08


def test_temporal_smoothing_is_zero_phase_and_streams():
    consumed = []

    def gen():
        for i in range(6):
            consumed.append(i)
            f = np.zeros((H, W, 3), np.uint8)
            if i == 2:
                f[:] = 255
            yield f

    it = matte_sequence(gen(), MatteConfig())
    _, a0 = next(it)
    assert len(consumed) <= 2  # at most one frame of look-ahead
    rest = [a for _, a in it]
    alphas = [float(a0.mean())] + [float(a.mean()) for a in rest]
    assert alphas[1] == pytest.approx(0.25, abs=1e-5) and alphas[2] == pytest.approx(0.5, abs=1e-5)
    assert alphas[3] == pytest.approx(0.25, abs=1e-5) and alphas[0] == 0.0 and len(alphas) == 6


def test_retime_49_into_40():
    assert retime_factor(49, 40) == pytest.approx(1.225)
    idx = retime_indices(49, 40)
    assert len(idx) == 40 and idx[0] == 0 and idx[-1] == 48
    assert all(b >= a for a, b in pairwise(idx))


def test_retime_out_of_range_raises():
    with pytest.raises(PlateLengthError) as e:
        retime_indices(49, 120)
    assert e.value.plate_frames == 49 and e.value.window_frames == 120
    assert isinstance(e.value, ValueError)
    with pytest.raises(PlateLengthError):
        retime_indices(100, 40)


def test_plate_indices_rules():
    assert plate_indices(100, 40) == list(range(40))  # P > 2W: time-trim, factor 1
    idx = plate_indices(60, 40)  # W <= P <= 2W: retime whole plate
    assert idx[0] == 0 and idx[-1] == 59 and len(idx) == 40
    assert plate_indices(40, 40) == list(range(40))
    idx = plate_indices(30, 50)  # P < W <= 2P: stretch
    assert len(idx) == 50 and idx[-1] == 29 and all(b >= a for a, b in pairwise(idx))
    with pytest.raises(PlateLengthError):
        plate_indices(49, 120)
    with pytest.raises(PlateLengthError):
        plate_indices(49, 120, loopable=True, seam_ok=False)
    with pytest.raises(PlateLengthError):
        plate_indices(49, 120, loopable=False, seam_ok=True)
    loop = plate_indices(20, 70, loopable=True, seam_ok=True)
    assert loop == [i % 20 for i in range(70)]


def test_loop_seam_and_loop_indices():
    a = np.full((8, 8, 3), 100, np.uint8)
    assert loop_seam_ok(a, a + 6) and not loop_seam_ok(a, a + 7)
    assert loop_indices(3, 7, seam_ok=True) == [0, 1, 2, 0, 1, 2, 0]
    with pytest.raises(PlateLengthError):
        loop_indices(3, 7, seam_ok=False)
