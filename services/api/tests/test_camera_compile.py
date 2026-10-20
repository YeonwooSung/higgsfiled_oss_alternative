import uuid

import pytest

from helpers import configure
from oc_api.camera_sketch import routes as camera_routes
from oc_api.models import Asset
from test_camera_config import cproject, make_scene_and_depth  # noqa: F401
from conftest import new_client


@pytest.fixture
def ready(app, session_client, cproject):  # noqa: F811
    pid = cproject["id"]
    scene, depth = make_scene_and_depth(app, pid)
    proj = configure(session_client, cproject, scene_asset_id=scene, depth_asset_id=depth)
    return pid, proj["current_revision"]


def compile_(client, pid, rev, sketch, preset="slide", parameters=None):
    return client.post(f"/api/v1/projects/{pid}/camera-path/compile",
                       json={"revision": rev, "sketch": sketch, "preset": preset,
                             "parameters": parameters or {}})


def test_single_point_is_static_ok(session_client, ready):
    pid, rev = ready
    r = compile_(session_client, pid, rev, [{"t": 0, "u": 0.5, "v": 0.5}],
                 parameters={"lateral_ratio": 0, "push_ratio": 0, "arc_deg": 0})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == "ok" and b["warnings"] == []
    assert len(b["path"]["samples"]) == 120 and b["path"]["solver_version"]
    assert b["deferred"] is False and 0.0 <= b["hole_ratio"] <= 1.0
    assert len(b["control_points"]) == 2


def test_motion_params_apply(session_client, ready):
    pid, rev = ready
    r = compile_(session_client, pid, rev, [], parameters={"lateral_ratio": 0.03})
    b = r.json()
    assert r.status_code == 200 and b["path"]["params"]["lateral_ratio"] == 0.03


def test_closed_loop_warns(session_client, ready):
    pid, rev = ready
    import math
    pts = [{"t": i / 40, "u": 0.5 + 0.1 * math.cos(2 * math.pi * i / 40),
            "v": 0.5 + 0.1 * math.sin(2 * math.pi * i / 40)} for i in range(41)]
    pts[-1] = dict(pts[0], t=1.0)
    r = compile_(session_client, pid, rev, pts)
    assert r.status_code == 200 and "unsupported_sketch" in r.json()["warnings"]


def test_too_many_points_422(session_client, ready):
    pid, rev = ready
    pts = [{"t": i / 200, "u": 0.5, "v": 0.5} for i in range(129)]
    assert compile_(session_client, pid, rev, pts).status_code == 422


def test_out_of_bounds_invalid_no_estimate(session_client, ready):
    pid, rev = ready
    r = compile_(session_client, pid, rev, [], parameters={"lateral_ratio": 0.2})
    b = r.json()
    assert r.status_code == 200 and b["status"] == "invalid" and "param_out_of_bounds" in b["warnings"]
    assert b["hole_ratio"] is None and b["deferred"] is False


def test_budget_zero_defers_hole_estimate(monkeypatch, session_client, ready):
    monkeypatch.setattr(camera_routes, "COMPILE_BUDGET_S", 0.0)
    pid, rev = ready
    b = compile_(session_client, pid, rev, [{"t": 0, "u": 0.5, "v": 0.5}]).json()
    assert b["hole_ratio"] is None and b["deferred"] is True and b["status"] in ("ok", "warning")


def test_other_session_is_404(app, session_client, ready):
    pid, rev = ready
    other = new_client(app)
    assert other.post("/api/v1/sessions").status_code == 201
    r = compile_(other, pid, rev, [])
    assert r.status_code == 404


def test_missing_depth_422(session_client, cproject):
    r = compile_(session_client, cproject["id"], 1, [])
    assert r.status_code == 422 and r.json()["error"]["code"] == "depth_missing"


def test_unknown_revision_422(session_client, ready):
    pid, _ = ready
    r = compile_(session_client, pid, 99, [])
    assert r.status_code == 422 and r.json()["error"]["code"] == "revision_not_found"


def test_product_lock_project_rejected(session_client, project):
    r = compile_(session_client, project["id"], 1, [])
    assert r.status_code == 422


