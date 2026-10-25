import uuid

import pytest
from fastapi.testclient import TestClient
from helpers import put_asset
from oc_api.apps.pocket_vfx import CLIP_MAX_BYTES
from oc_api.models import Asset, Job, Project
from sqlalchemy import func, select


def make_project(client, app_slug, title="Source"):
    r = client.post("/api/v1/projects", json={"app_slug": app_slug, "title": title})
    assert r.status_code == 201, r.text
    return r.json()


def finished_output(app, project, role, data=b"output-bytes", mime="video/mp4", profile="open_weights_v1",
                    state="ready", kind="product.render"):
    """A generated output asset of ``role`` made by a succeeded job (``asset.job_id`` points at it)."""
    aid = put_asset(app, project["id"], role, data, mime, 720, 1280, state=state)
    with app.state.db() as db:
        project_row = db.get(Project, uuid.UUID(project["id"]))
        job = Job(project_id=project_row.id, session_id=project_row.session_id, input_revision=1, kind=kind,
                  model_profile=profile, state="succeeded", output_asset_ids=[aid])
        db.add(job)
        db.flush()
        asset = db.get(Asset, uuid.UUID(aid))
        asset.job_id, asset.sha256 = job.id, "b" * 64
        db.commit()
        return aid, str(job.id)


def hand_off(client, asset_id, target_app, key=None):
    return client.post("/api/v1/handoffs", json={"source_asset_id": asset_id, "target_app": target_app},
                       headers={"Idempotency-Key": key} if key else {})


def project_count(app) -> int:
    with app.state.db() as db:
        return db.scalar(select(func.count()).select_from(Project))


def test_product_render_to_vfx_creates_project_and_inspect_job(app, session_client, project):
    src, src_job = finished_output(app, project, "render", b"mp4-render-bytes")
    r = hand_off(session_client, src, "pocket-vfx")
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["app_slug"] == "pocket-vfx"
    target = session_client.get(f"/api/v1/projects/{out['project_id']}").json()
    assert target["app_slug"] == "pocket-vfx" and target["title"] == "Can ad → Pocket VFX"
    assert target["current_revision"] == 1
    with app.state.db() as db:
        new = db.get(Asset, uuid.UUID(out["asset_id"]))
        old = db.get(Asset, uuid.UUID(src))
        assert str(new.project_id) == out["project_id"]
        assert (new.role, new.state, new.mime, new.bytes, new.sha256) == ("clip", "uploaded", "video/mp4",
                                                                           old.bytes, old.sha256)
        assert new.filename == "product-lock-render.mp4" and new.source_asset_id == old.id
        assert new.object_key == f"{target_session(db, out)}/{out['project_id']}/{new.id}/clip-original.mp4"
        assert app.state.storage.get_bytes(new.object_key) == app.state.storage.get_bytes(old.object_key)
        assert new.meta_json["handoff"] == {"source_app": "product-lock", "source_project_id": project["id"],
                                            "source_asset_id": src, "source_job_id": src_job,
                                            "source_sha256": "b" * 64, "source_title": "Can ad", "mock": False}
        job = db.get(Job, uuid.UUID(out["inspect_job_id"]))
        assert (job.kind, job.state, job.options_json) == ("asset.inspect", "queued", {"asset_id": out["asset_id"]})
        assert str(job.project_id) == out["project_id"]


def target_session(db, out) -> str:
    return str(db.get(Project, uuid.UUID(out["project_id"])).session_id)


def test_product_cover_to_camera_scene(app, session_client, project):
    src, _ = finished_output(app, project, "cover", b"png-cover", "image/png")
    r = hand_off(session_client, src, "camera-sketch")
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["app_slug"] == "camera-sketch"
    with app.state.db() as db:
        new = db.get(Asset, uuid.UUID(out["asset_id"]))
        assert (new.role, new.state, new.mime) == ("scene", "uploaded", "image/png")
        assert new.filename == "product-lock-cover.png" and new.object_key.endswith("/scene-original.png")


@pytest.mark.parametrize("role", ["geometry_video", "cinematic_video"])
def test_camera_geometry_and_cinematic_to_vfx(app, session_client, role):
    cam = make_project(session_client, "camera-sketch", "Room slide")
    src, _ = finished_output(app, cam, role, kind="camera.cinematic")
    r = hand_off(session_client, src, "pocket-vfx")
    assert r.status_code == 201, r.text
    with app.state.db() as db:
        new = db.get(Asset, uuid.UUID(r.json()["asset_id"]))
        assert new.role == "clip" and new.filename == f"camera-sketch-{role}.mp4"
        assert new.meta_json["handoff"]["source_app"] == "camera-sketch"


