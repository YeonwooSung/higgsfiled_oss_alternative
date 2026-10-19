import numpy as np
import pytest
from open_creative.camera_sketch.camera import intrinsics, pose, project
from open_creative.camera_sketch.guides import (
    DEPTH_FILL_STRATEGY,
    DepthGuideRange,
    crop_from_model,
    depth_guide,
    pad_to_model,
)
from open_creative.camera_sketch.render import Frame, rasterize, vertex_colors
from open_creative.camera_sketch.scene import build_mesh

W, H = 320, 180


def three_level_scene():
    """Box z=1 (near), plane z=2, far strip z=4 on the right."""
    K = intrinsics(50.0, W, H)
    z = np.full((H, W), 2.0, np.float32)
    z[:, 260:] = 4.0
    z[60:120, 120:200] = 1.0
    img = np.full((H, W, 3), 128, np.uint8)
    return K, build_mesh(z, K, stride=1), img


def test_depth_guide_fixed_range_same_point_same_value():
    K, mesh, img = three_level_scene()
    col = vertex_colors(mesh, img)
    T0 = np.eye(4)
    T119 = pose(np.eye(3), [0.08, 0.0, 0.0])  # pure x slide: camera z of every point kept
    f0 = rasterize(mesh, col, K, T0, W, H)
    f119 = rasterize(mesh, col, K, T119, W, H)
    assert not f119.valid.all()  # disocclusion exists -> fill path exercised
    rng = DepthGuideRange.from_frame(f0)
    assert rng.near == pytest.approx(1.0, abs=1e-5) and rng.far == pytest.approx(4.0, abs=1e-5)
    g0, s0 = depth_guide(f0, rng)
    g1, s1 = depth_guide(f119, rng)
    assert s0 == s1 == DEPTH_FILL_STRATEGY
    assert g0.dtype == np.uint8 and g0.shape == (H, W)
    X = np.array([[-0.4, -0.2, 2.0], [-0.5, 0.25, 2.0], [0.0, 0.0, 1.0]])  # plane x2, box
    uv0, _ = project(K, T0, X)
    uv1, _ = project(K, T119, X)
    for a, b in zip(uv0, uv1):
        v0 = g0[int(a[1]), int(a[0])]
        v1 = g1[int(b[1]), int(b[0])]
        assert v0 == v1
    # Inverse-depth mapping, near = white: plane (1/2 - 1/4) / (1 - 1/4) = 1/3.
    assert g0[int(uv0[0, 1]), int(uv0[0, 0])] == round(255 / 3)
    assert g0[int(uv0[2, 1]), int(uv0[2, 0])] == 255
    assert g0[90, 300] == 0
    # Invalid pixels take their nearest valid value (no black / white holes).
    hole = ~f119.valid
    hole[[0, -1], :] = False
    hole[:, [0, -1]] = False
    assert hole.any()
    assert set(np.unique(g1[hole]).tolist()) <= {0, 85, 255}


def test_depth_guide_range_degenerate_and_empty():
    z = np.ones((10, 10), np.float32)
    fr = Frame(np.zeros((10, 10, 3), np.uint8), z, np.ones((10, 10), bool))
    rng = DepthGuideRange.from_frame(fr)
    assert rng.far > rng.near
    g, _ = depth_guide(fr, rng)
    assert (g == 255).all()
    empty = Frame(fr.rgb, np.full((10, 10), np.inf, np.float32), np.zeros((10, 10), bool))
    with pytest.raises(ValueError):
        DepthGuideRange.from_frame(empty)
    with pytest.raises(ValueError):
        DepthGuideRange(2.0, 1.0)


def _frame(i: int, h: int, w: int, ch: int | None) -> np.ndarray:
    rs = np.random.default_rng(i)
    shape = (h, w) if ch is None else (h, w, ch)
    return rs.integers(0, 256, shape, dtype=np.uint8)


@pytest.mark.parametrize("ch", [3, None])
def test_pad_to_model_then_crop_round_trips(ch):
    frames = (_frame(i, 720, 1280, ch) for i in range(120))
    padded = pad_to_model(frames, 1280, 736, 121)
    seen = []

    def tap(it):
        for p in it:
            assert p.shape[:2] == (736, 1280)
            seen.append(p[:, :, 0].copy() if p.ndim == 3 else p.copy())
            yield p

    tapped = tap(padded)
    out = list(crop_from_model(tapped, 1280, 720, n=120))
    assert len(seen) == 120  # crop pulls exactly n frames
    for _ in tapped:  # drain the 121st (repeated) padded frame
        pass
    assert len(seen) == 121 and (seen[-1] == seen[-2]).all()  # last frame repeated
    # Edge padding: the 8 rows above/below equal the first/last content row.
    assert (seen[0][:8] == seen[0][8]).all() and (seen[0][-8:] == seen[0][-9]).all()
    assert len(out) == 120
    for i, f in enumerate(out):
        assert f.tobytes() == _frame(i, 720, 1280, ch).tobytes()


def test_crop_from_model_pulls_exactly_n_frames():
    pulled = []

    def src():
        for i in range(10):
            pulled.append(i)
            yield np.zeros((8, 8), np.uint8)

    assert len(list(crop_from_model(src(), 4, 4, n=5))) == 5
    assert len(pulled) == 5


def test_pad_to_model_rejects_bad_shapes():
    with pytest.raises(ValueError):
        list(pad_to_model(iter([np.zeros((10, 10), np.uint8)]), 8, 10, 2))
    with pytest.raises(ValueError):
        list(pad_to_model(iter([np.zeros((10, 10), np.uint8)] * 3), 10, 10, 2))
    with pytest.raises(ValueError):
        list(pad_to_model(iter([]), 10, 10, 2))
    with pytest.raises(ValueError):
        list(crop_from_model(iter([np.zeros((10, 10), np.uint8)]), 10, 10, n=2))
