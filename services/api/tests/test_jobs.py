import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from oc_api.models import JobCost
from helpers import configure, put_asset, ready_product, set_job_state


def post_job(client, project, kind, options=None, revision=None, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    rev = revision or client.get(f"/api/v1/projects/{project['id']}").json()["current_revision"]
    return client.post(f"/api/v1/projects/{project['id']}/jobs",
                       json={"kind": kind, "revision": rev, "options": options or {}}, headers=headers)


def bg_job(client, project, key=None, seed=1):
    return post_job(client, project, "product.background", {"preset_id": "neon_studio_v1", "seed": seed}, key=key)


def test_background_job_is_accepted_and_reserves_budget(app, session_client, project):
    r = bg_job(session_client, project)
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["state"] == "queued" and job["kind"] == "product.background" and job["progress"] is None
    with app.state.db() as db:
        cost = db.scalar(select(JobCost).where(JobCost.job_id == uuid.UUID(job["id"])))
        assert cost.reserved_units == 120 and cost.unit == "gpu_seconds"
        assert cost.actual_units_nullable is None


def test_unknown_or_internal_kind_rejected(session_client, project):
    assert post_job(session_client, project, "asset.inspect").status_code == 422
    assert post_job(session_client, project, "camera.render").status_code == 422


def test_idempotent_replay_returns_same_job(session_client, project):
    a = bg_job(session_client, project, key="k-1").json()
    b = bg_job(session_client, project, key="k-1")
    assert b.status_code == 202 and b.json()["id"] == a["id"]


def test_idempotency_key_with_different_body_conflicts(session_client, project):
    bg_job(session_client, project, key="k-2", seed=1)
    r = bg_job(session_client, project, key="k-2", seed=2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_conflict"


def test_second_gpu_job_in_session_is_rate_limited(session_client, project):
    assert bg_job(session_client, project).status_code == 202
    r = bg_job(session_client, project, seed=9)
    assert r.status_code == 429 and r.json()["error"]["code"] == "gpu_busy"


def test_unknown_revision_rejected(session_client, project):
    r = post_job(session_client, project, "product.background", {"preset_id": "neon_studio_v1", "seed": 1},
                 revision=99)
    assert r.status_code == 422 and r.json()["error"]["code"] == "revision_not_found"


def test_segment_requires_ready_product(app, session_client, project):
    r = post_job(session_client, project, "product.segment", {"points": [{"x": 0.5, "y": 0.5, "label": 1}]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "product_required"
    pid = put_asset(app, project["id"], "product", b"x", "image/png", 10, 10, state="uploaded")
    configure(session_client, project, product_asset_id=pid)
    r = post_job(session_client, project, "product.segment", {"points": [{"x": 0.5, "y": 0.5, "label": 1}]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "asset_not_ready"


def test_segment_point_coordinates_must_be_normalized(app, session_client, project):
    product, _ = ready_product(app, project["id"])
    configure(session_client, project, product_asset_id=product)
    r = post_job(session_client, project, "product.segment", {"points": [{"x": 1.5, "y": 0.5, "label": 1}]})
    assert r.status_code == 422


def test_render_requires_background(app, session_client, project):
    product, mask = ready_product(app, project["id"])
    configure(session_client, project, product_asset_id=product, mask_asset_id=mask)
    r = post_job(session_client, project, "product.render")
    assert r.status_code == 422 and r.json()["error"]["code"] == "background_required"


def test_render_rejects_mask_dimension_mismatch(app, session_client, project):
    product, mask = ready_product(app, project["id"], mask_shape=(60, 40))
    bg = put_asset(app, project["id"], "background", b"x", "image/png", 720, 1280)
    configure(session_client, project, product_asset_id=product, mask_asset_id=mask, scene__background_asset_id=bg)
    r = post_job(session_client, project, "product.render")
    assert r.status_code == 422 and r.json()["error"]["code"] == "mask_dimension_mismatch"


def test_render_rejects_empty_core(app, session_client, project):
    product, mask = ready_product(app, project["id"], empty_mask=True)
    bg = put_asset(app, project["id"], "background", b"x", "image/png", 720, 1280)
    configure(session_client, project, product_asset_id=product, mask_asset_id=mask, scene__background_asset_id=bg)
    r = post_job(session_client, project, "product.render")
    assert r.status_code == 422 and r.json()["error"]["code"] == "core_too_small"


def test_render_with_reused_video_reserves_no_gpu(app, session_client, project):
    product, mask = ready_product(app, project["id"])
    video = put_asset(app, project["id"], "background_video", b"x", "video/mp4", 736, 1280)
    configure(session_client, project, product_asset_id=product, mask_asset_id=mask)
    r = post_job(session_client, project, "product.render", {"reuse_background_video_asset_id": video})
    assert r.status_code == 202, r.text
    with app.state.db() as db:
        cost = db.scalar(select(JobCost).where(JobCost.job_id == uuid.UUID(r.json()["id"])))
        assert cost.reserved_units == 0
    # a CPU-only render does not count against the GPU slot
    assert bg_job(session_client, project).status_code == 202


def test_queue_limit_per_session(app, session_client, project):
    product, mask = ready_product(app, project["id"])
    video = put_asset(app, project["id"], "background_video", b"x", "video/mp4", 736, 1280)
    configure(session_client, project, product_asset_id=product, mask_asset_id=mask)
    assert post_job(session_client, project, "product.render", {"reuse_background_video_asset_id": video}).status_code == 202
    r = post_job(session_client, project, "product.render", {"reuse_background_video_asset_id": video})
    assert r.status_code == 429 and r.json()["error"]["code"] == "render_busy"


def test_unavailable_profile_returns_503(session_client, project):
    configure(session_client, project, model_profile="open_weights_v1")
    r = bg_job(session_client, project)
    assert r.status_code == 503 and r.json()["error"]["code"] == "profile_unavailable"


def test_cancel_queued_job_is_immediate(app, session_client, project):
    job = bg_job(session_client, project).json()
    r = session_client.post(f"/api/v1/jobs/{job['id']}/cancel")
    assert r.status_code == 202 and r.json()["state"] == "cancelled"
    with app.state.db() as db:
        cost = db.scalar(select(JobCost).where(JobCost.job_id == uuid.UUID(job["id"])))
        assert cost.actual_units_nullable == 0  # nothing reached the provider


def test_cancel_running_job_records_intent(app, session_client, project):
    job = bg_job(session_client, project).json()
    set_job_state(app, job["id"], "running")
    r = session_client.post(f"/api/v1/jobs/{job['id']}/cancel")
    assert r.json()["state"] == "cancel_requested" and r.json()["cancellation_requested"] is True


def test_cancel_finished_job_conflicts(app, session_client, project):
    job = bg_job(session_client, project).json()
    set_job_state(app, job["id"], "succeeded")
    assert session_client.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 409


def test_other_session_cannot_read_job(app, session_client, project):
    job = bg_job(session_client, project).json()
    other = TestClient(app)
    other.post("/api/v1/sessions")
    assert other.get(f"/api/v1/jobs/{job['id']}").status_code == 404


def test_events_stream_replays_after_last_event_id(app, session_client, project):
    job = bg_job(session_client, project).json()
    session_client.post(f"/api/v1/jobs/{job['id']}/cancel")
    with session_client.stream("GET", f"/api/v1/jobs/{job['id']}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        text = "".join(r.iter_text())
    assert "id: 1" in text and "id: 2" in text and '"cancelled"' in text
    with session_client.stream("GET", f"/api/v1/jobs/{job['id']}/events", headers={"Last-Event-ID": "1"}) as r:
        text = "".join(r.iter_text())
    assert "id: 1\n" not in text and "id: 2" in text


def test_estimate_marks_price_unknown(session_client, project):
    r = session_client.post(f"/api/v1/projects/{project['id']}/estimates",
                            json={"kind": "product.background", "revision": 1})
    assert r.status_code == 200
    e = r.json()
    assert e["reserved_units"] == 120 and e["unit"] == "gpu_seconds"
    assert e["price"] is None and e["price_status"] == "unknown"
    assert e["profile"]["id"] == "mock_v1" and e["profile"]["is_mock"] is True


def test_capabilities_and_presets(session_client):
    caps = session_client.get("/api/v1/capabilities", params={"app_slug": "product-lock"}).json()
    ids = {p["id"]: p for p in caps["profiles"]}
    assert ids["mock_v1"]["available"] and not ids["open_weights_v1"]["available"]
    assert caps["limits"]["duration_ms"] == 5000 and caps["limits"]["aspects"] == ["9:16", "16:9"]
    presets = session_client.get("/api/v1/product-lock/presets").json()["presets"]
    assert [p["id"] for p in presets] == ["soft_studio_v1", "neon_studio_v1", "botanical_v1"]


def test_integrity_unavailable_until_render_finishes(session_client, project):
    job = bg_job(session_client, project).json()
    r = session_client.get(f"/api/v1/jobs/{job['id']}/integrity")
    assert r.status_code == 404 and r.json()["error"]["code"] == "integrity_not_available"
