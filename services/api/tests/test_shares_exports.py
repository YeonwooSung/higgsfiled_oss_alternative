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
