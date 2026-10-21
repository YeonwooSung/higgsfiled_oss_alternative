import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from helpers import put_asset
from oc_api.cleanup import run_cleanup
from oc_api.models import Asset, CleanupTask, Job, Project


def finished_render(app, project, revision=1):
    video = put_asset(app, project["id"], "render", b"mp4-bytes", "video/mp4", 720, 1280)
    cover = put_asset(app, project["id"], "cover", b"png-bytes", "image/png", 720, 1280)
    with app.state.db() as db:
        project_row = db.get(Project, uuid.UUID(project["id"]))
        job = Job(project_id=project_row.id, session_id=project_row.session_id, input_revision=revision,
                  kind="product.render", model_profile="mock_v1", state="succeeded",
                  output_asset_ids=[video, cover], result_json={"integrity": {"passed": True, "revision": revision}})
        db.add(job)
        db.commit()
        return str(job.id), video, cover


def test_share_link_serves_output_until_revoked(app, session_client, project):
    _, video, _ = finished_render(app, project)
    r = session_client.post(f"/api/v1/projects/{project['id']}/shares", json={"output_asset_id": video, "ttl_days": 7})
    assert r.status_code == 201, r.text
    share = r.json()
    token = share["url"].rsplit("/", 1)[1]
    anon = TestClient(app)  # no session
    got = anon.get(f"/api/v1/public/shares/{token}", follow_redirects=True)
    assert got.status_code == 200 and got.content == b"mp4-bytes"
    assert session_client.delete(f"/api/v1/shares/{share['id']}").status_code == 204
    assert anon.get(f"/api/v1/public/shares/{token}", follow_redirects=False).status_code == 404


def test_only_outputs_can_be_shared(app, session_client, project):
    original = put_asset(app, project["id"], "product", b"x", "image/png", 10, 10)
    r = session_client.post(f"/api/v1/projects/{project['id']}/shares", json={"output_asset_id": original})
    assert r.status_code == 422


def test_share_ttl_bounds(app, session_client, project):
    _, video, _ = finished_render(app, project)
    r = session_client.post(f"/api/v1/projects/{project['id']}/shares", json={"output_asset_id": video, "ttl_days": 90})
    assert r.status_code == 422


def test_export_requires_finished_render_and_known_format(app, session_client, project):
    job_id, _, _ = finished_render(app, project)
    assert session_client.post(f"/api/v1/projects/{project['id']}/exports",
                               json={"job_id": job_id, "format": "gif"}).status_code == 422
    r = session_client.post(f"/api/v1/projects/{project['id']}/exports", json={"job_id": job_id, "format": "mp4"})
    assert r.status_code == 202 and r.json()["kind"] == "export"
    assert r.json()["options"] == {"render_job_id": job_id, "format": "mp4"}


def test_delete_project_blocks_access_and_schedules_cleanup(app, session_client, project):
    _, video, _ = finished_render(app, project)
    share = session_client.post(f"/api/v1/projects/{project['id']}/shares", json={"output_asset_id": video}).json()
    token = share["url"].rsplit("/", 1)[1]
    assert session_client.delete(f"/api/v1/projects/{project['id']}").status_code == 202
    assert session_client.get(f"/api/v1/projects/{project['id']}").status_code == 404
    assert session_client.get(f"/api/v1/assets/{video}").status_code == 404
    assert TestClient(app).get(f"/api/v1/public/shares/{token}", follow_redirects=False).status_code == 404
    with app.state.db() as db:
        tasks = db.scalars(select(CleanupTask).where(CleanupTask.project_id == uuid.UUID(project["id"]))).all()
        assert {t.target for t in tasks} == {"storage"}
        key = db.get(Asset, uuid.UUID(video)).object_key
    assert app.state.storage.exists(key)
    assert run_cleanup(app.state.db, app.state.storage) == 1
    assert not app.state.storage.exists(key)
    with app.state.db() as db:
        assert db.get(Asset, uuid.UUID(video)) is None


def _vfx_render_video(app, project_id, *, profile, meta):
    """A pocket-vfx ``render_video`` asset produced by a succeeded vfx.render job of ``profile``."""
    aid = put_asset(app, project_id, "render_video", b"mp4", "video/mp4", 1280, 720, meta=meta)
    with app.state.db() as db:
        row = db.get(Project, uuid.UUID(project_id))
        job = Job(project_id=row.id, session_id=row.session_id, input_revision=1, kind="vfx.render",
                  model_profile=profile, state="succeeded", output_asset_ids=[aid], result_json={})
        db.add(job)
        db.flush()
        db.get(Asset, uuid.UUID(aid)).job_id = job.id
        db.commit()
    return aid


