import json
from pathlib import Path

import numpy as np
import pytest

from open_creative.camera_sketch import path as cs_path
from open_creative.camera_sketch.camera import (
    camera_center,
    cover_fit,
    intrinsics,
    project,
)
from open_creative.camera_sketch.path import (
    SOLVER_VERSION,
    SceneInfo,
    compile_path,
    load_presets,
    preset_center,
    simplify_sketch,
    solve_framing,
)
from open_creative.camera_sketch.scene import (
    DAV2_SMALL,
    Z_FAR_CAP,
    decode_depth_png16,
    depth_to_z,
    prepare_z,
)

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "fixtures" / "camera-sketch"
OUT_W, OUT_H = 1280, 720


def fixture_scene(name: str, focal: float = 50.0) -> SceneInfo:
    meta = json.loads((FIX / "scenes.json").read_text())["scenes"][name]
    d = decode_depth_png16((FIX / meta["depth"]).read_bytes(), meta["depth_png16"])
    u, v = meta["target_uv01"]
    zn, _ = prepare_z(depth_to_z(d, DAV2_SMALL), (u, v))
    W, H = meta["width"], meta["height"]
    K = intrinsics(focal, W, H)
    ray = np.linalg.solve(K, [u * W, v * H, 1.0])
    tz = float(zn[int(v * H), int(u * W)])
    return SceneInfo(K, W, H, ray * tz, (u, v), depth_convention=DAV2_SMALL)


def cfg(preset="slide", sketch=(), **params):
    return {"preset": preset, "sketch_points": [{"t": t, "u": u, "v": v} for t, u, v in sketch],
            "easing": "ease_in_out", **params}


def centers(cp):
    return np.array([camera_center(np.array(s["T_cw"])) for s in cp.to_dict()["samples"]])


def assert_json_safe(cp):
    s = json.dumps(cp.to_dict(), allow_nan=False)
    assert json.loads(s)["status"] == cp.status


# ---- presets ---------------------------------------------------------------


def test_presets_repo_and_package_copies_are_identical():
    packaged = Path(cs_path.__file__).with_name("presets.v1.json")
    assert packaged.read_bytes() == (REPO / "presets" / "camera-sketch" / "presets.v1.json").read_bytes()


def test_presets_content():
    data = json.loads((REPO / "presets" / "camera-sketch" / "presets.v1.json").read_text())
    assert data["solver_version"] == SOLVER_VERSION == "cs_framing_ls_v1"
    p = load_presets()
    assert set(p) == {"push_in", "slide", "small_arc"}
    assert p["push_in"]["bounds"]["push_ratio"] == [0, 0.10]
    assert p["push_in"]["bounds"]["lateral_ratio"] == [0, 0]
    assert p["slide"]["bounds"]["lateral_ratio"] == [-0.05, 0.05]
    assert p["small_arc"]["bounds"]["arc_deg"] == [-6, 6]
    assert all(pr["bounds"]["push_ratio"] == [0, 0.10] for pr in p.values())
    for pr in p.values():
        assert pr["label_en"] and pr["label_ko"] and set(pr["default"]) == set(pr["bounds"])


# ---- simplify_sketch -------------------------------------------------------


def test_single_point_is_static_pair():
    ctrl, warn = simplify_sketch([(0.3, 0.4, 0.6)])
    assert ctrl == [(0.0, 0.4, 0.6), (1.0, 0.4, 0.6)] and warn is None


def test_zigzag_200_points_reduces_to_at_most_five():
    pts = [(i / 199, 0.1 + 0.8 * i / 199, 0.5 + (0.04 if i % 2 else -0.04)) for i in range(200)]
    ctrl, _ = simplify_sketch(pts)
    assert 2 <= len(ctrl) <= 5
    assert ctrl[0][1:] == pts[0][1:] and ctrl[-1][1:] == pts[-1][1:]
    ts = [c[0] for c in ctrl]
    assert ts[0] == 0.0 and ts[-1] == 1.0 and all(np.diff(ts) > 0)


