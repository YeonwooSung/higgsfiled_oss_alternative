import itertools
import time

import cv2
import numpy as np
import pytest
from open_creative.pocket_vfx.presets import load_palettes
from open_creative.pocket_vfx.procedural import EffectLayer, impact_curve, render_effect
from open_creative.pocket_vfx.timeline import EffectWindow

W, H = 1280, 720
WIN = EffectWindow(34, 43, 82)
IMPACT = {
    "scale": 1.0,
    "intensity": 0.8,
    "palette": "amber",
    "rotation_deg": 0,
    "squash": 0.5,
}
AURA = {"scale": 1.0, "intensity": 0.8, "palette": "violet", "rotation_deg": 0}
BLINK = {"scale": 1.0, "intensity": 0.8, "palette": "cyan", "rotation_deg": 0}


def _impact(frame, window=WIN, anchor=(640, 500), seed=7, params=IMPACT, w=W, h=H):
    return render_effect("impact_wave_v1", params, window, frame, anchor, w, h, seed, None)


def _body_mask(w=W, h=H):
    m = np.zeros((h, w), np.float32)
    cv2.ellipse(m, (640, 420), (90, 220), 0, 0, 360, 1.0, -1)
    cv2.circle(m, (640, 170), 55, 1.0, -1)
    return m


