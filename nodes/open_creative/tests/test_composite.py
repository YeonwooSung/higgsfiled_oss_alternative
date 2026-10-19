import numpy as np
import pytest

from open_creative.product_lock.composite import LayerConfig, composite_frame, composite_sequence
from open_creative.product_lock.geometry import place_product
from open_creative.product_lock.layers import add_particles, adjust_brightness, apply_shadow, contact_shadow
from open_creative.product_lock.locked import build_locked_product, expected_reference
from open_creative.product_lock.masks import compute_core

OUT_W, OUT_H = 360, 640


def make_product(h=200, w=120, seed=0):
    rng = np.random.default_rng(seed)
    rgb = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    mask = np.zeros((h, w), np.float32)
    mask[20:180, 10:110] = 1.0
    mask[20:180, 8:10] = 0.5  # soft left edge
    core = compute_core(mask, None, erosion_px=3)
    return rgb, mask, core


def make_locked(center_x=0.5, width_ratio=0.4, feather_px=2.0):
    rgb, mask, core = make_product()
    pl = place_product(rgb.shape[1], rgb.shape[0], OUT_W, OUT_H, center_x, 0.55, width_ratio)
    return build_locked_product(rgb, mask, core, pl, feather_px=feather_px)


def random_bg(seed=1):
    return np.random.default_rng(seed).integers(0, 256, (OUT_H, OUT_W, 3), dtype=np.uint8)


def core_pixels(frame, locked):
    ref, core = expected_reference(locked)
    fy, fx, py, px = locked.placement.visible(OUT_W, OUT_H)
    c = core[py, px]
    return frame[fy, fx][c], ref[py, px][c]


def test_locked_product_matches_placement_size():
    locked = make_locked()
    pl = locked.placement
    assert locked.rgb.shape == (pl.h, pl.w, 3) and locked.rgb.dtype == np.uint8
    assert locked.alpha.shape == (pl.h, pl.w)
    assert locked.core.any()
    assert np.all(locked.alpha[locked.core] == 1.0)


def test_feather_softens_only_outside_core():
    locked = make_locked(feather_px=2.0)
    soft = (locked.alpha > 0) & (locked.alpha < 1)
    assert soft.any()
    assert not (soft & locked.core).any()


def test_composite_preserves_core_exactly():
    locked = make_locked()
    out = composite_frame(random_bg(), locked, None)
    got, ref = core_pixels(out, locked)
    assert got.size > 0
    assert np.array_equal(got, ref)


def test_composite_outside_product_is_background():
    locked = make_locked()
    bg = random_bg()
    out = composite_frame(bg, locked, None)
    assert np.array_equal(out[:5], bg[:5])


def test_layers_do_not_change_core():
    locked = make_locked()
    cfg = LayerConfig(brightness=0.2, shadow_enabled=True, shadow_opacity=0.6, shadow_blur_px=40,
                      particles="bokeh", seed=7)
    bgs = [random_bg(s) for s in range(4)]
    frames = list(composite_sequence(bgs, locked, cfg))
    assert len(frames) == 4
    for f in frames:
        got, ref = core_pixels(f, locked)
        assert np.array_equal(got, ref)


def test_shadow_darkens_floor_below_product():
    locked = make_locked(feather_px=0)
    shadow = contact_shadow(locked, OUT_W, OUT_H, opacity=0.5, blur_px=10)
    assert shadow.shape == (OUT_H, OUT_W)
    pl = locked.placement
    assert shadow[pl.y0 + pl.h - 1, pl.x0 + pl.w // 2] > 0
    assert shadow[0, 0] == 0 and shadow.max() <= 0.5 + 1e-6
    bg = np.full((OUT_H, OUT_W, 3), 200, np.uint8)
    assert apply_shadow(bg, shadow).min() < 200


def test_brightness_and_particles_are_deterministic():
    bg = random_bg()
    assert adjust_brightness(bg, 0.0).tolist() == bg.tolist()
    assert adjust_brightness(np.full((2, 2, 3), 100, np.uint8), -0.2)[0, 0, 0] == 80
    a = add_particles(bg, t=3, seed=5, kind="bokeh")
    b = add_particles(bg, t=3, seed=5, kind="bokeh")
    assert np.array_equal(a, b) and not np.array_equal(a, bg)
    assert np.array_equal(add_particles(bg, t=3, seed=5, kind="none"), bg)
    assert not np.array_equal(add_particles(bg, t=0, seed=5, kind="neon"), add_particles(bg, t=60, seed=5, kind="neon"))


@pytest.mark.parametrize("center_x", [0.02, 0.98])
def test_product_partly_outside_frame_composites(center_x):
    locked = make_locked(center_x=center_x, width_ratio=0.7)
    cfg = LayerConfig(shadow_enabled=True, shadow_opacity=0.3, shadow_blur_px=10)
    (out,) = composite_sequence([random_bg()], locked, cfg)
    got, ref = core_pixels(out, locked)
    assert np.array_equal(got, ref)
