import numpy as np
from open_creative.pocket_vfx.color import linear_to_srgb, over_premult, srgb_to_linear


def test_lut_round_trip_identity():
    u = np.arange(256, dtype=np.uint8)
    lin = srgb_to_linear(u)
    assert lin.dtype == np.float32 and lin[0] == 0 and lin[255] == 1
    assert np.array_equal(linear_to_srgb(lin), u)


def test_linear_to_srgb_clips():
    out = linear_to_srgb(np.array([-0.5, 2.0]))
    assert out.dtype == np.uint8 and list(out) == [0, 255]


def test_over_premult():
    dst = np.full((2, 2, 3), 0.5, np.float32)
    src = np.full((2, 2, 3), 0.25, np.float32)
    out = over_premult(dst, src, np.full((2, 2), 0.5, np.float32))
    assert np.allclose(out, 0.5) and out.dtype == np.float32
    assert np.array_equal(over_premult(dst, np.zeros_like(src), np.zeros((2, 2))), dst)