def test_arc_length_time_renormalization():
    pts = [(0.0, 0.2, 0.5)] + [(0.1 * k, 0.2 + 0.04 * k, 0.5) for k in range(1, 11)]
    pts += [(1.0 + 0.1 * k, 0.6, 0.5 + 0.02 * k) for k in range(1, 11)]
    ctrl, warn = simplify_sketch(pts)
    assert warn is None
    assert np.allclose([c[1:] for c in ctrl], [(0.2, 0.5), (0.6, 0.5), (0.6, 0.7)])
    assert [c[0] for c in ctrl] == pytest.approx([0.0, 0.4 / 0.6, 1.0])


def test_closed_loop_and_backtrack_are_unsupported():
    a = np.linspace(0, 2 * np.pi, 64)
    loop = [(i / 63, 0.5 + 0.1 * np.cos(x), 0.5 + 0.1 * np.sin(x)) for i, x in enumerate(a)]
    assert simplify_sketch(loop)[1] == "unsupported_sketch"
    back = [(i / 20, 0.2 + 0.03 * i, 0.5) for i in range(11)]
    back += [(0.5 + i / 20, 0.5 - 0.03 * i, 0.56) for i in range(1, 11)]
    assert simplify_sketch(back)[1] == "unsupported_sketch"
    line = [(i / 9, 0.3 + 0.02 * i, 0.5) for i in range(10)]
    assert simplify_sketch(line) == ([(0.0, 0.3, 0.5), (1.0, 0.3 + 0.02 * 9, 0.5)], None)


# ---- solve_framing ---------------------------------------------------------


def test_solve_framing_zero_weights_is_identity_inside_box():
    d = np.array([[0.5, 0.5], [0.55, 0.45], [0.6, 0.5]])
    assert np.allclose(solve_framing(d, lam_vel=0.0, lam_acc=0.0), d)


def test_solve_framing_clamps_and_keeps_first_point():
    d = np.array([[0.9, 0.5], [0.97, 0.5], [1.02, 0.03], [1.05, 0.01]])
    p = solve_framing(d)
    assert np.array_equal(p[0], d[0])
    assert (p[1:] >= 0.05 - 1e-12).all() and (p[1:] <= 0.95 + 1e-12).all()
    assert p[2, 0] == pytest.approx(0.95) and p[3, 0] == pytest.approx(0.95)


def test_solve_framing_smooths_spike():
    d = np.array([[0.5, 0.5], [0.5, 0.5], [0.7, 0.5], [0.5, 0.5], [0.5, 0.5]])
    p = solve_framing(d)
    assert 0.5 < p[2, 0] < 0.7 and np.allclose(p[:, 1], 0.5)


# ---- preset_center ---------------------------------------------------------


def test_preset_center_formulas():
    T = np.array([0.1, 0.05, 1.0])
    assert np.allclose(preset_center("slide", {"lateral_ratio": 0.04}, 0.5, T), [0.02, 0, 0])
    assert np.allclose(preset_center("push_in", {"push_ratio": 0.1}, 1.0, T), [0, 0, 0.1])
    for s in (0.0, 0.3, 1.0):
        c = preset_center("small_arc", {"arc_deg": 6.0}, s, T)
        assert c[1] == 0.0
        assert np.linalg.norm(c - T) == pytest.approx(np.linalg.norm(T), abs=1e-12)
    assert np.allclose(preset_center("small_arc", {"arc_deg": 6.0}, 0.0, T), 0.0)
    # Positive arc and positive lateral both move the camera toward +x.
    assert preset_center("small_arc", {"arc_deg": 6.0}, 1.0, np.array([0.0, 0.0, 1.0]))[0] > 0
    assert preset_center("small_arc", {"arc_deg": -6.0}, 1.0, np.array([0.0, 0.0, 1.0]))[0] < 0
    # Push is additive on every preset.
    assert np.allclose(preset_center("slide", {"lateral_ratio": 0.02, "push_ratio": 0.05}, 1.0, T),
                       [0.02, 0, 0.05])
    c0 = preset_center("small_arc", {"arc_deg": 6.0}, 0.5, T)
    c1 = preset_center("small_arc", {"arc_deg": 6.0, "push_ratio": 0.08}, 0.5, T)
    assert np.allclose(c1 - c0, [0, 0, 0.04])


