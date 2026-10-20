"""Pocket VFX point tracker (spec 6.2) on the committed synthetic fixtures (fixtures/pocket-vfx).

Tracking runs on the 640x360 proxy; ground truth is in output (1280x720) px, so errors are compared
in output px (proxy x 2)."""

import json
import math
from pathlib import Path

import cv2
import numpy as np
import pytest
from open_creative.media.video import decode_frames
from open_creative.pocket_vfx.track import (
    TrackPoint,
    assemble,
    plan_segments,
    segment_keys,
    smooth_track,
    static_track,
    track_point,
    track_segment,
    track_to_json,
    track_with_corrections,
)

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "pocket-vfx"
PW, PH = 640, 360
SCALE = 2.0  # proxy -> output


def _proxy_gray(name):
    return [cv2.cvtColor(cv2.resize(f, (PW, PH), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
            for f in decode_frames(FIX / name)]


@pytest.fixture(scope="module")
def gt():
    return np.array([[h["x"], h["y"]] for h in json.loads((FIX / "tracks.json").read_text())["hand"]])


@pytest.fixture(scope="module")
def stomp():
    return _proxy_gray("stomp_landscape.mp4")


@pytest.fixture(scope="module")
def occluded():
    return _proxy_gray("occluded_hand.mp4")


def _kf(gt, f):
    return {"frame": f, "x": gt[f, 0] / 1280, "y": gt[f, 1] / 720}


def _errors(points, gt, frames):
    return [math.hypot(points[f].x * SCALE - gt[f, 0], points[f].y * SCALE - gt[f, 1]) for f in frames]


def test_stomp_hand_median_error_within_5_output_px(stomp, gt):
    pts = track_point(stomp, 43, gt[43] / SCALE)
    assert len(pts) == 120 and all(p.valid for p in pts)
    assert pts[43].source == "keyframe" and pts[43].confidence == 1.0
    assert np.median(_errors(pts, gt, range(120))) <= 5.0
    assert all(0.0 <= p.confidence <= 1.0 for p in pts)


def test_occluded_frames_are_invalid_not_copied(occluded, gt):
    pts = track_point(occluded, 20, gt[20] / SCALE)
    assert all(pts[f].valid for f in range(50))
    for f in range(50, 63):
        assert not pts[f].valid and math.isnan(pts[f].x) and pts[f].confidence == 0.0
    # the run stops at the first failure: nothing after the occlusion is valid without a keyframe
    assert not any(p.valid for p in pts[63:])
    assert np.median(_errors(pts, gt, range(50))) <= 5.0


def test_one_correction_after_occlusion_recovers_the_track(occluded, gt):
    anchor, corr = _kf(gt, 20), _kf(gt, 63)
    pts = track_with_corrections(occluded, anchor, [corr])
    assert [p.frame for p in pts if not p.valid] == list(range(50, 63))
    assert np.median(_errors(pts, gt, range(63, 120))) <= 5.0
    assert pts[63].source == "keyframe" and (pts[63].x, pts[63].y) == (corr["x"] * PW, corr["y"] * PH)
    assert (pts[20].x, pts[20].y) == (anchor["x"] * PW, anchor["y"] * PH)
    assert track_to_json(pts, (PW, PH), (1280, 720))["invalid_intervals"] == [[50, 62]]


def test_segment_between_keyframes_blends_both_directions(stomp, gt):
    corr = _kf(gt, 80)
    corr["x"] += 3 / PW  # a correction the tracker disagrees with by 3 proxy px
    pts = track_with_corrections(stomp, _kf(gt, 10), [corr])
    assert all(p.valid for p in pts)
    assert (pts[80].x, pts[80].y) == (corr["x"] * PW, corr["y"] * PH)
    assert np.median(_errors(pts, gt, range(120))) <= 5.0
    # near the right keyframe the blended track follows the correction's offset
    fwd_only = track_point(stomp, 10, gt[10] / SCALE)
    assert pts[79].x - fwd_only[79].x > 2.0


def test_correction_wins_over_anchor_on_same_frame(stomp, gt):
    pts = track_with_corrections(stomp, _kf(gt, 43), [{"frame": 43, "x": 0.25, "y": 0.5}], window=0)
    assert (pts[43].x, pts[43].y) == (0.25 * PW, 0.5 * PH)


def test_track_window_limits_tracked_frames(stomp, gt):
    pts = track_with_corrections(stomp, _kf(gt, 43), [], window=10)
    assert [p.frame for p in pts if p.valid] == list(range(33, 54))


def test_keyframes_beyond_the_window_are_invalid(stomp, gt):
    corrs = [_kf(gt, 5), _kf(gt, 38), _kf(gt, 100)]
    pts = track_with_corrections(stomp, _kf(gt, 43), corrs, window=10)
    assert [p.frame for p in pts if p.valid] == list(range(33, 54))
    assert not pts[5].valid and not pts[100].valid and math.isnan(pts[5].x)
    assert pts[38].source == "keyframe" and (pts[38].x, pts[38].y) == (corrs[1]["x"] * PW, corrs[1]["y"] * PH)


def test_keyframes_outside_the_clip_are_ignored_not_crashing():
    tex = cv2.GaussianBlur(np.random.default_rng(2).integers(0, 256, (PH, PW), np.uint8), (0, 0), 1.5)
    frames = [tex] * 5
    anchor = {"frame": 1, "x": 0.5, "y": 0.5}
    pts = track_with_corrections(frames, anchor, [{"frame": 5, "x": 0.1, "y": 0.1}, {"frame": -1, "x": 0, "y": 0}])
    assert len(pts) == 5 and all(p.valid for p in pts) and pts[4].source == "tracked"
    pts = track_with_corrections(frames, anchor, [{"frame": 4, "x": 0.5, "y": 0.5}])
    assert pts[4].source == "keyframe" and all(p.valid for p in pts)
    assert not any(p.valid for p in track_with_corrections(frames, {"frame": 7, "x": 0.5, "y": 0.5}, []))


def test_track_point_frame_range(stomp, gt):
    pts = track_point(stomp, 43, gt[43] / SCALE, frame_range=(40, 50))
    assert [p.frame for p in pts if p.valid] == list(range(40, 51))
    with pytest.raises(ValueError):
        track_point(stomp, 43, gt[43] / SCALE, frame_range=(50, 60))


def test_segment_keys_and_cached_assembly(stomp, gt):
    anchor, corrs = _kf(gt, 40), [_kf(gt, 70), _kf(gt, 10)]
    segs = plan_segments(anchor, corrs, 120)
    assert [(s.lo, s.hi) for s in segs] == [(0, 9), (11, 39), (41, 69), (71, 119)]
    keys = segment_keys("sha-a", anchor, corrs, None, {}, n_frames=120)
    assert len(keys) == len(segs) == len(set(keys))
    moved = [corrs[0], {**corrs[1], "x": corrs[1]["x"] + 0.01}]  # edit the frame-10 correction
    keys2 = segment_keys("sha-a", anchor, moved, None, {}, n_frames=120)
    assert [a == b for a, b in zip(keys, keys2)] == [False, False, True, True]
    assert set(segment_keys("sha-b", anchor, corrs, None, {}, n_frames=120)).isdisjoint(keys)
    assert segment_keys("sha-a", anchor, corrs, None, {"fb_max": 2.0}, n_frames=120)[3] != keys[3]
    pts = assemble(segs, [track_segment(stomp, s) for s in segs], 120, (PW, PH))
    assert pts == track_with_corrections(stomp, anchor, corrs)


def test_feature_less_frames_are_invalid():
    frames = [np.full((PH, PW), 128, np.uint8)] * 5
    pts = track_point(frames, 2, (320, 180))
    assert [p.valid for p in pts] == [False, False, True, False, False]


def test_point_leaving_the_frame_is_invalid():
    tex = cv2.GaussianBlur(np.random.default_rng(0).integers(0, 256, (PH, PW + 400), np.uint8), (0, 0), 1.0)
    frames = [np.ascontiguousarray(tex[:, 8 * k : 8 * k + PW]) for k in range(12)]  # pans: content moves left 8 px
    pts = track_point(frames, 0, (40.0, 180.0))
    valid = [p.frame for p in pts if p.valid]
    assert valid == list(range(6))  # x = 40 - 8f: frame 5 at x = 0 is inside, frame 6 at x = -8 is not
    assert all(abs(pts[f].x - (40 - 8 * f)) < 0.5 for f in valid)


def _pts(ys, valid=None, sources=None):
    n = len(ys)
    valid = valid or [True] * n
    sources = sources or ["tracked"] * n
    return [TrackPoint(f, float(f), float(y) if v else math.nan, v, 1.0 if v else 0.0, 0.0, 10, s)
            for f, (y, v, s) in enumerate(zip(ys, valid, sources))]


def test_smoothing_pins_keyframes_and_never_bridges_gaps():
    ys = [0.0] * 6 + [0.0] * 3 + [100.0] * 6
    valid = [True] * 6 + [False] * 3 + [True] * 6
    src = ["tracked"] * 12 + ["keyframe"] + ["tracked"] * 2
    ys[12] = 130.0  # a keyframe off the run's level
    out = smooth_track(_pts(ys, valid, src), sigma_frames=1.5)
    assert all(p.y == 0.0 for p in out[:6])  # the left run never sees the right one
    assert all(not p.valid and math.isnan(p.y) for p in out[6:9])
    assert out[12].y == 130.0 and out[12].source == "keyframe"
    assert all(100.0 <= p.y < 130.0 for p in out[9:12] + out[13:])


def test_smoothing_runs_shorter_than_the_kernel_stay_aligned():
    # sigma 1 -> 7-tap kernel; a 3-frame run is smoothed in place with the truncated kernel renormalized
    w1, w2 = math.exp(-0.5), math.exp(-2.0)
    edge = (10 * w1 + 20 * w2) / (1 + w1 + w2)
    out = smooth_track(_pts([0.0, 10.0, 20.0]), sigma_frames=1.0)
    assert [p.y for p in out] == pytest.approx([edge, 10.0, 20.0 - edge])
    pinned = smooth_track(_pts([0.0, 10.0, 20.0], sources=["keyframe", "tracked", "tracked"]), sigma_frames=1.0)
    assert pinned[0].y == 0.0 and pinned[1].y == pytest.approx(10.0) and pinned[2].y == pytest.approx(20.0 - edge)


def test_smoothing_reduces_jitter_without_lag():
    clean = np.linspace(0, 60, 40)
    noise = np.random.default_rng(1).normal(0, 1.0, 40)
    out = smooth_track(_pts(list(clean + noise)), sigma_frames=1.0)
    ys = np.array([p.y for p in out])
    assert np.abs(ys - clean)[3:-3].mean() < 0.7 * np.abs(noise[3:-3]).mean()
    assert abs(np.mean(ys[3:-3] - clean[3:-3])) < 0.3  # zero-phase: no systematic lag on a ramp


def test_smoothing_max_shift_bounds_the_correction():
    # a staircase (duplicated CFR frames of a 15 fps source): plain smoothing smears each jump over its neighbours
    ys = [0.0, 0.0, 30.0, 30.0, 60.0, 60.0, 90.0, 90.0]
    plain = smooth_track(_pts(ys), sigma_frames=1.0)
    assert max(abs(p.y - y) for p, y in zip(plain, ys, strict=True)) > 5.0
    bounded = smooth_track(_pts(ys), sigma_frames=1.0, max_shift=0.5)
    assert max(abs(p.y - y) for p, y in zip(bounded, ys, strict=True)) == pytest.approx(0.5)
    # jitter below the bound is still smoothed exactly as without it
    jitter = [10.0, 10.2, 9.9, 10.1, 10.0]
    assert [p.y for p in smooth_track(_pts(jitter), 1.0, max_shift=0.5)] == \
        [p.y for p in smooth_track(_pts(jitter), 1.0)]


def test_static_track_and_json():
    pts = static_track((320.0, 90.0), 4)
    assert all(p.valid and p.confidence == 1.0 and p.source == "static" for p in pts)
    js = track_to_json(pts, (PW, PH), (1280, 720))
    assert js["schema_version"] == 1 and js["algorithm"] == "lk_fb_median_v1"
    assert js["proxy_size"] == [640, 360] and js["output_size"] == [1280, 720]
    assert js["points"][0] == {"frame": 0, "x": 0.5, "y": 0.25, "valid": True, "confidence": 1.0}
    assert js["invalid_intervals"] == [] and js["params"]["fb_max"] == 3.0
    gap = _pts([1.0] * 6, valid=[False, True, False, False, True, False])
    assert track_to_json(gap, (PW, PH), (1280, 720))["invalid_intervals"] == [[0, 0], [2, 3], [5, 5]]
