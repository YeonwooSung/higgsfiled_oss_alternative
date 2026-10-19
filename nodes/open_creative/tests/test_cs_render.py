import inspect
import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from open_creative.camera_sketch.camera import cover_fit, intrinsics, output_intrinsics, pose
from open_creative.camera_sketch.path import SceneInfo, compile_path
from open_creative.camera_sketch.render import (
    FAR,
    Frame,
    fill_small_holes,
    hole_ratio,
    rasterize,
    render_sequence,
    target_visibility,
    vertex_colors,
)
from open_creative.camera_sketch.scene import (
    DAV2_SMALL,
    Z_FAR_CAP,
    build_mesh,
    decode_depth_png16,
    depth_to_z,
    prepare_z,
)

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "fixtures" / "camera-sketch"
META = json.loads((FIX / "scenes.json").read_text())["scenes"]


def load_rgb(name: str) -> np.ndarray:
    bgr = cv2.imread(str(FIX / META[name]["image"]), cv2.IMREAD_COLOR)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def load_scene(name: str, focal: float = 50.0):
    meta = META[name]
    d = decode_depth_png16((FIX / meta["depth"]).read_bytes(), meta["depth_png16"])
    u, v = meta["target_uv01"]
    zn, _ = prepare_z(depth_to_z(d, DAV2_SMALL), (u, v))
    W, H = meta["width"], meta["height"]
    K = intrinsics(focal, W, H)
    ray = np.linalg.solve(K, [u * W, v * H, 1.0])
    tz = float(zn[int(v * H), int(u * W)])
    return zn, K, SceneInfo(K, W, H, ray * tz, (u, v), depth_convention=DAV2_SMALL)


