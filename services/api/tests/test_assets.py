import hashlib
import io
import time
import uuid

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from oc_api.models import Asset, Job


def png_bytes(w=8, h=6):
    buf = io.BytesIO()
    Image.fromarray(np.full((h, w, 3), 100, np.uint8)).save(buf, "PNG")
    return buf.getvalue()


def init(client, project, role="product", mime="image/png", size=100, filename="can.png"):
    return client.post(f"/api/v1/projects/{project['id']}/assets/init",
                       json={"role": role, "filename": filename, "mime": mime, "bytes": size})


def upload(client, project, data, role="product", mime="image/png"):
    r = init(client, project, role=role, mime=mime, size=len(data))
    assert r.status_code == 201, r.text
    body = r.json()
    put = client.put(body["upload_url"], content=data, headers=body["headers"])
    assert put.status_code == 200, put.text
    return body["asset_id"]


def test_init_returns_short_lived_put_url(session_client, project):
    r = init(session_client, project)
    assert r.status_code == 201
    b = r.json()
    assert b["method"] == "PUT" and b["headers"]["Content-Type"] == "image/png"
    assert b["upload_url"].startswith("/api/v1/storage/o/")


def test_upload_complete_enqueues_inspection(app, session_client, project):
    data = png_bytes()
    aid = upload(session_client, project, data)
    r = session_client.post(f"/api/v1/assets/{aid}/complete", json={"sha256": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 202, r.text
    assert r.json()["asset"]["state"] == "uploaded"
    assert r.json()["asset"]["bytes"] == len(data)
    with app.state.db() as db:
        job = db.scalar(select(Job).where(Job.kind == "asset.inspect"))
        assert job.options_json["asset_id"] == aid and job.state == "queued"


def test_hash_mismatch_rejected(session_client, project):
    aid = upload(session_client, project, png_bytes())
    r = session_client.post(f"/api/v1/assets/{aid}/complete", json={"sha256": "0" * 64})
    assert r.status_code == 422 and r.json()["error"]["code"] == "hash_mismatch"


def test_complete_without_upload_conflicts(session_client, project):
    aid = init(session_client, project).json()["asset_id"]
    r = session_client.post(f"/api/v1/assets/{aid}/complete", json={"sha256": "0" * 64})
    assert r.status_code == 409 and r.json()["error"]["code"] == "upload_missing"


def test_size_and_format_limits(session_client, project):
    assert init(session_client, project, size=20 * 1024 * 1024 + 1).status_code == 413
    assert init(session_client, project, mime="image/gif").status_code == 415
    assert init(session_client, project, role="mask", mime="image/jpeg").status_code == 415
    assert init(session_client, project, role="render").status_code == 422


def test_upload_larger_than_declared_is_refused(session_client, project):
    b = init(session_client, project, size=10).json()
    r = session_client.put(b["upload_url"], content=b"x" * 11, headers=b["headers"])
    assert r.status_code == 413


def test_forged_and_wrong_content_type_tokens_refused(session_client, project):
    b = init(session_client, project, size=10).json()
    forged = b["upload_url"][:-3] + "abc"
    assert session_client.put(forged, content=b"x", headers=b["headers"]).status_code == 403
    r = session_client.put(b["upload_url"], content=b"x", headers={"Content-Type": "text/html"})
    assert r.status_code == 415


def test_expired_upload_token_refused(app, session_client, project):
    storage = app.state.storage
    url, headers, _ = storage.upload_url("k/expired", ttl=-1, mime="image/png", max_bytes=10)
    assert session_client.put(url, content=b"x", headers=headers).status_code == 403


def test_other_session_cannot_read_or_complete_asset(app, session_client, project):
    aid = upload(session_client, project, png_bytes())
    other = TestClient(app)
    other.post("/api/v1/sessions")
    assert other.get(f"/api/v1/assets/{aid}").status_code == 404
    assert other.post(f"/api/v1/assets/{aid}/complete", json={"sha256": "0" * 64}).status_code == 404


def test_read_url_only_for_ready_assets(app, session_client, project):
    data = png_bytes()
    aid = upload(session_client, project, data)
    assert "read_url" not in session_client.get(f"/api/v1/assets/{aid}").json()
    with app.state.db() as db:
        db.get(Asset, uuid.UUID(aid)).state = "ready"
        db.commit()
    url = session_client.get(f"/api/v1/assets/{aid}").json()["read_url"]
    r = session_client.get(url)
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-type"] == "image/png"
