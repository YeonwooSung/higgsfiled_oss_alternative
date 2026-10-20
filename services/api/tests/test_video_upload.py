import hashlib

from test_assets import init, upload


def pocket(session_client):
    r = session_client.post("/api/v1/projects", json={"app_slug": "pocket-vfx", "title": "Clip"})
    assert r.status_code == 201, r.text
    return r.json()


def test_clip_limit_is_100_mb_and_images_stay_20_mb(session_client, project):
    p = pocket(session_client)
    mb = 1024 * 1024
    assert init(session_client, p, role="clip", mime="video/mp4", size=100 * mb).status_code == 201
    r = init(session_client, p, role="clip", mime="video/mp4", size=100 * mb + 1)
    assert r.status_code == 413 and r.json()["error"]["message"] == "Videos must be 100 MB or smaller."
    r = init(session_client, p, role="clean_plate", mime="image/png", size=20 * mb + 1)
    assert r.status_code == 413 and r.json()["error"]["message"] == "Images must be 20 MB or smaller."
    assert init(session_client, project, size=20 * mb + 1).status_code == 413


def test_clip_formats(session_client):
    p = pocket(session_client)
    for mime in ("video/webm", "video/quicktime", "video/mp4"):
        assert init(session_client, p, role="clip", mime=mime, size=10).status_code == 201
    r = init(session_client, p, role="clip", mime="image/png", size=10)
    assert r.status_code == 415 and r.json()["error"]["message"] == "Use an MP4, MOV or WebM video."


def test_streamed_upload_complete_and_mov_key(app, session_client):
    p = pocket(session_client)
    data = b"\x00" * (3 * 1024 * 1024 + 7)
    aid = upload(session_client, p, data, role="clip", mime="video/quicktime")
    r = session_client.post(f"/api/v1/assets/{aid}/complete", json={"sha256": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 202, r.text
    assert r.json()["asset"]["bytes"] == len(data)
    from sqlalchemy import select

    from oc_api.models import Asset
    with app.state.db() as db:
        assert db.scalar(select(Asset).where(Asset.role == "clip")).object_key.endswith("clip-original.mov")


def test_put_over_declared_size_is_rejected_and_leaves_no_object(app, session_client):
    p = pocket(session_client)
    r = init(session_client, p, role="clip", mime="video/mp4", size=100)
    body = r.json()
    put = session_client.put(body["upload_url"], content=b"x" * 101, headers=body["headers"])
    assert put.status_code == 413
    done = session_client.post(f"/api/v1/assets/{body['asset_id']}/complete", json={"sha256": "0" * 64})
    assert done.status_code == 409 and done.json()["error"]["code"] == "upload_missing"
    import os
    assert not [f for _, _, fs in os.walk(app.state.settings.storage_dir) for f in fs]
