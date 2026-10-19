"""Regression tests for issues found in branch review."""

import hashlib
import io
import uuid

import pytest
from PIL import Image
from sqlalchemy import select

from helpers import put_asset
from oc_api.cleanup import run_cleanup
from oc_api.jobs_service import add_event
from oc_api.models import Asset, CleanupTask, Job, JobEvent, Project
from oc_api.settings import Settings
from oc_api.storage import LocalStorage


def test_complete_checks_size_before_reading_and_deletes_oversize(app, session_client, project):
    init = session_client.post(f"/api/v1/projects/{project['id']}/assets/init",
                               json={"role": "product", "filename": "a.png", "mime": "image/png", "bytes": 10}).json()
    with app.state.db() as db:
        key = db.get(Asset, uuid.UUID(init["asset_id"])).object_key
    data = b"x" * 50  # S3 presigned PUTs cannot enforce the declared size
    app.state.storage.put_bytes(key, data)
    calls = []
    original = app.state.storage.get_bytes
    app.state.storage.get_bytes = lambda k: calls.append(k) or original(k)
    r = session_client.post(f"/api/v1/assets/{init['asset_id']}/complete", json={"sha256": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 413
    assert calls == []  # never loaded into memory
    assert not app.state.storage.exists(key)


def test_forged_non_ascii_token_is_403_not_500(session_client):
    assert session_client.get("/api/v1/storage/o/abc.é").status_code == 403


def test_add_event_retries_sequence_collision(app, session_client, project, monkeypatch):
    job_id = session_client.post(f"/api/v1/projects/{project['id']}/jobs",
                                 json={"kind": "product.background", "revision": 1,
                                       "options": {"preset_id": "neon_studio_v1", "seed": 1}}).json()["id"]
    import oc_api.jobs_service as js
    real = js._next_sequence
    stale = iter([1])  # first attempt reuses an existing sequence (another writer won)
    monkeypatch.setattr(js, "_next_sequence", lambda db, job: next(stale, None) or real(db, job))
    with app.state.db() as db:
        job = db.get(Job, uuid.UUID(job_id))
        seq = add_event(db, job, "progress", {"progress": 0.5})
        db.commit()
        assert seq == 2
        assert len(db.scalars(select(JobEvent).where(JobEvent.job_id == job.id)).all()) == 2


def test_cancel_does_not_resurrect_finished_job(app, session_client, project):
    """API loaded the job as running, then the worker finished it: cancel must not overwrite."""
    job_id = session_client.post(f"/api/v1/projects/{project['id']}/jobs",
                                 json={"kind": "product.background", "revision": 1,
                                       "options": {"preset_id": "neon_studio_v1", "seed": 1}}).json()["id"]
    from oc_api.routes import jobs as jobs_routes
    with app.state.db() as db:
        db.get(Job, uuid.UUID(job_id)).state = "running"
        db.commit()
    real_owned = jobs_routes._owned_job

    def owned_then_worker_finishes(jid, db, session):
        job = real_owned(jid, db, session)
        with app.state.db() as other:
            other.get(Job, job.id).state = "succeeded"
            other.commit()
        return job

    jobs_routes._owned_job = owned_then_worker_finishes
    try:
        r = session_client.post(f"/api/v1/jobs/{job_id}/cancel")
    finally:
        jobs_routes._owned_job = real_owned
    assert r.status_code == 409
    with app.state.db() as db:
        assert db.get(Job, uuid.UUID(job_id)).state == "succeeded"


def test_cleanup_waits_for_active_jobs(app, session_client, project):
    job_id = session_client.post(f"/api/v1/projects/{project['id']}/jobs",
                                 json={"kind": "product.background", "revision": 1,
                                       "options": {"preset_id": "neon_studio_v1", "seed": 1}}).json()["id"]
    with app.state.db() as db:
        db.get(Job, uuid.UUID(job_id)).state, db.get(Job, uuid.UUID(job_id)).lease_owner = "running", "w1"
        db.commit()
    assert session_client.delete(f"/api/v1/projects/{project['id']}").status_code == 202
    assert run_cleanup(app.state.db, app.state.storage) == 0  # job still cancel_requested
    with app.state.db() as db:
        db.get(Job, uuid.UUID(job_id)).state = "cancelled"
        db.commit()
    assert run_cleanup(app.state.db, app.state.storage) == 1


def test_default_signing_secret_refused_in_secure_mode(tmp_path):
    from oc_api.app import create_app
    with pytest.raises(RuntimeError, match="OC_SIGNING_SECRET"):
        create_app(Settings(database_url=f"sqlite+pysqlite:///{tmp_path}/x.db", storage_dir=tmp_path,
                            cookie_secure=True, signing_secret="change-me-to-a-long-random-string"))
