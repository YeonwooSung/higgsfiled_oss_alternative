import numpy as np

from open_creative.product_lock.masks import (
    CORE_MIN_FRACTION,
    compute_core,
    core_fraction,
    mask_from_alpha,
    rasterize_boxes,
)


def square_mask(size=40, lo=10, hi=30):
    m = np.zeros((size, size), np.float32)
    m[lo:hi, lo:hi] = 1.0
    return m


def test_mask_from_alpha_scales_to_unit_range():
    alpha = np.array([[0, 128, 255]], np.uint8)
    m = mask_from_alpha(alpha)
    assert m.dtype == np.float32
    assert m[0, 0] == 0.0 and m[0, 2] == 1.0
    assert abs(m[0, 1] - 128 / 255) < 1e-6


def test_core_is_eroded_opaque_region():
    core = compute_core(square_mask(), None, erosion_px=3)
    ys, xs = np.nonzero(core)
    assert (ys.min(), ys.max(), xs.min(), xs.max()) == (13, 26, 13, 26)


def test_core_excludes_semi_transparent_pixels():
    m = square_mask()
    m[15:25, 15:25] = 0.7
    core = compute_core(m, None, erosion_px=0)
    assert not core[20, 20]
    assert core[11, 11]


def test_user_core_extends_core_but_only_inside_product():
    user = np.zeros((40, 40), bool)
    user[0:40, 10:12] = True  # crosses the product edge and the transparent area
    core = compute_core(square_mask(), user, erosion_px=3)
    assert core[15, 10]  # user-protected edge pixel inside the product survives erosion
    assert not core[5, 10]  # outside the product is cut away


def test_rasterize_boxes_marks_normalized_rect():
    r, clipped = rasterize_boxes([{"x": 0.25, "y": 0.5, "w": 0.5, "h": 0.25}], width=40, height=20)
    assert r.shape == (20, 40)
    ys, xs = np.nonzero(r)
    assert (ys.min(), ys.max(), xs.min(), xs.max()) == (10, 14, 10, 29)
    assert clipped is False


def test_rasterize_boxes_reports_clipping_outside_image():
    r, clipped = rasterize_boxes([{"x": 0.9, "y": 0.0, "w": 0.5, "h": 0.5}], width=10, height=10)
    assert clipped is True
    assert r[:, 9].any() and r.sum() == 5


def test_empty_mask_has_empty_core():
    m = np.zeros((10, 10), np.float32)
    core = compute_core(m, None)
    assert not core.any()
    assert core_fraction(core, m) == 0.0


def test_core_fraction_relative_to_product_area():
    m = square_mask()
    core = compute_core(m, None, erosion_px=3)
    assert abs(core_fraction(core, m) - (14 * 14) / (20 * 20)) < 1e-6
    assert CORE_MIN_FRACTION == 0.02