# ---- compile_path ----------------------------------------------------------


@pytest.fixture(scope="module")
def room():
    return fixture_scene("room")


def test_static_path_is_identity_everywhere(room):
    for c in (cfg("slide", lateral_ratio=0.0), cfg("slide", [(0.0, 0.5, 0.5)], lateral_ratio=0.0)):
        cp = compile_path(c, room, OUT_W, OUT_H)
        d = cp.to_dict()
        assert cp.status == "ok" and d["reasons"] == []
        assert len(d["samples"]) == 120
        assert d["samples"][1]["t_ms"] == pytest.approx(1000 / 24)
        for s in d["samples"]:
            assert np.allclose(s["T_cw"], np.eye(4), atol=1e-9)


def test_slide_moves_plus_x_and_keeps_framing(room):
    cp = compile_path(cfg("slide", lateral_ratio=0.05), room, OUT_W, OUT_H)
    c = centers(cp)
    assert cp.status == "ok"
    assert c[-1] == pytest.approx([0.05, 0, 0], abs=1e-12)
    assert (np.diff(c[:, 0]) >= -1e-15).all()
    dg = cp.diagnostics
    assert dg["framing_rms_rel"] <= 0.01
    assert dg["framing_rms_px"] == pytest.approx(dg["framing_rms_rel"] * OUT_H)
    assert dg["target_in_frame_fraction"] == 1.0 and dg["hole_ratio"] is None


def test_push_ends_at_z_010(room):
    cp = compile_path(cfg("push_in", push_ratio=0.10), room, OUT_W, OUT_H)
    assert cp.status == "ok"
    assert centers(cp)[-1] == pytest.approx([0, 0, 0.10], abs=1e-12)


def test_small_arc_keeps_distance_and_7_deg_is_invalid(room):
    cp = compile_path(cfg("small_arc", arc_deg=6.0), room, OUT_W, OUT_H)
    assert cp.status == "ok"
    dist = np.linalg.norm(centers(cp) - room.target_world, axis=1)
    assert np.abs(dist - np.linalg.norm(room.target_world)).max() <= 1e-6
    bad = compile_path(cfg("small_arc", arc_deg=7.0), room, OUT_W, OUT_H)
    assert bad.status == "invalid" and "param_out_of_bounds" in bad.reasons
    assert bad.suggested_params == {"lateral_ratio": 0.0, "push_ratio": 0.0, "arc_deg": 3.5}
    assert_json_safe(bad)


def test_params_of_other_presets_are_out_of_bounds(room):
    cp = compile_path(cfg("slide", lateral_ratio=0.02, arc_deg=2.0), room, OUT_W, OUT_H)
    assert cp.status == "invalid" and "param_out_of_bounds" in cp.reasons
    cp = compile_path(cfg("small_arc", arc_deg=2.0, lateral_ratio=0.01), room, OUT_W, OUT_H)
    assert cp.status == "invalid" and "param_out_of_bounds" in cp.reasons