def test_disallowed_pairs_are_422(app, session_client, project):
    vfx = make_project(session_client, "pocket-vfx", "Stomp")
    vfx_cover, _ = finished_output(app, vfx, "cover", b"png", "image/png", kind="vfx.render")
    render, _ = finished_output(app, project, "render")
    original = put_asset(app, project["id"], "product", b"png", "image/png", 10, 10)
    cover, _ = finished_output(app, project, "cover", b"png", "image/png")
    before = project_count(app)
    for asset_id, target in ((vfx_cover, "camera-sketch"), (render, "camera-sketch"), (original, "pocket-vfx"),
                             (cover, "product-lock")):
        r = hand_off(session_client, asset_id, target)
        assert r.status_code == 422, (asset_id, target, r.text)
        assert r.json()["error"]["code"] == "handoff_not_allowed"
    assert project_count(app) == before


def test_foreign_session_asset_is_404(app, session_client, project):
    src, _ = finished_output(app, project, "render")
    other = TestClient(app)
    assert other.post("/api/v1/sessions").status_code == 201
    r = hand_off(other, src, "pocket-vfx")
    assert r.status_code == 404
    assert hand_off(session_client, str(uuid.uuid4()), "pocket-vfx").status_code == 404
    assert hand_off(session_client, "not-a-uuid", "pocket-vfx").status_code == 404


def test_source_not_ready_is_409(app, session_client, project):
    src, _ = finished_output(app, project, "render", state="uploaded")
    r = hand_off(session_client, src, "pocket-vfx")
    assert r.status_code == 409 and r.json()["error"]["code"] == "asset_not_ready"


def test_too_large_for_target_is_413_and_creates_nothing(app, session_client, project):
    src, _ = finished_output(app, project, "render")
    with app.state.db() as db:
        db.get(Asset, uuid.UUID(src)).bytes = CLIP_MAX_BYTES + 1
        db.commit()
    before = project_count(app)
    r = hand_off(session_client, src, "pocket-vfx")
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"
    assert project_count(app) == before


def test_idempotent_replay_returns_same_project_and_conflict_on_other_body(app, session_client, project):
    render, _ = finished_output(app, project, "render")
    cover, _ = finished_output(app, project, "cover", b"png", "image/png")
    first = hand_off(session_client, render, "pocket-vfx", key="k-1")
    assert first.status_code == 201, first.text
    before = project_count(app)
    again = hand_off(session_client, render, "pocket-vfx", key="k-1")
    assert again.status_code == 201 and again.json() == first.json()
    assert project_count(app) == before
    r = hand_off(session_client, cover, "camera-sketch", key="k-1")
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_conflict"
    assert project_count(app) == before


def test_handoff_marks_mock_from_source_profile(app, session_client, project):
    src, _ = finished_output(app, project, "render", profile="mock_v1")
    r = hand_off(session_client, src, "pocket-vfx")
    assert r.status_code == 201, r.text
    with app.state.db() as db:
        assert db.get(Asset, uuid.UUID(r.json()["asset_id"])).meta_json["handoff"]["mock"] is True


def _mock_handoff_target_with_render(app, session_client, project):
    """A pocket-vfx project started from a Mock render, holding its own non-mock render_video result."""
    src, _ = finished_output(app, project, "render", profile="mock_v1")
    out = hand_off(session_client, src, "pocket-vfx").json()
    video, _ = finished_output(app, {"id": out["project_id"]}, "render_video", profile="open_weights_v1",
                               kind="vfx.render")
    return out, video


def test_share_meta_has_app_slug_and_upstream_mock(app, session_client, project):
    out, video = _mock_handoff_target_with_render(app, session_client, project)
    r = session_client.post(f"/api/v1/projects/{out['project_id']}/shares", json={"output_asset_id": video})
    assert r.status_code == 201, r.text
    meta = session_client.get(f"/api/v1/public/shares/{r.json()['url'].rsplit('/', 1)[1]}/meta").json()
    assert meta["app_slug"] == "pocket-vfx" and meta["mock"] is True


def test_project_out_handoff_survives_source_delete(app, session_client, project):
    out, _ = _mock_handoff_target_with_render(app, session_client, project)
    got = session_client.get(f"/api/v1/projects/{out['project_id']}").json()
    assert got["upstream_mock"] is True
    assert got["handoff"] == {"source_app": "product-lock", "source_project_id": project["id"],
                              "source_title": "Can ad", "source_exists": True, "mock": True}
    assert session_client.delete(f"/api/v1/projects/{project['id']}").status_code == 202
    got = session_client.get(f"/api/v1/projects/{out['project_id']}").json()
    assert got["handoff"]["source_exists"] is False and got["upstream_mock"] is True


def test_plain_project_has_no_handoff(session_client, project):
    got = session_client.get(f"/api/v1/projects/{project['id']}").json()
    assert got["upstream_mock"] is False and got["handoff"] is None