def _compile_with_scale(client, app, cproject, depth_scale, preset, params):
    pid = cproject["id"]
    scene, depth = make_scene_and_depth(app, pid)
    proj = configure(client, cproject, scene_asset_id=scene, depth_asset_id=depth,
                     target__depth_scale=depth_scale, target__u=0.7)
    return compile_(client, pid, proj["current_revision"], [{"t": 0, "u": 0.5, "v": 0.5},
                                                           {"t": 1, "u": 0.55, "v": 0.5}],
                    preset=preset, parameters=params).json()


def test_depth_scale_changes_compile_and_is_recorded(app, session_client, cproject):
    a = _compile_with_scale(session_client, app, cproject, 1.0, "slide", {"lateral_ratio": 0.04})
    b = _compile_with_scale(session_client, app, cproject, 1.5, "slide", {"lateral_ratio": 0.04})
    assert a["path"]["keyframes"][-1]["T_cw"] != b["path"]["keyframes"][-1]["T_cw"]
    dm = b["path"]["depth_mapping"]
    assert dm["depth_scale"] == 1.5 and dm["depth_contrast"] == 1.0 and abs(dm["edge_rel_threshold"] - 0.08) < 1e-12


def test_small_arc_keeps_distance_to_scaled_target(app, session_client, cproject):
    import numpy as np

    dists = {}
    for s in (1.0, 1.5):
        b = _compile_with_scale(session_client, app, cproject, s, "small_arc", {"arc_deg": 5.0})
        cs = []
        for smp in b["path"]["samples"]:
            T = np.array(smp["T_cw"])
            cs.append(-T[:3, :3].T @ T[:3, 3])
        cs = np.array(cs)
        K = np.array(b["path"]["K"])
        # target world at scene-source ray; distance from camera to it is constant on the arc
        sw = b["path"]["source_to_output"]
        u, v = 0.7 * sw["src_w"], 0.5 * sw["src_h"]
        f = 50 / 36 * sw["src_w"]
        ray = np.array([(u - sw["src_w"] / 2) / f, (v - sw["src_h"] / 2) / f, 1.0])
        tw = ray * s
        d = np.linalg.norm(cs - tw, axis=1)
        assert d.std() < 1e-6
        dists[s] = d.mean()
    assert abs(dists[1.5] / dists[1.0] - 1.5) < 1e-6


def test_depth_not_decoded_when_deferred(monkeypatch, session_client, ready):
    monkeypatch.setattr(camera_routes, "COMPILE_BUDGET_S", 0.0)

    def boom(*a, **k):
        raise AssertionError("depth decoded")
    monkeypatch.setattr(camera_routes, "load_estimate_extras", boom)
    pid, rev = ready
    assert compile_(session_client, pid, rev, []).json()["deferred"] is True


def test_bad_easing_422(session_client, ready):
    pid, rev = ready
    r = compile_(session_client, pid, rev, [], parameters={"easing": "bounce"})
    assert r.status_code == 422 and "easing" in r.json()["error"]["field"]


def test_control_point_times_are_kept(session_client, ready):
    pid, rev = ready
    pts = [{"t": 0, "u": 0.5, "v": 0.5}, {"t": 0.2, "u": 0.53, "v": 0.49}, {"t": 1, "u": 0.55, "v": 0.5}]
    b = compile_(session_client, pid, rev, pts, parameters={"lateral_ratio": 0.02}).json()
    assert b["status"] in ("ok", "warning")
    assert [k["t"] for k in b["path"]["keyframes"]] == [0.0, 0.2, 1.0]
    assert b["control_points"] == [[0.0, 0.5, 0.5], [0.2, 0.53, 0.49], [1.0, 0.55, 0.5]]


def test_misordered_control_points_are_invalid_input(session_client, ready):
    pid, rev = ready
    pts = [{"t": 0, "u": 0.5, "v": 0.5}, {"t": 0.7, "u": 0.53, "v": 0.49}, {"t": 0.4, "u": 0.55, "v": 0.5},
           {"t": 1, "u": 0.56, "v": 0.5}]
    b = compile_(session_client, pid, rev, pts).json()
    assert b["status"] == "invalid" and b["warnings"] == ["invalid_input"]