def test_sketch_framing_follows_and_clamps_near_edge(room):
    edge = SceneInfo(room.K_src, room.src_w, room.src_h,
                     np.linalg.solve(room.K_src, [0.9 * 960, 0.5 * 540, 1.0]), (0.9, 0.5))
    sk = [(0.0, 0.5, 0.5), (0.5, 0.55, 0.5), (1.0, 0.62, 0.5)]
    cp = compile_path(cfg("slide", sk, lateral_ratio=0.0), edge, OUT_W, OUT_H)
    d = cp.to_dict()
    assert cp.status == "warning" and "framing_clamped" in cp.reasons
    k = d["keyframes"]
    assert [x["t"] for x in k] == [0.0, 1.0]  # collinear sketch -> 2 control points
    assert k[-1]["requested_uv"][0] == pytest.approx(0.9 + 0.12)
    assert all(x["desired_uv"][0] <= 0.95 + 1e-12 for x in k[1:])
    # The last keyframe frames the target exactly at its solved position.
    uv, _ = project(np.array(d["K"]), np.array(k[-1]["T_cw"]), edge.target_world)
    assert uv[0] / [OUT_W, OUT_H] == pytest.approx(k[-1]["desired_uv"], abs=1e-9)


def test_unsupported_sketch_is_warning(room):
    a = np.linspace(0, 2 * np.pi, 40)
    loop = [(i / 39, 0.5 + 0.08 * np.cos(x), 0.5 + 0.08 * np.sin(x)) for i, x in enumerate(a)]
    cp = compile_path(cfg("slide", loop, lateral_ratio=0.0), room, OUT_W, OUT_H)
    assert cp.status == "warning" and "unsupported_sketch" in cp.reasons


def test_target_behind_camera_is_invalid_without_nan(room):
    behind = SceneInfo(room.K_src, 960, 540, np.array([0.0, 0.0, -1.0]), (0.5, 0.5))
    cp = compile_path(cfg("slide", lateral_ratio=0.05), behind, OUT_W, OUT_H)
    assert cp.status == "invalid" and "target_behind_camera" in cp.reasons
    assert_json_safe(cp)
    near = SceneInfo(room.K_src, 960, 540, np.array([0.0, 0.0, 0.08]), (0.5, 0.5))
    cp = compile_path(cfg("push_in", push_ratio=0.10), near, OUT_W, OUT_H)
    assert cp.status == "invalid" and cp.reasons
    assert cp.suggested_params == {"lateral_ratio": 0.0, "push_ratio": 0.025, "arc_deg": 0.0}
    assert_json_safe(cp)


def test_portrait_source_to_landscape_output():
    sc = fixture_scene("facade")
    cp = compile_path(cfg("slide", lateral_ratio=0.03), sc, OUT_W, OUT_H)
    d = cp.to_dict()
    assert cp.status == "ok"
    assert np.allclose(d["samples"][0]["T_cw"], np.eye(4), atol=1e-9)
    fit = cover_fit(576, 1024, OUT_W, OUT_H)
    assert d["source_to_output"]["scale"] == fit.scale
    assert d["source_to_output"]["offset_y"] == fit.offset_y
    uv, _ = project(np.array(d["K"]), np.eye(4), sc.target_world)
    exp = np.array([0.5 * 576, 0.6 * 1024]) * fit.scale + [fit.offset_x, fit.offset_y]
    assert uv[0] == pytest.approx(exp, abs=1e-6)
    assert d["keyframes"][0]["desired_uv"] == pytest.approx((exp / [OUT_W, OUT_H]).tolist())


def test_artifact_fields_are_json_safe(room):
    cp = compile_path(cfg("small_arc", [(0, 0.5, 0.5), (1, 0.45, 0.5)], arc_deg=-4.0), room, OUT_W, OUT_H)
    d = cp.to_dict()
    assert_json_safe(cp)
    for k in ("schema_version", "solver_version", "coordinate_convention", "K", "source_to_output",
              "depth_mapping", "keyframes", "samples", "diagnostics", "status", "reasons",
              "suggested_params", "control_points"):
        assert k in d
    assert d["coordinate_convention"] == "opencv_T_cw" and d["solver_version"] == SOLVER_VERSION
    assert d["depth_mapping"]["kind"] == "inverse_relative"
    assert d["depth_mapping"]["z_far_cap"] == Z_FAR_CAP
    assert d["samples"][-1]["t_ms"] == pytest.approx(119 * 1000 / 24)
    assert type(d["K"][0][0]) is float and type(d["samples"][0]["t_ms"]) is float


