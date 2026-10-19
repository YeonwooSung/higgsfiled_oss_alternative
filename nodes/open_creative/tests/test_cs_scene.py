import numpy as np
import pytest
from open_creative.camera_sketch.camera import intrinsics
from open_creative.camera_sketch.scene import (
    DAV2_SMALL,
    DepthConvention,
    build_mesh,
    decode_depth_png16,
    depth_to_z,
    encode_depth_png16,
    prepare_z,
)

H, W = 90, 160
K = intrinsics(50.0, W, H)


def box_depth(box_d=0.8, bg_d=0.3):
    d = np.full((H, W), bg_d, np.float32)
    d[30:60, 60:110] = box_d
    return d


def test_dav2_convention_and_near_bright_gives_smaller_z():
    assert DAV2_SMALL == DepthConvention("inverse_relative")
    z = depth_to_z(box_depth(), DAV2_SMALL)
    assert z[45, 80] < z[5, 5]
    assert np.isfinite(z).all()


def test_depth_relative_and_scale_offset():
    d = np.full((4, 4), 2.0, np.float32)
    assert np.allclose(depth_to_z(d, DepthConvention("depth_relative", scale=3.0)), 6.0)
    z = depth_to_z(d, DepthConvention("inverse_relative", scale=4.0, offset=2.0))
    assert np.allclose(z, 1.0)


def test_constant_and_zero_depth_finite_planar_all_faces_kept():
    for val in (0.5, 0.0):
        z = depth_to_z(np.full((H, W), val, np.float32), DAV2_SMALL)
        assert np.isfinite(z).all()
        zn, tz = prepare_z(z, (0.5, 0.5))
        assert np.isfinite(zn).all() and np.allclose(zn, 1.0, atol=1e-4)
        m = build_mesh(zn, K, stride=4)
        gh, gw = m.grid_shape
        assert len(m.faces) == 2 * (gh - 1) * (gw - 1)
        assert np.allclose(m.vertices[:, 2], 1.0, atol=1e-4)


def test_prepare_z_target_is_one():
    z = depth_to_z(box_depth(), DAV2_SMALL)
    zn, tz = prepare_z(z, (85 / W, 45 / H))
    r = round(0.02 * H)
    patch = zn[45 - r : 45 + r + 1, 85 - r : 85 + r + 1]
    assert np.median(patch) == pytest.approx(1.0, abs=0.02)
    assert tz > 0 and np.isfinite(tz)


def test_box_on_plane_cuts_faces_along_boundary():
    z = depth_to_z(box_depth(box_d=1.0, bg_d=0.25), DAV2_SMALL)
    m = build_mesh(z / np.median(z), K, stride=2)
    gh, gw = m.grid_shape
    assert 0 < len(m.faces) < 2 * (gh - 1) * (gw - 1)
    zv = m.vertices[:, 2]
    ratio = zv[m.faces].max(1) / zv[m.faces].min(1)
    assert (ratio - 1 <= 0.08 + 1e-6).all()
    # a face never spans both box and background
    ys = (m.uv[m.faces][:, :, 1] * H)
    xs = (m.uv[m.faces][:, :, 0] * W)
    inbox = (ys > 30) & (ys < 60) & (xs > 60) & (xs < 110)
    assert not (inbox.any(1) & ~inbox.all(1) & (ratio > 1.5)).any()