def exact_cover_fit_image(img: np.ndarray, K_fit) -> np.ndarray:
    """Reference: the camera module's CoverFit (continuous coords, centers at i+0.5) as a
    bilinear warp in OpenCV index coords (centers at i): out = s*src + (o + 0.5*s - 0.5)."""
    s = K_fit.scale
    M = np.array([[s, 0.0, K_fit.offset_x + 0.5 * s - 0.5],
                  [0.0, s, K_fit.offset_y + 0.5 * s - 0.5]])
    return cv2.warpAffine(img, M, (K_fit.out_w, K_fit.out_h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


# ---- identity pose ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["room", "facade"])
@pytest.mark.parametrize("stride", [1, 2, 4])
def test_identity_pose_full_frame_has_no_holes(name, stride):
    """Edge-aligned mesh covers the whole cover-fit output (no border ring)."""
    img = load_rgb(name)
    H, W = img.shape[:2]
    K_src = intrinsics(50.0, W, H)
    mesh = build_mesh(np.ones((H, W), np.float32), K_src, stride=stride)
    for out_w, out_h in ((1280, 720), (720, 1280)):
        K_out = output_intrinsics(K_src, cover_fit(W, H, out_w, out_h))
        fr = rasterize(mesh, vertex_colors(mesh, img), K_out, np.eye(4), out_w, out_h)
        assert hole_ratio(fr) == 0.0, (name, stride, out_w, out_h)


@pytest.mark.parametrize("stride", [1, 2])
@pytest.mark.parametrize("name,out_w,out_h", [("room", 1280, 720), ("facade", 1280, 720),
                                              ("room", 720, 1280)])
def test_identity_pose_planar_reproduces_cover_fit(name, out_w, out_h, stride):
    img = load_rgb(name)
    H, W = img.shape[:2]
    K_src = intrinsics(50.0, W, H)
    mesh = build_mesh(np.ones((H, W), np.float32), K_src, stride=stride)
    fit = cover_fit(W, H, out_w, out_h)
    K_out = output_intrinsics(K_src, fit)
    fr = rasterize(mesh, None, K_out, np.eye(4), out_w, out_h, texture=img)
    assert isinstance(fr, Frame)
    assert fr.rgb.shape == (out_h, out_w, 3) and fr.rgb.dtype == np.uint8
    assert fr.z.shape == (out_h, out_w) and fr.z.dtype == np.float32
    assert fr.valid.shape == (out_h, out_w) and fr.valid.dtype == bool
    ref = exact_cover_fit_image(img, fit)
    # Full frame, nothing excluded.
    assert hole_ratio(fr) == 0.0
    err = np.abs(fr.rgb.astype(np.int16) - ref.astype(np.int16)).mean()
    print(f"\n{name} {out_w}x{out_h} stride {stride}: identity mean abs error (full frame)"
          f" = {err:.4f} / 255")
    assert err <= 2.0
    assert np.allclose(fr.z, 1.0, atol=1e-5)


def test_identity_vertex_colors_path_close_to_cover_fit():
    img = load_rgb("room")
    H, W = img.shape[:2]
    K_src = intrinsics(50.0, W, H)
    mesh = build_mesh(np.ones((H, W), np.float32), K_src, stride=1)
    fit = cover_fit(W, H, 1280, 720)
    fr = rasterize(mesh, vertex_colors(mesh, img), output_intrinsics(K_src, fit), np.eye(4),
                   1280, 720)
    err = np.abs(fr.rgb.astype(np.int16) - exact_cover_fit_image(img, fit).astype(np.int16))
    assert hole_ratio(fr) == 0.0 and err.mean() <= 3.0  # vertex colors add a [1,2,1]/4 blur


def test_vertex_colors_bilinear_at_edge_aligned_uv():
    img = np.zeros((4, 6, 3), np.uint8)
    img[:, 3:] = 200
    K = intrinsics(50.0, 6, 4)
    m = build_mesh(np.ones((4, 6), np.float32), K, stride=1)
    col = vertex_colors(m, img)
    gw = m.grid_shape[1]
    # Row 1 vertices at x = 0..6: clamp at the border, average across the pixel edge x=3.
    assert col[gw : 2 * gw, 0].tolist() == [0, 0, 0, 100, 200, 200, 200]


# ---- static path --------------------------------------------------------------------------


def test_static_path_frames_byte_identical_and_streamed():
    zn, K, scene = load_scene("room")
    out_w, out_h = 320, 180
    cp = compile_path({"preset": "push_in", "push_ratio": 0.0, "sketch_points": []},
                      scene, out_w, out_h)
    assert cp.status == "ok" and len(cp.samples) == 120
    img = load_rgb("room")
    mesh = build_mesh(zn, K, stride=4)
    gen = render_sequence(mesh, vertex_colors(mesh, img), cp, out_w, out_h)
    assert inspect.isgenerator(gen)
    first = next(gen)
    n = 1
    for fr in gen:
        n += 1
        assert fr.rgb.tobytes() == first.rgb.tobytes()
        assert fr.z.tobytes() == first.z.tobytes()
        assert fr.valid.tobytes() == first.valid.tobytes()
    assert n == 120


def test_render_sequence_rejects_invalid_path():
    zn, K, scene = load_scene("room")
    cp = compile_path({"preset": "nope"}, scene, 320, 180)
    assert cp.status == "invalid"
    mesh = build_mesh(zn, K, stride=8)
    with pytest.raises(ValueError):
        next(render_sequence(mesh, vertex_colors(mesh, load_rgb("room")), cp, 320, 180))


def test_render_sequence_rejects_size_mismatch():
    zn, K, scene = load_scene("room")
    cp = compile_path({"preset": "push_in", "push_ratio": 0.0}, scene, 320, 180)
    mesh = build_mesh(zn, K, stride=8)
    with pytest.raises(ValueError):
        next(render_sequence(mesh, vertex_colors(mesh, load_rgb("room")), cp, 640, 360))


# ---- occlusion / disocclusion -------------------------------------------------------------

BW, BH = 320, 180
BOX_ROWS, BOX_COLS = slice(60, 120), slice(120, 200)
RED, BLUE = (255, 0, 0), (0, 0, 255)


def box_scene():
    K = intrinsics(50.0, BW, BH)
    z = np.full((BH, BW), 2.0, np.float32)
    z[BOX_ROWS, BOX_COLS] = 1.0
    img = np.zeros((BH, BW, 3), np.uint8)
    img[:] = BLUE
    img[BOX_ROWS, BOX_COLS] = RED
    mesh = build_mesh(z, K, stride=1)
    return K, z, img, mesh


def test_box_in_front_of_plane_from_plus_x_camera():
    K, _z, img, mesh = box_scene()
    tx = 0.1
    T = pose(np.eye(3), [tx, 0.0, 0.0])
    fr = rasterize(mesh, vertex_colors(mesh, img), K, T, BW, BH)
    f = K[0, 0]
    # Box vertices span source x 120..199 (vertex x samples pixel floor(x)) -> shifted by -f*tx.
    bx0, bx1 = 120.0 - f * tx, 199.0 - f * tx
    # Plane points in the overlap (hidden by the box in the new view) project here too.
    plane_left = np.arange(98, 120) + 0.5 - f * tx / 2.0
    overlap = plane_left[(plane_left > bx0 + 1) & (plane_left < bx1 - 1)]
    assert overlap.size > 5
    rows = np.arange(62, 118)
    for x in overlap:
        col = int(np.floor(x))
        assert (fr.rgb[rows, col] == RED).all(), "plane popped over box"
        assert np.allclose(fr.z[rows, col], 1.0, atol=1e-5)
    # Whole box interior (1 px margin) is box.
    cols = np.arange(int(np.ceil(bx0)) + 1, int(np.floor(bx1)) - 1)
    blk = fr.rgb[np.ix_(rows, cols)]
    assert (blk == RED).all()
    # Newly exposed strip right of the box: from box edge to where the plane resumes.
    px0 = 200.0 - f * tx / 2.0
    strip = np.arange(int(np.ceil(bx1)) + 1, int(np.floor(px0)) - 1)
    assert strip.size >= 15
    assert not fr.valid[np.ix_(rows, strip)].any()
    assert np.isinf(fr.z[np.ix_(rows, strip)]).all()
    # Holes = this strip + the right border strip uncovered by the slide (~f*tx/2 px).
    assert hole_ratio(fr) >= strip.size * rows.size / (BW * BH)


def test_triangles_crossing_near_are_dropped_and_far_backdrop_renders():
    K = intrinsics(50.0, 64, 36)
    z = np.full((36, 64), Z_FAR_CAP, np.float32)
    mesh = build_mesh(z, K, stride=2)
    col = np.full((len(mesh.vertices), 3), 200, np.uint8)
    fr = rasterize(mesh, col, K, np.eye(4), 64, 36)
    assert fr.valid[1:-1, 1:-1].all() and FAR > Z_FAR_CAP  # sky at the cap is not clipped
    # Camera pushed through the plane: everything is behind/at near -> nothing renders.
    T = pose(np.eye(3), [0.0, 0.0, Z_FAR_CAP - 0.01])
    assert not rasterize(mesh, col, K, T, 64, 36).valid.any()


# ---- small holes ---------------------------------------------------------------------------


def synthetic_frame(h=160, w=200):
    yy, xx = np.mgrid[0:h, 0:w]
    rgb = np.stack([xx % 256, yy % 256, np.full_like(xx, 128)], -1).astype(np.uint8)
    z = (1.0 + 0.001 * xx).astype(np.float32)
    return Frame(rgb, z, np.ones((h, w), bool))


def test_fill_small_holes_3px_filled_10px_stays_invalid():
    fr = synthetic_frame()
    small = (slice(40, 43), slice(50, 53))
    big = (slice(100, 110), slice(120, 130))
    for s in (small, big):
        fr.valid[s] = False
        fr.z[s] = np.inf
        fr.rgb[s] = 0
    before = (fr.rgb.copy(), fr.z.copy(), fr.valid.copy())
    out, filled = fill_small_holes(fr, max_px=4)
    assert filled.dtype == bool and filled.sum() == 9 and filled[small].all()
    assert out.valid[small].all() and np.isfinite(out.z[small]).all()
    assert not out.valid[big].any() and not filled[big].any() and np.isinf(out.z[big]).all()
    # z = nearest valid neighbor value; rgb inpainted close to its surroundings.
    assert np.all(out.z[small] >= 1.0 + 0.001 * 49) and np.all(out.z[small] <= 1.0 + 0.001 * 53)
    assert np.abs(out.rgb[small].astype(int) - before[0][39, 51].astype(int)).max() <= 8
    # Input untouched; deterministic.
    assert (fr.rgb == before[0]).all() and (fr.valid == before[2]).all()
    out2, _ = fill_small_holes(fr, max_px=4)
    assert out2.rgb.tobytes() == out.rgb.tobytes() and out2.z.tobytes() == out.z.tobytes()


@pytest.mark.parametrize("hole,fill", [((4, 4), True), ((5, 5), False), ((1, 9), False),
                                       ((9, 1), False), ((4, 1), True), ((1, 5), False)])
def test_fill_small_holes_bbox_at_most_4px_both_sides(hole, fill):
    fr = synthetic_frame(60, 60)
    s = (slice(20, 20 + hole[0]), slice(20, 20 + hole[1]))
    fr.valid[s] = False
    fr.z[s] = np.inf
    out, filled = fill_small_holes(fr, max_px=4)
    assert filled[s].all() == fill and filled.any() == fill
    assert out.valid.all() == fill


def test_fill_small_holes_diagonal_component_uses_bbox():
    fr = synthetic_frame(60, 60)
    for i in range(5):  # 8-connected diagonal: bbox 5x5 -> not small
        fr.valid[20 + i, 20 + i] = False
    _, filled = fill_small_holes(fr)
    assert not filled.any()


def test_fill_small_holes_noop_without_holes():
    fr = synthetic_frame(20, 20)
    out, filled = fill_small_holes(fr)
    assert not filled.any() and out.rgb.tobytes() == fr.rgb.tobytes()


# ---- metrics -------------------------------------------------------------------------------


def test_hole_ratio():
    fr = synthetic_frame(100, 100)
    fr.valid[:10, :10] = False
    assert hole_ratio(fr) == pytest.approx(0.01)


def test_target_visibility_occlusion_and_frame():
    K, _z, img, mesh = box_scene()
    T = np.eye(4)
    fr = rasterize(mesh, vertex_colors(mesh, img), K, T, BW, BH)

    def pts(cols, rows, depth):
        u, v = np.meshgrid(np.asarray(cols) + 0.5, np.asarray(rows) + 0.5)
        rays = np.stack([u, v, np.ones_like(u)], -1).reshape(-1, 3) @ np.linalg.inv(K).T
        return rays * depth

    on_box = pts(range(150, 160), range(80, 90), 1.0)
    behind_box = pts(range(150, 160), range(80, 90), 2.0)  # occluded plane points
    on_plane = pts(range(20, 30), range(20, 30), 2.0)
    assert target_visibility(fr, on_box.mean(0), K, T, on_box) == 1.0
    assert target_visibility(fr, on_plane.mean(0), K, T, on_plane) == 1.0
    assert target_visibility(fr, behind_box.mean(0), K, T, behind_box) == 0.0
    out_of_frame = on_plane + np.array([100.0, 0.0, 0.0])
    mixed = np.concatenate([on_box, out_of_frame])
    assert target_visibility(fr, on_box.mean(0), K, T, mixed) == pytest.approx(0.5)


# ---- timing --------------------------------------------------------------------------------


@pytest.mark.slow
def test_timing_120_frames_1280x720_stride2_under_90s():
    zn, K, scene = load_scene("room")
    out_w, out_h = 1280, 720
    cp = compile_path({"preset": "slide", "lateral_ratio": 0.04, "easing": "ease_in_out",
                       "sketch_points": [{"t": 0, "u": 0.5, "v": 0.5},
                                         {"t": 1, "u": 0.45, "v": 0.5}]},
                      scene, out_w, out_h)
    assert cp.status in ("ok", "warning")
    img = load_rgb("room")
    t0 = time.perf_counter()
    mesh = build_mesh(zn, K, stride=2)
    n, worst = 0, 0.0
    for fr in render_sequence(mesh, None, cp, out_w, out_h, texture=img):
        n += 1
        worst = max(worst, hole_ratio(fr))
    dt = time.perf_counter() - t0
    print(f"\nrender 120 frames 1280x720 stride 2 ({len(mesh.faces)} faces): {dt:.2f} s "
          f"({dt / max(n, 1) * 1000:.0f} ms/frame), max hole ratio {worst:.4f}")
    assert n == 120
    assert dt < 90.0