def test_unknown_preset_or_easing_is_invalid(room):
    assert compile_path(cfg("orbit"), room, OUT_W, OUT_H).reasons == ["unknown_preset"]
    c = {**cfg("slide", lateral_ratio=0.01), "easing": "bounce"}
    assert compile_path(c, room, OUT_W, OUT_H).reasons == ["unknown_easing"]


def test_push_is_additive_on_slide_and_arc(room):
    cp = compile_path(cfg("slide", lateral_ratio=0.02, push_ratio=0.05), room, OUT_W, OUT_H)
    assert cp.status in ("ok", "warning")
    assert centers(cp)[-1] == pytest.approx([0.02, 0, 0.05], abs=1e-12)
    cp = compile_path(cfg("small_arc", arc_deg=5.0, push_ratio=0.08), room, OUT_W, OUT_H)
    assert cp.status == "ok" and cp.diagnostics["framing_rms_rel"] <= 0.01
    arc_only = preset_center("small_arc", {"arc_deg": 5.0}, 1.0, room.target_world)
    assert arc_only[0] > 0
    assert centers(cp)[-1] == pytest.approx(arc_only + [0, 0, 0.08], abs=1e-12)


def test_suggestion_halves_only_nonzero_params(room):
    cp = compile_path(cfg("slide", lateral_ratio=0.08, push_ratio=0.0), room, OUT_W, OUT_H)
    assert cp.status == "invalid"
    assert cp.suggested_params == {"lateral_ratio": 0.04, "push_ratio": 0.0, "arc_deg": 0.0}


@pytest.mark.parametrize("c", [
    cfg("slide", lateral_ratio=float("nan")),
    cfg("slide", lateral_ratio="abc"),
    cfg("slide", [(0.0, 0.5, 0.5), (0.5, float("nan"), 0.5), (1.0, 0.6, 0.5)], lateral_ratio=0.0),
    cfg("slide", [(0.0, 0.5, float("inf"))], lateral_ratio=0.0),
    {**cfg("slide", lateral_ratio=0.0), "sketch_points": [{"t": 0, "u": 0.5}]},
])
def test_invalid_input_is_json_safe(room, c):
    cp = compile_path(c, room, OUT_W, OUT_H)
    assert cp.status == "invalid" and "invalid_input" in cp.reasons
    d = cp.to_dict()
    assert d["keyframes"] == [] and d["samples"] == [] and d["control_points"] == []
    assert_json_safe(cp)
    assert "nan" not in json.dumps(d).lower() and "abc" not in json.dumps(d)


@pytest.mark.parametrize("n", [0, 1])
def test_too_few_frames_is_invalid_input(room, n):
    cp = compile_path(cfg("slide", lateral_ratio=0.02), room, OUT_W, OUT_H, n_frames=n)
    assert cp.status == "invalid" and cp.reasons == ["invalid_input"]
    assert_json_safe(cp)


def test_non_finite_poses_clear_artifact(room, monkeypatch):
    real = cs_path.preset_center

    def broken(preset, params, s, target):
        c = real(preset, params, s, target)
        return c * np.nan if 0.0 < s < 1.0 else c

    monkeypatch.setattr(cs_path, "preset_center", broken)
    cp = compile_path(cfg("slide", [(0, 0.5, 0.5), (1, 0.55, 0.5)], lateral_ratio=0.02), room, OUT_W, OUT_H)
    assert cp.status == "invalid" and "non_finite" in cp.reasons
    d = cp.to_dict()
    assert d["keyframes"] == d["samples"] == d["control_points"] == []
    assert_json_safe(cp)