def test_mesh_deterministic_backprojection_and_uv():
    z = np.full((H, W), 2.0, np.float32)
    m = build_mesh(z, K, stride=4)
    assert m.vertices.dtype == np.float32 and m.faces.dtype == np.int32
    assert m.uv.shape == (len(m.vertices), 2)
    # Edge-aligned grid: vertex 0 = image corner (0, 0); last vertex = (W, H).
    assert m.grid_shape == (-(-H // 4) + 1, -(-W // 4) + 1)
    X = 2.0 * np.linalg.inv(K) @ np.array([0.0, 0.0, 1.0])
    assert np.allclose(m.vertices[0], X, atol=1e-5)
    assert np.allclose(m.uv[0], [0.0, 0.0], atol=1e-7)
    Xe = 2.0 * np.linalg.inv(K) @ np.array([W, H, 1.0])
    assert np.allclose(m.vertices[-1], Xe, atol=1e-5)
    assert np.allclose(m.uv[-1], [1.0, 1.0], atol=1e-7)
    m2 = build_mesh(z, K, stride=4)
    assert np.array_equal(m.faces, m2.faces)


def test_partial_stride_reaches_image_edge_and_samples_clamped_pixel():
    w, h = 10, 7
    k = intrinsics(50.0, w, h)
    z = np.tile(np.arange(1, w + 1, dtype=np.float32), (h, 1))  # z = col + 1
    m = build_mesh(z, k, stride=4)
    assert m.grid_shape == (3, 4)  # rows 0,4,7; cols 0,4,8,10
    u = m.uv[:4, 0] * w
    assert np.allclose(u, [0, 4, 8, 10])
    # depth at clamp(floor(x), 0, W-1): cols 0,4,8,9 -> z 1,5,9,10
    assert np.allclose(m.vertices[:4, 2], [1, 5, 9, 10])
    assert np.allclose(m.uv[-1], [1.0, 1.0])


def test_nan_vertices_drop_faces():
    z = np.full((H, W), 1.0, np.float32)
    z[:, 80:] = np.nan
    m = build_mesh(z, K, stride=4)
    assert np.isfinite(m.vertices).all()
    assert len(m.faces) > 0
    assert (m.uv[m.faces][:, :, 0] * W < 80).all()


def test_target_on_nan_sky_falls_back_to_nearest_finite_and_never_nan():
    z = depth_to_z(box_depth(), DAV2_SMALL)
    z[:40, :] = np.nan  # sky
    zn, tz = prepare_z(z, (0.5, 5 / H))
    assert np.isfinite(tz)
    # nearest finite pixel to (80, 5) is (80, 40), on the box -> box z normalises to 1
    assert np.nanmedian(zn[41:45, 75:85]) == pytest.approx(1.0, abs=0.05)
    assert np.isnan(zn[:40]).all()


def test_all_nan_raises():
    with pytest.raises(ValueError):
        prepare_z(np.full((H, W), np.nan, np.float32), (0.5, 0.5))


def test_png16_roundtrip():
    rng = np.random.default_rng(1)
    d = rng.uniform(-3.0, 7.0, (40, 50)).astype(np.float32)
    data, meta = encode_depth_png16(d)
    assert data[:4] == b"\x89PNG"
    back = decode_depth_png16(data, meta)
    assert back.dtype == np.float32
    assert np.abs(back - d).max() <= (meta["max"] - meta["min"]) / 65535 + 1e-6
    c, cm = encode_depth_png16(np.full((5, 5), 0.3, np.float32))
    assert np.allclose(decode_depth_png16(c, cm), 0.3, atol=1e-6)


def test_sky_inverse_zero_is_capped_and_cut_from_building():
    from open_creative.camera_sketch.scene import Z_FAR_CAP

    d = np.full((H, W), 0.5, np.float32)
    d[:30, :] = 0.0  # sky: infinity
    z = depth_to_z(d, DAV2_SMALL)
    zn, _ = prepare_z(z, (0.5, 0.8))
    assert np.isfinite(zn).all()
    assert zn.max() == pytest.approx(Z_FAR_CAP) and zn.max() <= Z_FAR_CAP
    m = build_mesh(zn, K, stride=2)
    zv = m.vertices[:, 2]
    fz = zv[m.faces]
    sky = fz.min(1) > 0.5 * Z_FAR_CAP
    assert sky.any()  # sky-to-sky faces kept
    assert not ((fz.max(1) > 0.5 * Z_FAR_CAP) & (fz.min(1) < 5)).any()  # no sky-building faces