# ---- low-res depth for the hole estimate ------------------------------------------------------------------


@pytest.fixture
def ready_lowres(app, session_client, cproject):  # noqa: F811
    pid = cproject["id"]
    scene, depth = make_scene_and_depth(app, pid, w=640, h=360, lowres=True)
    proj = configure(session_client, cproject, scene_asset_id=scene, depth_asset_id=depth)
    return pid, proj["current_revision"], depth


def _keys(app, *ids):
    with app.state.db() as db:
        return [db.get(Asset, uuid.UUID(i)).object_key for i in ids]


def _spy_reads(monkeypatch, app):
    reads: list[str] = []
    storage = app.state.storage
    orig = storage.get_bytes

    def spy(key):
        reads.append(key)
        return orig(key)
    monkeypatch.setattr(storage, "get_bytes", spy)
    return reads


def test_compile_reads_only_lowres_depth(monkeypatch, app, session_client, ready_lowres):
    pid, rev, depth = ready_lowres
    reads = _spy_reads(monkeypatch, app)
    b = compile_(session_client, pid, rev, [{"t": 0, "u": 0.5, "v": 0.5}]).json()
    assert b["deferred"] is False and 0.0 <= b["hole_ratio"] <= 1.0
    with app.state.db() as db:
        lowres_id = db.get(Asset, uuid.UUID(depth)).meta_json["lowres_asset_id"]
    full_key, low_key = _keys(app, depth, lowres_id)
    assert full_key not in reads and reads == [low_key]


def test_compile_lowres_cache_hit_skips_second_load(monkeypatch, app, session_client, ready_lowres):
    pid, rev, _ = ready_lowres
    reads = _spy_reads(monkeypatch, app)
    a = compile_(session_client, pid, rev, [{"t": 0, "u": 0.5, "v": 0.5}]).json()
    b = compile_(session_client, pid, rev, [{"t": 0, "u": 0.5, "v": 0.5}],
                 parameters={"lateral_ratio": 0.02}).json()
    assert len(reads) == 1  # the second compile reuses the prepared low-res z
    assert a["hole_ratio"] is not None and b["hole_ratio"] is not None


def test_compile_lowres_matches_fullres_fallback(monkeypatch, app, session_client, cproject):  # noqa: F811
    """Older depth assets without ``depth_lowres`` decode the full depth; both estimates agree."""
    pid = cproject["id"]
    out = {}
    for lowres in (False, True):
        scene, depth = make_scene_and_depth(app, pid, w=640, h=360, lowres=lowres)
        proj = configure(session_client, cproject, scene_asset_id=scene, depth_asset_id=depth)
        reads = _spy_reads(monkeypatch, app)
        out[lowres] = compile_(session_client, pid, proj["current_revision"], [{"t": 0, "u": 0.5, "v": 0.5}],
                               parameters={"lateral_ratio": 0.03}).json()["hole_ratio"]
        if not lowres:
            assert _keys(app, depth)[0] in reads
        monkeypatch.undo()
    assert out[True] == pytest.approx(out[False], abs=0.02)


def test_slow_depth_load_defers_estimate(monkeypatch, app, session_client, ready_lowres):
    """The budget is re-checked after the load: a slow load skips the estimate and reports deferred."""
    pid, rev, _ = ready_lowres
    now = [0.0]
    monkeypatch.setattr(camera_routes, "_clock", lambda: now[0])
    orig = camera_routes.load_estimate_extras

    def slow(*a, **k):
        res = orig(*a, **k)
        now[0] += camera_routes.COMPILE_BUDGET_S  # the load used up the whole budget
        return res
    monkeypatch.setattr(camera_routes, "load_estimate_extras", slow)

    def no_estimate(*a, **k):
        raise AssertionError("estimate ran after the budget was exceeded")
    monkeypatch.setattr(camera_routes, "_hole_estimate", no_estimate)
    b = compile_(session_client, pid, rev, [{"t": 0, "u": 0.5, "v": 0.5}]).json()
    assert b["deferred"] is True and b["hole_ratio"] is None and b["status"] in ("ok", "warning")
