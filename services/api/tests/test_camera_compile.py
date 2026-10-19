import pytest

from helpers import configure
from oc_api.camera_sketch import routes as camera_routes
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
