import uuid

import pytest
from sqlalchemy import select

from helpers import configure
from oc_api.camera_sketch import jobs as camera_jobs
from oc_api.camera_sketch.config import default_config
from oc_api.models import Job, JobCost
from open_creative.camera_sketch.path import CompiledPath
from test_camera_config import cproject, make_scene_and_depth  # noqa: F401


@pytest.fixture
def ready(app, session_client, cproject):  # noqa: F811
    scene, depth = make_scene_and_depth(app, cproject["id"])
    proj = configure(session_client, cproject, scene_asset_id=scene, depth_asset_id=depth)
    return cproject, proj["current_revision"], scene, depth


def post(client, project, kind, options=None, revision=1):
    return client.post(f"/api/v1/projects/{project['id']}/jobs",
                       json={"kind": kind, "revision": revision, "options": options or {}})


def make_guide(app, project, revision, state="succeeded", result=None):
    with app.state.db() as db:
        from oc_api.models import Project
        p = db.get(Project, uuid.UUID(project["id"]))
        j = Job(project_id=p.id, session_id=p.session_id, input_revision=revision, kind="camera.guide",
                options_json={}, model_profile="mock_v1", state=state,
                result_json={"frame_count": 120} if result is None else result)
        db.add(j)
        db.commit()
        return str(j.id)


def err(r):
    return r.json()["error"]


def test_analyze_ok_gpu_reserves_60(app, session_client, ready):
    p, rev, *_ = ready
    r = post(session_client, p, "camera.analyze", revision=rev)
    assert r.status_code == 202, r.text
    with app.state.db() as db:
        c = db.scalar(select(JobCost).where(JobCost.job_id == uuid.UUID(r.json()["id"])))
        j = db.get(Job, uuid.UUID(r.json()["id"]))
        assert c.reserved_units == 60 and j.uses_gpu is True


def test_analyze_requires_scene(session_client, cproject):  # noqa: F811
    r = post(session_client, cproject, "camera.analyze")
    assert r.status_code == 422 and err(r)["code"] == "scene_required"


def test_analyze_rejects_options(session_client, ready):
    p, rev, *_ = ready
    assert post(session_client, p, "camera.analyze", {"x": 1}, rev).status_code == 422


def test_guide_ok_cpu(app, session_client, ready):
    p, rev, *_ = ready
    r = post(session_client, p, "camera.guide", revision=rev)
    assert r.status_code == 202, r.text
    with app.state.db() as db:
        j = db.get(Job, uuid.UUID(r.json()["id"]))
        assert j.uses_gpu is False and j.input_revision == rev
        assert db.scalar(select(JobCost).where(JobCost.job_id == j.id)).reserved_units == 0


def test_guide_needs_depth(session_client, cproject):  # noqa: F811
    r = post(session_client, cproject, "camera.guide")
    assert r.status_code == 422 and err(r)["code"] in ("scene_required", "depth_missing")


def test_guide_without_depth_asset(app, session_client, cproject):  # noqa: F811
    scene, _ = make_scene_and_depth(app, cproject["id"])
    proj = configure(session_client, cproject, scene_asset_id=scene)
    r = post(session_client, cproject, "camera.guide", revision=proj["current_revision"])
    assert r.status_code == 422 and err(r)["code"] == "depth_missing"


def test_guide_invalid_path_422(monkeypatch, session_client, ready):
    p, rev, *_ = ready

    def fake(cfg, scene, w, h, *a, **k):
        return CompiledPath(K=[], source_to_output={}, depth_mapping=None, preset="slide", params={},
                            easing="linear", control_points=[], status="invalid",
                            reasons=["target_behind_camera", "target_out_of_frame"])
    monkeypatch.setattr(camera_jobs, "compile_path", fake)
    r = post(session_client, p, "camera.guide", revision=rev)
    e = err(r)
    assert r.status_code == 422 and e["code"] == "path_invalid" and e["field"] == "config.path"
    assert "target_behind_camera" in e["message"] and "target_out_of_frame" in e["message"]


def test_guide_warning_does_not_block(monkeypatch, session_client, ready):
    p, rev, *_ = ready
    orig = camera_jobs.compile_path

    def warn(*a, **k):
        c = orig(*a, **k)
        c.status, c.reasons = "warning", ["hole_ratio_high"]
        return c
    monkeypatch.setattr(camera_jobs, "compile_path", warn)
    assert post(session_client, p, "camera.guide", revision=rev).status_code == 202


def test_second_guide_is_render_busy(session_client, ready):
    p, rev, *_ = ready
    assert post(session_client, p, "camera.guide", revision=rev).status_code == 202
    r = post(session_client, p, "camera.guide", revision=rev)
    assert r.status_code == 429 and err(r)["code"] == "render_busy"


def test_guide_blocks_cinematic(app, session_client, ready):
    p, rev, *_ = ready
    gid = make_guide(app, p, rev)
    assert post(session_client, p, "camera.guide", revision=rev).status_code == 202
    r = post(session_client, p, "camera.cinematic", {"guide_job_id": gid}, rev)
    assert r.status_code == 429 and err(r)["code"] == "render_busy"


