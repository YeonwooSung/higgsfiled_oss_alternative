import time

import cv2
import numpy as np
import pytest
from open_creative.pocket_vfx.composite import (
    FrameInputs,
    composite_frame,
    composite_sequence,
    split_glow,
)
from open_creative.pocket_vfx.integrity import (
    IntegrityAccumulator,
    outside_roi_report,
    subject_core_cover,
)
from open_creative.pocket_vfx.procedural import EffectLayer, render_effect
from open_creative.pocket_vfx.timeline import EffectWindow

W, H = 1280, 720
WIN = EffectWindow(34, 43, 82)
IMPACT = {"scale": 1.0, "intensity": 0.8, "palette": "amber", "rotation_deg": 0, "squash": 0.5}


def _source(seed=0, w=W, h=H):
    return np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)


def _subject(w=W, h=H):
    m = np.zeros((h, w), np.float32)
    cv2.ellipse(m, (640, 380), (110, 260), 0, 0, 360, 1.0, -1)
    return cv2.GaussianBlur(m, (0, 0), 2.0)  # soft edge


def _core(mask, px=10):
    b = (mask > 0.5).astype(np.uint8)
    return cv2.erode(b, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))) > 0


def _layer(alpha, color=(0.9, 0.4, 0.1)):
    alpha = alpha.astype(np.float32)
    rgb = alpha[..., None] * np.asarray(color, np.float32)
    ys, xs = np.nonzero(alpha > 0)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if ys.size else None
    return EffectLayer(rgb_premult=rgb.astype(np.float32), alpha=alpha, bbox=bbox)


def _covering_ring(w=W, h=H):
    """A thick opaque ring whose disk passes straight through the subject."""
    a = np.zeros((h, w), np.float32)
    cv2.circle(a, (640, 380), 180, 1.0, 140)
    return _layer(a)


def _inp(source, effect=None, plate=None, subject_mask=None, front_glow=None, roi_limit=None):
    return FrameInputs(source, effect, plate, subject_mask, front_glow, roi_limit)


@pytest.mark.parametrize("order", ["behind_subject", "in_front", "split_glow"])
def test_no_effect_is_byte_identical_with_empty_roi(order):
    src = _source()
    out, roi = composite_frame(_inp(src, subject_mask=_subject()), order)
    assert out.dtype == np.uint8 and out.shape == src.shape
    assert np.array_equal(out, src)
    assert roi.dtype == np.bool_ and roi.shape == (H, W) and not roi.any()


def test_behind_subject_keeps_core_untouched():
    src, m = _source(1), _subject()
    ring = _covering_ring()
    core = _core(m)
    assert (ring.alpha[core] > 0.99).mean() > 0.3  # the ring really crosses the subject core
    out, roi = composite_frame(_inp(src, ring, subject_mask=m), "behind_subject")
    assert subject_core_cover(out, src, core) == 0.0
    assert np.array_equal(out[core], src[core])
    # the ring is visible around the subject
    visible = (ring.alpha > 0.99) & (m < 0.01)
    assert (np.abs(out.astype(int) - src.astype(int)).max(axis=2)[visible] > 2).mean() > 0.9
    assert outside_roi_report(src, out, roi)["max_err"] == 0


def test_in_front_covers_core():
    src, m = _source(2), _subject()
    ring = _covering_ring()
    core = _core(m)
    out, _ = composite_frame(_inp(src, ring, subject_mask=m), "in_front")
    expect = float((ring.alpha[core] > 0.99).mean())
    assert subject_core_cover(out, src, core) >= expect - 0.01 > 0.3


def test_plate_is_drawn_behind_and_masked():
    src, m = _source(3), _subject()
    core = _core(m)
    plate = _covering_ring()
    out, roi = composite_frame(_inp(src, None, plate=plate, subject_mask=m), "behind_subject")
    assert subject_core_cover(out, src, core) == 0.0
    assert roi[(plate.alpha > 0.5) & (m < 0.01)].all()


