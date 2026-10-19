import numpy as np
import pytest
from open_creative.camera_sketch.camera import (
    CoverFit,
    camera_center,
    cover_fit,
    ease,
    framing_rotation,
    from_uv01,
    interpolate,
    intrinsics,
    output_intrinsics,
    pose,
    project,
    quat_to_rot,
    rot_to_quat,
    slerp,
    to_uv01,
)


def _rot_y(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rot_x(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def test_intrinsics_values():
    K = intrinsics(50.0, 1280, 720)
    assert K[0, 0] == pytest.approx(50.0 / 36.0 * 1280)
    assert K[1, 1] == K[0, 0]
    assert (K[0, 2], K[1, 2]) == (640.0, 360.0)
    assert K[2].tolist() == [0.0, 0.0, 1.0]


def test_identity_pose_reprojects_backprojected_pixels():
    K = intrinsics(35.0, 1280, 720)
    uv = np.array([[0.5, 0.5], [640.0, 360.0], [1279.5, 719.5], [100.25, 600.75]])
    zs = np.array([1.0, 2.5, 0.3, 7.0])
    rays = (np.linalg.inv(K) @ np.c_[uv, np.ones(len(uv))].T).T
    X = rays * zs[:, None]
    got, z = project(K, np.eye(4), X)
    assert np.abs(got - uv).max() <= 1e-9
    assert np.allclose(z, zs)


def test_project_behind_camera_is_nan():
    K = intrinsics(35.0, 64, 64)
    uv, z = project(K, np.eye(4), np.array([[0.0, 0.0, -1.0], [0.0, 0.0, 0.0]]))
    assert np.isnan(uv).all()
    assert z.tolist() == [-1.0, 0.0]


def test_camera_moved_right_moves_point_left():
    K = intrinsics(50.0, 1280, 720)
    X = np.array([[0.0, 0.0, 1.0]])
    u0 = project(K, np.eye(4), X)[0][0, 0]
    u1 = project(K, pose(np.eye(3), np.array([0.05, 0.0, 0.0])), X)[0][0, 0]
    assert u1 < u0


def test_push_in_enlarges_box_width():
    K = intrinsics(50.0, 1280, 720)
    box = np.array([[-0.1, 0.0, 1.0], [0.1, 0.0, 1.0]])
    w0 = np.ptp(project(K, np.eye(4), box)[0][:, 0])
    w1 = np.ptp(project(K, pose(np.eye(3), np.array([0.0, 0.0, 0.1])), box)[0][:, 0])
    assert w1 > w0


def test_pose_and_camera_center_round_trip():
    R = _rot_x(0.2) @ _rot_y(-0.4)
    c = np.array([0.3, -0.1, 0.05])
    T = pose(R, c)
    assert T.shape == (4, 4)
    assert np.allclose(T[3], [0, 0, 0, 1])
    assert np.allclose(camera_center(T), c)
    # the camera centre maps to the camera origin
    assert np.allclose(T[:3, :3] @ c + T[:3, 3], 0)


@pytest.mark.parametrize(
    "c, target, desired",
    [
        ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (640.0, 360.0)),
        ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (300.0, 500.0)),
        ((0.04, -0.02, 0.08), (0.1, 0.2, 1.0), (900.5, 120.25)),
        ((-0.05, 0.03, 0.0), (-0.3, -0.25, 1.2), (50.0, 700.0)),
        ((0.0, 0.0, 0.0), (0.5, 0.0, 0.8), (1200.0, 30.0)),
    ],
)
def test_framing_rotation_hits_desired_pixel_with_zero_roll(c, target, desired):
    K = intrinsics(35.0, 1280, 720)
    c, target, desired = np.array(c), np.array(target), np.array(desired)
    R = framing_rotation(c, target, desired, K)
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-12)
    assert np.linalg.det(R) == pytest.approx(1.0)
    assert abs(R[0, 1]) <= 1e-12  # camera x-axis has no world-y component
    uv, z = project(K, pose(R, c), target[None])
    assert z[0] > 0
    assert np.abs(uv[0] - desired).max() <= 1e-9


def test_framing_rotation_centered_target_on_axis_is_identity():
    K = intrinsics(50.0, 1280, 720)
    R = framing_rotation(
        np.zeros(3), np.array([0.0, 0.0, 1.0]), np.array([640.0, 360.0]), K
    )
    assert np.allclose(R, np.eye(3), atol=1e-12)


def test_framing_rotation_rejects_target_at_camera():
    K = intrinsics(50.0, 1280, 720)
    with pytest.raises(ValueError):
        framing_rotation(np.zeros(3), np.zeros(3), np.array([640.0, 360.0]), K)


def test_cover_fit_portrait_into_landscape_maps_centers():
    fit = cover_fit(720, 1280, 1280, 720)
    assert isinstance(fit, CoverFit)
    assert fit.scale == pytest.approx(1280 / 720)
    assert (fit.out_w, fit.out_h) == (1280, 720)
    assert fit.offset_x == pytest.approx(0.0)
    assert fit.scale * 360 + fit.offset_x == pytest.approx(640)
    assert fit.scale * 640 + fit.offset_y == pytest.approx(360)


