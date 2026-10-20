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


def test_linear_to_srgb_matches_reference_search():
    from open_creative.pocket_vfx.color import _LUT, _MID

    rng = np.random.default_rng(0)
    mid32 = _MID.astype(np.float32)
    xs32 = np.concatenate(
        [rng.random(200_000, dtype=np.float32), mid32, np.nextafter(mid32, 2), np.nextafter(mid32, -1), _LUT]
    ).astype(np.float32)
    xs64 = np.concatenate([rng.random(200_000), _MID, np.nextafter(_MID, 2), np.nextafter(_MID, -1)])
    for xs in (xs32, xs64):
        ref = np.searchsorted(_MID, np.clip(xs.astype(np.float64), 0.0, 1.0)).astype(np.uint8)
        out = linear_to_srgb(xs)
        assert out.dtype == np.uint8 and np.array_equal(out, ref)


def test_over_premult():
    dst = np.full((2, 2, 3), 0.5, np.float32)
    src = np.full((2, 2, 3), 0.25, np.float32)
    out = over_premult(dst, src, np.full((2, 2), 0.5, np.float32))
    assert np.allclose(out, 0.5) and out.dtype == np.float32
    assert np.array_equal(over_premult(dst, np.zeros_like(src), np.zeros((2, 2))), dst)