def _check_layer(layer: EffectLayer, w=W, h=H, palette="amber"):
    assert layer.rgb_premult.shape == (h, w, 3) and layer.rgb_premult.dtype == np.float32
    assert layer.alpha.shape == (h, w) and layer.alpha.dtype == np.float32
    assert layer.alpha.min() >= 0 and layer.alpha.max() <= 1
    vmax = max(max(v) for v in load_palettes()[palette].values())
    assert layer.rgb_premult.min() >= 0
    assert np.all(layer.rgb_premult <= layer.alpha[..., None] * vmax + 1e-6)
    # bbox is the tight (exclusive-end) box of alpha > 0
    ys, xs = np.nonzero(layer.alpha > 0)
    assert layer.bbox == (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    x0, y0, x1, y1 = layer.bbox
    outside = layer.alpha.copy()
    outside[y0:y1, x0:x1] = 0
    assert not outside.any()


def test_impact_invisible_before_start_and_after_end():
    assert _impact(WIN.start - 1) is None
    assert _impact(0) is None
    assert _impact(WIN.end + 1) is None
    first = _impact(WIN.start)
    assert first is not None and first.alpha.sum() > 0  # first visible frame = start
    _check_layer(first)


def test_impact_peak_has_largest_alpha_sum():
    for window in (
        WIN,
        EffectWindow(10, 12, 17),
        EffectWindow(0, 0, 40),
        EffectWindow(0, 12, 20),
    ):
        sums = {}
        for f in range(window.start, window.end + 1):
            layer = _impact(f, window=window)
            sums[f] = 0.0 if layer is None else float(layer.alpha.sum())
        assert abs(max(sums, key=sums.get) - window.peak) <= 1, (window, sums)


def test_impact_charge_ramps_up_to_peak():
    sums = [float(_impact(f).alpha.sum()) for f in range(WIN.start, WIN.peak + 1)]
    assert all(b > a for a, b in itertools.pairwise(sums))


def test_impact_radius_monotone_and_alpha_decays_after_peak():
    short = min(W, H)
    curve = [impact_curve(WIN, f, IMPACT, short) for f in range(WIN.peak, WIN.end + 1)]
    radii = [r for r, _ in curve]
    alphas = [a for _, a in curve]
    assert radii[0] == 0 and alphas[0] == pytest.approx(0.8)
    assert all(b >= a for a, b in itertools.pairwise(radii))
    assert all(b <= a for a, b in itertools.pairwise(alphas))
    assert radii[-1] == pytest.approx(0.35 * short) and alphas[-1] == 0
    s = (50 - WIN.peak) / (WIN.end - WIN.peak)
    r, a = impact_curve(WIN, 50, {**IMPACT, "scale": 1.5}, short)
    assert r == pytest.approx(1.5 * 0.35 * short * (1 - (1 - s) ** 3))
    assert a == pytest.approx(0.8 * (1 - s) ** 1.5)
    assert impact_curve(WIN, WIN.peak - 1, IMPACT, short) is None


def test_impact_ring_extent_follows_curve():
    layer = _impact(70)
    _check_layer(layer)
    r, _ = impact_curve(WIN, 70, IMPACT, min(W, H))
    # squash 0.5, rotation 0: horizontal semi-axis ~R, vertical ~R/2 (dust may rise above)
    row = layer.alpha[500]
    xs = np.nonzero(row > 0.05)[0]
    assert abs((xs.max() - 640) - r) < 0.06 * min(W, H)
    assert layer.alpha[500, 640] < 0.05  # hollow ring, flash long gone


def test_impact_deterministic_in_seed():
    a, b = _impact(60, seed=3), _impact(60, seed=3)
    assert np.array_equal(a.alpha, b.alpha) and np.array_equal(a.rgb_premult, b.rgb_premult)
    assert a.bbox == b.bbox
    c = _impact(60, seed=4)
    assert not np.array_equal(a.alpha, c.alpha)
    # the ring itself is seed-independent: the difference is only the dust
    assert np.abs(a.alpha - c.alpha).max() < 0.8


def test_impact_anchor_at_corner_clips_bbox():
    for anchor in ((0, 0), (W - 1, H - 1), (-50, H + 30)):
        for f in (WIN.start, WIN.peak, 60):
            layer = _impact(f, anchor=anchor)
            if layer is not None:
                _check_layer(layer)
                x0, y0, x1, y1 = layer.bbox
                assert 0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H


def test_impact_far_offscreen_anchor_is_none():
    assert _impact(60, anchor=(-5000, -5000)) is None


def test_impact_zero_intensity_is_none():
    assert _impact(WIN.peak, params={**IMPACT, "intensity": 0.0}) is None


def test_impact_rotation_and_squash():
    flat = _impact(70, params={**IMPACT, "squash": 0.2})
    round_ = _impact(70, params={**IMPACT, "squash": 1.0})
    assert (flat.bbox[3] - flat.bbox[1]) < (round_.bbox[3] - round_.bbox[1])
    rot = _impact(70, params={**IMPACT, "squash": 0.2, "rotation_deg": 90})
    assert (rot.bbox[2] - rot.bbox[0]) < (rot.bbox[3] - rot.bbox[1])


def test_impact_portrait_and_palette():
    layer = _impact(50, anchor=(360, 900), w=720, h=1280, params={**IMPACT, "palette": "cyan"})
    _check_layer(layer, w=720, h=1280, palette="cyan")
    # hot crest takes the palette's blue-dominant colour (cyan)
    x0, y0, x1, y1 = layer.bbox
    rgb = layer.rgb_premult[y0:y1, x0:x1].reshape(-1, 3).sum(0)
    assert rgb[2] > rgb[0]


def test_impact_requires_anchor():
    with pytest.raises(ValueError):
        _impact(50, anchor=None)


def test_impact_frame_render_time():
    _impact(60)  # warm-up
    t0 = time.perf_counter()
    for f in (43, 50, 60, 70):
        _impact(f)
    assert (time.perf_counter() - t0) / 4 < 0.1


def test_unknown_preset_and_palette():
    with pytest.raises(ValueError):
        render_effect("nope", IMPACT, WIN, 50, (640, 360), W, H, 1, None)
    with pytest.raises(ValueError):
        _impact(50, params={**IMPACT, "palette": "pink"})


def test_preset_without_renderer_is_rejected(monkeypatch):
    from open_creative.pocket_vfx import procedural

    presets = procedural.load_presets()
    fake = {**presets, "frost_nova_v1": {**presets["blink_out_v1"], "id": "frost_nova_v1"}}
    monkeypatch.setattr(procedural, "load_presets", lambda: fake)
    with pytest.raises(ValueError, match="no procedural renderer"):
        render_effect("frost_nova_v1", BLINK, WIN, 50, None, W, H, 1, _body_mask())


def test_aura_requires_mask_or_anchor():
    with pytest.raises(ValueError):
        render_effect("energy_aura_v1", AURA, WIN, 50, None, W, H, 1, None)


def test_aura_anchor_local_glow():
    layer = render_effect("energy_aura_v1", AURA, WIN, 50, (900, 300), W, H, 1, None)
    _check_layer(layer, palette="violet")
    assert layer.alpha[300, 900] > 0.3
    r = 0.12 * min(W, H)
    x0, y0, x1, y1 = layer.bbox
    assert x1 - x0 < 6 * r and y1 - y0 < 6 * r
    assert render_effect("energy_aura_v1", AURA, WIN, WIN.start - 1, (900, 300), W, H, 1, None) is None
    assert render_effect("energy_aura_v1", AURA, WIN, WIN.end + 1, (900, 300), W, H, 1, None) is None


def test_aura_mask_band_not_filled_inside_silhouette():
    mask = _body_mask()
    layer = render_effect("energy_aura_v1", AURA, WIN, 50, None, W, H, 1, mask)
    _check_layer(layer, palette="violet")
    core = cv2.erode((mask > 0.5).astype(np.uint8), np.ones((31, 31), np.uint8)).astype(bool)
    assert layer.alpha[core].max() == 0
    # glow hugs the contour just outside the silhouette
    assert layer.alpha[420, 640 + 90 + 4] > 0.2
    assert layer.alpha[420, 640 + 90 + 200] == 0


def test_aura_band_does_not_leak_inside_a_silhouette_touching_the_frame_edge():
    mask = np.zeros((H, W), np.float32)
    cv2.ellipse(mask, (640, 690), (120, 260), 0, 0, 360, 1.0, -1)  # cut by the bottom edge
    assert mask[-1].any()
    layer = render_effect("energy_aura_v1", AURA, WIN, 50, None, W, H, 1, mask)
    _check_layer(layer, palette="violet")
    core = cv2.erode((mask > 0.5).astype(np.uint8), np.ones((31, 31), np.uint8)).astype(bool)
    assert core[-1].any()  # the eroded silhouette reaches the edge rows (erode does not erode at the border)
    assert layer.alpha[core].max() == 0
    assert layer.alpha[690, 640 - 120 - 4] > 0.1  # the glow still hugs the visible contour


def test_aura_anchor_glow_is_masked_out_inside_the_silhouette():
    mask = _body_mask()
    layer = render_effect("energy_aura_v1", AURA, WIN, 50, (640, 420), W, H, 1, mask)  # anchor on the torso
    _check_layer(layer, palette="violet")
    core = cv2.erode((mask > 0.5).astype(np.uint8), np.ones((31, 31), np.uint8)).astype(bool)
    assert layer.alpha[core].max() == 0
    free = render_effect("energy_aura_v1", AURA, WIN, 50, (640, 420), W, H, 1, None)
    assert free.alpha[420, 640] > 0.3  # without a mask the local glow sits on the anchor


def test_aura_flickers_and_is_deterministic():
    mask = _body_mask()
    a = render_effect("energy_aura_v1", AURA, WIN, 50, None, W, H, 1, mask)
    b = render_effect("energy_aura_v1", AURA, WIN, 50, None, W, H, 1, mask)
    c = render_effect("energy_aura_v1", AURA, WIN, 51, None, W, H, 1, mask)
    assert np.array_equal(a.alpha, b.alpha) and np.array_equal(a.rgb_premult, b.rgb_premult)
    assert not np.array_equal(a.alpha, c.alpha)


def test_aura_mask_shape_mismatch():
    with pytest.raises(ValueError):
        render_effect(
            "energy_aura_v1",
            AURA,
            WIN,
            50,
            None,
            W,
            H,
            1,
            np.zeros((10, 10), np.float32),
        )


def test_blink_particles_between_peak_and_end():
    mask = _body_mask()
    assert render_effect("blink_out_v1", BLINK, WIN, WIN.peak - 1, None, W, H, 1, mask) is None
    assert render_effect("blink_out_v1", BLINK, WIN, WIN.end + 1, None, W, H, 1, mask) is None
    layer = render_effect("blink_out_v1", BLINK, WIN, 55, None, W, H, 1, mask)
    _check_layer(layer, palette="cyan")
    ring = cv2.dilate((mask > 0.5).astype(np.uint8), np.ones((121, 121), np.uint8)).astype(bool)
    assert layer.alpha[~ring].max() < 0.05  # particles start at the subject boundary
    with pytest.raises(ValueError):
        render_effect("blink_out_v1", BLINK, WIN, 55, (640, 360), W, H, 1, None)