def test_vfx_render_video_is_shareable_with_profile_and_mock_meta(app, session_client):
    pid = session_client.post("/api/v1/projects", json={"app_slug": "pocket-vfx", "title": "Stomp"}).json()["id"]
    quick = _vfx_render_video(app, pid, profile="mock_v1", meta={"render_profile": "quick", "plate_source": "bundled",
                                                                       "mock": False})
    generated = _vfx_render_video(app, pid, profile="open_weights_v1", meta={"render_profile": "generated",
                                                                                "plate_source": "generated", "mock": True})
    plain = _vfx_render_video(app, pid, profile="open_weights_v1", meta={"render_profile": "quick", "mock": False})
    clip = put_asset(app, pid, "clip", b"mp4", "video/mp4", 1280, 720)
    assert session_client.post(f"/api/v1/projects/{pid}/shares", json={"output_asset_id": clip}).status_code == 422
    for aid, profile, mock in ((quick, "quick", True), (generated, "generated", True), (plain, "quick", False)):
        r = session_client.post(f"/api/v1/projects/{pid}/shares", json={"output_asset_id": aid})
        assert r.status_code == 201, r.text
        token = r.json()["url"].rsplit("/", 1)[1]
        meta = TestClient(app).get(f"/api/v1/public/shares/{token}/meta").json()
        # a Quick render of the mock profile is Mock (its masks are stand-ins) though the asset says mock=False
        assert (meta["mime"], meta["role"], meta["render_profile"], meta["mock"]) == ("video/mp4", "render_video",
                                                                                      profile, mock)
        assert meta["plate_source"] == {quick: "bundled", generated: "generated", plain: None}[aid]


def _job_asset(app, project_id, role, kind, profile, meta, mime="video/mp4"):
    """An output ``role`` asset produced by a succeeded ``kind`` job of ``profile`` (asset.job_id set)."""
    aid = put_asset(app, project_id, role, b"bytes", mime, 1280, 720, meta=meta)
    with app.state.db() as db:
        row = db.get(Project, uuid.UUID(project_id))
        job = Job(project_id=row.id, session_id=row.session_id, input_revision=1, kind=kind, model_profile=profile,
                  state="succeeded", output_asset_ids=[aid], result_json={})
        db.add(job)
        db.flush()
        db.get(Asset, uuid.UUID(aid)).job_id = job.id
        db.commit()
    return aid


def _share_meta(app, client, pid, aid):
    r = client.post(f"/api/v1/projects/{pid}/shares", json={"output_asset_id": aid})
    assert r.status_code == 201, r.text
    return TestClient(app).get(f"/api/v1/public/shares/{r.json()['url'].rsplit('/', 1)[1]}/meta").json()


def test_share_meta_mock_for_product_lock_and_camera_sketch_outputs(app, session_client):
    """``mock`` is true for an output of a mock_v1 job and false for a non-mock profile job whose asset says mock false."""
    pl = session_client.post("/api/v1/projects", json={"app_slug": "product-lock", "title": "Can"}).json()["id"]
    cs = session_client.post("/api/v1/projects", json={"app_slug": "camera-sketch", "title": "Room"}).json()["id"]
    cases = [
        (pl, "render", "product.render", "mock_v1", {"mock": True}, True),
        (pl, "render", "product.render", "open_weights_v1", {"mock": False}, False),
        (pl, "cover", "product.render", "mock_v1", {}, True),
        (cs, "geometry_video", "camera.guide", "mock_v1", {"mode": "geometry", "mock": True}, True),
        (cs, "geometry_video", "camera.guide", "open_weights_v1", {"mode": "geometry", "mock": False}, False),
        (cs, "cinematic_video", "camera.cinematic", "mock_v1", {"mode": "cinematic"}, True),
        (cs, "cinematic_video", "camera.cinematic", "open_weights_v1", {"mode": "cinematic", "mock": False}, False),
    ]
    for pid, role, kind, profile, meta, mock in cases:
        got = _share_meta(app, session_client, pid, _job_asset(app, pid, role, kind, profile, meta))
        assert got["role"] == role and got["mock"] is mock, (role, profile, got)
        if role == "cinematic_video":
            assert got["exact_camera_control"] is False


def test_list_shares_returns_the_owners_active_links_without_tokens(app, session_client, project):
    from datetime import timedelta

    from oc_api.db import utcnow
    from oc_api.models import Share

    _, video, cover = finished_render(app, project)
    url = f"/api/v1/projects/{project['id']}/shares"
    assert session_client.get(url).json() == {"shares": []}
    active = session_client.post(url, json={"output_asset_id": video, "ttl_days": 7}).json()
    revoked = session_client.post(url, json={"output_asset_id": video}).json()
    expired = session_client.post(url, json={"output_asset_id": cover}).json()
    assert session_client.delete(f"/api/v1/shares/{revoked['id']}").status_code == 204
    with app.state.db() as db:
        db.get(Share, uuid.UUID(expired["id"])).expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    r = session_client.get(url)
    assert r.status_code == 200, r.text
    (item,) = r.json()["shares"]
    assert set(item) == {"id", "output_asset_id", "created_at", "expires_at"}  # never the token or its hash
    assert (item["id"], item["output_asset_id"], item["expires_at"]) == (active["id"], video, active["expires_at"])
    # another session sees nothing of this project
    other = TestClient(app)
    assert other.post("/api/v1/sessions").status_code == 201
    assert other.get(url).status_code == 404
    # the listed id is enough to revoke
    assert session_client.delete(f"/api/v1/shares/{item['id']}").status_code == 204
    assert session_client.get(url).json() == {"shares": []}