def test_cover_fit_landscape_into_portrait_maps_centers():
    fit = cover_fit(1280, 720, 720, 1280)
    assert fit.scale * 640 + fit.offset_x == pytest.approx(360)
    assert fit.scale * 360 + fit.offset_y == pytest.approx(640)
    assert fit.offset_y == pytest.approx(0.0)


def test_output_intrinsics_matches_projecting_then_fitting():
    K = intrinsics(35.0, 720, 1280)
    fit = cover_fit(720, 1280, 1280, 720)
    Ko = output_intrinsics(K, fit)
    X = np.array([[0.1, -0.2, 1.3], [0.0, 0.0, 2.0]])
    src, _ = project(K, np.eye(4), X)
    out, _ = project(Ko, np.eye(4), X)
    assert np.allclose(out, src * fit.scale + [fit.offset_x, fit.offset_y])
    assert Ko[0, 2] == pytest.approx(640.0) and Ko[1, 2] == pytest.approx(360.0)


def test_uv01_round_trip():
    px = np.array([[0.0, 0.0], [640.0, 360.0], [1280.0, 720.0]])
    uv = to_uv01(px, 1280, 720)
    assert uv.tolist() == [[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]]
    assert np.allclose(from_uv01(uv, 1280, 720), px)


def test_ease():
    assert ease(0.0, "linear") == 0.0
    assert ease(1.0, "ease_in_out") == 1.0
    assert ease(0.5, "ease_in_out") == pytest.approx(0.5)
    assert ease(0.25, "ease_in_out") == pytest.approx(3 * 0.0625 - 2 * 0.015625)
    assert ease(0.3, "linear") == pytest.approx(0.3)
    assert ease(-1.0, "linear") == 0.0 and ease(2.0, "ease_in_out") == 1.0
    with pytest.raises(ValueError):
        ease(0.5, "bounce")


def test_quaternion_round_trip_and_order():
    q = rot_to_quat(np.eye(3))
    assert np.allclose(q, [1, 0, 0, 0])
    for R in (_rot_y(2.9), _rot_x(-1.2) @ _rot_y(0.7), _rot_x(np.pi)):
        assert np.allclose(quat_to_rot(rot_to_quat(R)), R, atol=1e-12)


def test_slerp_midpoint_and_shortest_path():
    qa = rot_to_quat(np.eye(3))
    qb = rot_to_quat(_rot_y(0.8))
    assert np.allclose(quat_to_rot(slerp(qa, qb, 0.5)), _rot_y(0.4), atol=1e-12)
    # -qb is the same rotation; slerp must still take the short way
    assert np.allclose(quat_to_rot(slerp(qa, -qb, 0.5)), _rot_y(0.4), atol=1e-12)
    assert np.allclose(slerp(qa, qb, 0.0), qa)


def test_interpolate_endpoints_exact_and_midpoint():
    T0 = np.eye(4)
    T1 = pose(_rot_y(0.1), np.array([0.05, 0.0, 0.1]))
    out = interpolate([(0.0, T0), (1.0, T1)], np.array([0.0, 0.5, 1.0]), "linear")
    assert len(out) == 3
    assert np.array_equal(out[0], T0)
    assert np.array_equal(out[2], T1)
    assert np.allclose(camera_center(out[1]), [0.025, 0.0, 0.05])
    assert np.allclose(out[1][:3, :3], _rot_y(0.05), atol=1e-12)


def test_interpolate_easing_applies_to_position_and_orientation():
    T0 = np.eye(4)
    T1 = pose(_rot_y(0.2), np.array([0.1, 0.0, 0.0]))
    (T,) = interpolate([(0.0, T0), (1.0, T1)], np.array([0.25]), "ease_in_out")
    s = ease(0.25, "ease_in_out")
    assert np.allclose(camera_center(T), [0.1 * s, 0.0, 0.0])
    assert np.allclose(T[:3, :3], _rot_y(0.2 * s), atol=1e-12)


def test_interpolate_multi_segment_clamps_and_hits_keys():
    Ts = [pose(np.eye(3), np.array([x, 0.0, 0.0])) for x in (0.0, 0.04, -0.02)]
    keys = [(0.0, Ts[0]), (0.4, Ts[1]), (1.0, Ts[2])]
    out = interpolate(keys, np.array([-0.5, 0.0, 0.2, 0.4, 0.7, 1.0, 1.5]), "linear")
    xs = [camera_center(T)[0] for T in out]
    assert xs == pytest.approx([0.0, 0.0, 0.02, 0.04, 0.01, -0.02, -0.02])
    assert np.array_equal(out[3], Ts[1])


def test_interpolate_rejects_unsorted_keys():
    with pytest.raises(ValueError):
        interpolate([(0.5, np.eye(4)), (0.5, np.eye(4))], np.array([0.5]), "linear")