def test_plate_under_effect_order():
    src = np.full((40, 40, 3), 128, np.uint8)
    a = np.zeros((40, 40), np.float32)
    a[10:30, 10:30] = 1.0
    plate = _layer(a, (1.0, 0.0, 0.0))
    effect = _layer(a, (0.0, 0.0, 1.0))
    out, _ = composite_frame(_inp(src, effect, plate=plate), "in_front")
    assert tuple(out[20, 20]) == (0, 0, 255)  # effect on top of the plate


def test_split_glow_masks_behind_and_clips_glow_to_roi():
    src, m = _source(4), _subject()
    core = _core(m)
    ring = _covering_ring()
    glow_a = np.zeros((H, W), np.float32)
    cv2.rectangle(glow_a, (300, 100), (980, 660), 0.6, -1)  # spills over the ring's ROI
    glow = _layer(glow_a, (0.2, 0.3, 0.9))
    out, roi = composite_frame(_inp(src, ring, subject_mask=m, front_glow=glow), "split_glow")
    assert outside_roi_report(src, out, roi)["max_err"] == 0
    # glow is drawn over the subject inside the ring ROI...
    ring_roi = cv2.dilate((ring.alpha > 1 / 1024).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    over_core = core & ring_roi & (glow_a > 0)
    assert over_core.any() and subject_core_cover(out, src, over_core) > 0.95
    # ...but never outside the (dilated) behind-effect ROI
    far = (glow_a > 0) & ~cv2.dilate(ring_roi.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    assert far.any() and np.array_equal(out[far], src[far])


def test_split_glow_respects_roi_limit():
    src, m = _source(5), _subject()
    ring = _covering_ring()
    glow_a = np.zeros((H, W), np.float32)
    cv2.rectangle(glow_a, (300, 100), (980, 660), 0.6, -1)
    limit = np.zeros((H, W), bool)
    limit[:, :640] = True
    out, roi = composite_frame(
        _inp(src, ring, subject_mask=m, front_glow=_layer(glow_a), roi_limit=limit), "split_glow"
    )
    core = _core(m)
    right_core = core.copy()
    right_core[:, :640] = False
    left_core = core & ~right_core
    assert subject_core_cover(out, src, right_core) == 0.0  # no glow past the limit
    assert subject_core_cover(out, src, left_core) > 0.3
    assert outside_roi_report(src, out, roi)["max_err"] == 0


def test_unknown_layer_order():
    with pytest.raises(ValueError):
        composite_frame(_inp(_source()), "sideways")
    with pytest.raises(ValueError):
        composite_sequence(iter([]), lambda i, f: _inp(f), "sideways")


@pytest.mark.parametrize("seed", range(5))
def test_random_effect_never_changes_outside_roi(seed):
    rng = np.random.default_rng(100 + seed)
    src = _source(seed)
    a = np.zeros((H, W), np.float32)
    for _ in range(6):
        x, y = rng.integers(0, W), rng.integers(0, H)
        cv2.circle(a, (int(x), int(y)), int(rng.integers(5, 120)), float(rng.uniform(0.01, 1.0)), -1)
    a = cv2.GaussianBlur(a, (0, 0), float(rng.uniform(1, 25)))
    effect = _layer(a, tuple(rng.uniform(0, 1, 3)))
    order = ["behind_subject", "in_front", "split_glow"][seed % 3]
    out, roi = composite_frame(_inp(src, effect, subject_mask=_subject()), order)
    rep = outside_roi_report(src, out, roi)
    assert rep == {"max_err": 0, "changed_outside_px": 0}
    # roi includes every changed pixel and every pixel with visible alpha
    changed = (out != src).any(axis=2)
    assert not (changed & ~roi).any()
    if order == "in_front":
        assert roi[a > 1 / 1024].all()


def test_roi_is_alpha_dilated_by_two():
    src = _source(6, 64, 64)
    a = np.zeros((64, 64), np.float32)
    a[30, 30] = 1.0
    _, roi = composite_frame(_inp(src, _layer(a)), "in_front")
    ys, xs = np.nonzero(roi)
    assert (ys.min(), ys.max(), xs.min(), xs.max()) == (28, 32, 28, 32)


def test_real_impact_layer_integrity():
    src = _source(7)
    acc = IntegrityAccumulator()
    for f in (36, 43, 50, 70):
        layer = render_effect("impact_wave_v1", IMPACT, WIN, f, (640, 500), W, H, 3, None)
        out, roi = composite_frame(_inp(src, layer, subject_mask=_subject()), "behind_subject")
        acc.add(src, out, roi)
    res = acc.result()
    assert res["status"] == "pass" and res["max_err"] == 0 and res["frames"] == 4


def test_composite_sequence_streams():
    srcs = (_source(s, 64, 48) for s in range(5))
    seen = []

    def per_frame(i, src):
        seen.append(i)
        a = np.zeros((48, 64), np.float32)
        a[10:20, i * 5 : i * 5 + 10] = 0.5
        return FrameInputs(src, _layer(a), None, None, None, None)

    it = composite_sequence(srcs, per_frame, "behind_subject")
    first = next(it)
    assert seen == [0]  # lazy: one frame at a time
    rest = list(it)
    assert len(rest) == 4 and seen == [0, 1, 2, 3, 4]
    assert first[0].shape == (48, 64, 3) and first[1].shape == (48, 64)


def test_accumulator_fails_on_single_pixel_err_two():
    src = _source(8, 64, 48)
    roi = np.zeros((48, 64), bool)
    roi[10:20, 10:20] = True
    acc = IntegrityAccumulator()
    acc.add(src, src.copy(), roi)
    bad = src.copy()
    bad[40, 50, 1] = bad[40, 50, 1] + 2 if bad[40, 50, 1] < 254 else bad[40, 50, 1] - 2
    acc.add(src, bad, roi)
    acc.add(src, src.copy(), roi)
    res = acc.result()
    assert res == {"status": "fail", "max_err": 2, "worst_frame": 1, "frames": 3}
    assert outside_roi_report(src, bad, roi) == {"max_err": 2, "changed_outside_px": 1}


def test_accumulator_tolerates_one_and_ignores_inside_roi():
    src = _source(9, 64, 48)
    roi = np.zeros((48, 64), bool)
    roi[10:20, 10:20] = True
    out = src.astype(np.int16)
    out[15, 15] = 255 - out[15, 15]  # inside the ROI: anything goes
    out[40, 50, 0] += 1 if out[40, 50, 0] < 255 else -1
    acc = IntegrityAccumulator()
    acc.add(src, out.astype(np.uint8), roi)
    assert acc.result() == {"status": "pass", "max_err": 1, "worst_frame": 0, "frames": 1}


def test_accumulator_without_frames_is_invalid():
    assert IntegrityAccumulator().result()["status"] == "invalid"


def test_subject_core_cover_empty_core():
    src = _source(10, 16, 16)
    assert subject_core_cover(src, src, np.zeros((16, 16), bool)) == 0.0


@pytest.mark.slow
def test_full_frame_composite_speed():
    src, m = _source(11), _subject()
    effect = _layer(np.full((H, W), 0.3, np.float32))
    composite_frame(_inp(src, effect, subject_mask=m), "behind_subject")
    t0 = time.perf_counter()
    for _ in range(3):
        composite_frame(_inp(src, effect, subject_mask=m), "behind_subject")
    assert (time.perf_counter() - t0) / 3 < 0.05


def test_split_glow_layer_is_the_effect_at_35_percent():
    ring = _covering_ring()
    g = split_glow(ring)
    assert np.allclose(g.alpha, ring.alpha * 0.35) and np.allclose(g.rgb_premult, ring.rgb_premult * 0.35)
    assert g.bbox == ring.bbox and split_glow(None) is None