def test_cinematic_ok(app, session_client, ready):
    p, rev, *_ = ready
    gid = make_guide(app, p, rev)
    r = post(session_client, p, "camera.cinematic", {"guide_job_id": gid}, rev)
    assert r.status_code == 202, r.text
    with app.state.db() as db:
        j = db.get(Job, uuid.UUID(r.json()["id"]))
        assert j.input_revision == rev and j.options_json == {"guide_job_id": gid} and j.uses_gpu
        assert db.scalar(select(JobCost).where(JobCost.job_id == j.id)).reserved_units == 600


def test_cinematic_seed_change_is_not_stale(app, session_client, ready):
    p, rev, *_ = ready
    gid = make_guide(app, p, rev)
    new = configure(session_client, p, generation__seed=777, generation__extra_prompt="warm")["current_revision"]
    assert post(session_client, p, "camera.cinematic", {"guide_job_id": gid}, new).status_code == 202


def test_cinematic_stale_when_geometry_changes(app, session_client, ready):
    p, rev, *_ = ready
    gid = make_guide(app, p, rev)
    for change in ({"path__lateral_ratio": 0.03}, {"scene__depth_contrast": 1.2}, {"target__u": 0.3}):
        new = configure(session_client, p, **change)["current_revision"]
        r = post(session_client, p, "camera.cinematic", {"guide_job_id": gid}, new)
        assert r.status_code == 422 and err(r)["code"] == "guide_stale", change


def test_cinematic_frame_count_mismatch(app, session_client, ready):
    p, rev, *_ = ready
    for result in ({"frame_count": 96}, {}):
        gid = make_guide(app, p, rev, result=result)
        r = post(session_client, p, "camera.cinematic", {"guide_job_id": gid}, rev)
        assert r.status_code == 422 and err(r)["code"] == "frame_count_mismatch"


def test_cinematic_guide_validation(app, session_client, ready, cproject):  # noqa: F811
    p, rev, *_ = ready
    assert post(session_client, p, "camera.cinematic", {}, rev).status_code == 422
    r = post(session_client, p, "camera.cinematic", {"guide_job_id": str(uuid.uuid4())}, rev)
    assert r.status_code == 422 and err(r)["code"] == "guide_not_found"
    gid = make_guide(app, p, rev, state="running")
    r = post(session_client, p, "camera.cinematic", {"guide_job_id": gid}, rev)
    assert r.status_code == 422 and err(r)["code"] == "guide_not_finished"
    with app.state.db() as db:
        j = db.get(Job, uuid.UUID(gid))
        j.state, j.kind = "succeeded", "camera.analyze"
        db.commit()
    r = post(session_client, p, "camera.cinematic", {"guide_job_id": gid}, rev)
    assert r.status_code == 422 and err(r)["code"] == "guide_not_found"


def test_cinematic_guide_other_project(app, session_client, ready):
    p, rev, *_ = ready
    other = session_client.post("/api/v1/projects", json={"app_slug": "camera-sketch", "title": "B"}).json()
    gid = make_guide(app, other, 1)
    r = post(session_client, p, "camera.cinematic", {"guide_job_id": gid}, rev)
    assert r.status_code == 422 and err(r)["code"] == "guide_not_found"


def test_estimate_cinematic_unknown_price(session_client, ready):
    p, rev, *_ = ready
    r = session_client.post(f"/api/v1/projects/{p['id']}/estimates", json={"kind": "camera.cinematic",
                                                                         "revision": rev})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["price_status"] == "unknown" and b["uses_gpu"] is True and b["reserved_units"] == 600
    assert b["stages"] == ["prepare", "generate", "postprocess", "qa"]


def test_camera_export_job_kind(app, session_client, ready):
    p, rev, *_ = ready
    gid = make_guide(app, p, rev)
    r = session_client.post(f"/api/v1/projects/{p['id']}/exports", json={"job_id": gid, "format": "guides_zip"})
    assert r.status_code == 202 and r.json()["kind"] == "camera.export"
    with app.state.db() as db:
        j = db.get(Job, uuid.UUID(r.json()["id"]))
        assert j.input_revision == rev and j.options_json == {"source_job_id": gid, "format": "guides_zip"}
    bad = session_client.post(f"/api/v1/projects/{p['id']}/exports", json={"job_id": gid, "format": "cover_png"})
    assert bad.status_code == 422


def test_cinematic_export_formats(app, session_client, ready):
    p, rev, *_ = ready
    gid = make_guide(app, p, rev)
    with app.state.db() as db:
        db.get(Job, uuid.UUID(gid)).kind = "camera.cinematic"
        db.commit()
    assert session_client.post(f"/api/v1/projects/{p['id']}/exports",
                               json={"job_id": gid, "format": "guides_zip"}).status_code == 422
    assert session_client.post(f"/api/v1/projects/{p['id']}/exports",
                               json={"job_id": gid, "format": "recipe_zip"}).status_code == 202


def test_geometry_hash_properties():
    a = default_config("mock_v1")
    h = camera_jobs.geometry_hash(a)
    assert len(h) == 64 and h == camera_jobs.geometry_hash(a.model_copy(deep=True))
    b = a.model_copy(deep=True)
    b.generation.seed = 5
    b.model_profile = "other"
    assert camera_jobs.geometry_hash(b) == h
    c = a.model_copy(deep=True)
    c.scene.edge_protection = 0.9
    assert camera_jobs.geometry_hash(c) != h
