import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from oc_api.db import utcnow
from oc_api.models import Asset, Job, Project, ProjectRevision
from oc_api.models import Session as SessionRow
from oc_api.pocket_vfx.config import default_config
from oc_worker.main import run_once
from worker_helpers import add_job, load

FIX = Path(__file__).parents[3] / "fixtures" / "pocket-vfx"


@pytest.fixture
def pvfx_project(db_factory):
    with db_factory() as db:
        s = SessionRow(expires_at=utcnow() + timedelta(days=1))
        db.add(s)
        db.flush()
        p = Project(session_id=s.id, app_slug="pocket-vfx", title="t", current_revision=1)
        db.add(p)
        db.flush()
        db.add(ProjectRevision(project_id=p.id, revision=1, config_json=default_config().model_dump(mode="json"),
                               config_schema_version=1))
        db.commit()
        db.refresh(p)
        db.expunge(p)
        return p


def upload_clip(db_factory, storage, project, data: bytes, mime="video/mp4", role="clip"):
    aid = uuid.uuid4()
    key = f"{project.session_id}/{project.id}/{aid}/{role}-original.mp4"
    storage.put_bytes(key, data, mime)
    with db_factory() as db:
        db.add(Asset(id=aid, project_id=project.id, role=role, state="uploaded", object_key=key, mime=mime,
                     bytes=len(data), sha256="a" * 64))
        db.commit()
    return aid


def inspect(ctx, db_factory, project, aid):
    jid = add_job(db_factory, project, kind="asset.inspect", options={"asset_id": str(aid)}, uses_gpu=False,
                  reserved=0, profile="cpu")
    assert run_once(ctx)
    return load(db_factory, Job, jid)


def test_rotated_portrait_clip_is_ready_with_preview_and_filmstrip(ctx, db_factory, storage, pvfx_project):
    data = (FIX / "rotated_portrait.mp4").read_bytes()
    aid = upload_clip(db_factory, storage, pvfx_project, data)
    job = inspect(ctx, db_factory, pvfx_project, aid)
    assert job.state == "succeeded", (job.error_code, job.error_message)
    clip = load(db_factory, Asset, aid)
    assert clip.state == "ready" and clip.mime == "video/mp4"
    assert (clip.width, clip.height) == (720, 1280)
    assert clip.duration_ms and clip.frame_count
    src = clip.meta_json["source"]
    assert src["rotation"] in (90, 270) and (src["display_w"], src["display_h"]) == (720, 1280)
    assert clip.meta_json["original_sha256"] == "a" * 64
    assert storage.get_bytes(clip.object_key) == data  # original untouched
    with db_factory() as db:
        kids = {a.role: a for a in db.scalars(select(Asset).where(Asset.source_asset_id == aid))}
    assert set(kids) == {"clip_preview", "clip_filmstrip"}
    prev = kids["clip_preview"]
    assert prev.mime == "video/mp4" and prev.object_key != clip.object_key
    assert prev.width < prev.height and prev.height <= 640 and prev.width * 1280 == pytest.approx(prev.height * 720, abs=2 * 1280)
    assert prev.frame_count and prev.fps_num == 24
    assert kids["clip_filmstrip"].mime == "image/png"


def test_corrupt_clip_is_rejected(ctx, db_factory, storage, pvfx_project):
    aid = upload_clip(db_factory, storage, pvfx_project, b"not a video at all" * 100)
    job = inspect(ctx, db_factory, pvfx_project, aid)
    assert job.state == "failed" and job.error_code == "unsupported_codec"
    assert job.error_message == "This video codec is not supported."
    clip = load(db_factory, Asset, aid)
    assert clip.state == "rejected" and clip.error_code == "unsupported_codec"


def test_clean_plate_uses_image_path_without_mask(ctx, db_factory, storage, pvfx_project):
    aid = upload_clip(db_factory, storage, pvfx_project, (FIX / "clean_plate.png").read_bytes(),
                      mime="image/png", role="clean_plate")
    job = inspect(ctx, db_factory, pvfx_project, aid)
    assert job.state == "succeeded"
    assert "suggested_mask_asset_id" not in (job.result_json or {})
    assert load(db_factory, Asset, aid).state == "ready"


def test_clip_turns_ready_only_after_its_preview_and_filmstrip_exist(ctx, db_factory, storage, pvfx_project,
                                                                     monkeypatch):
    """The client stops polling once the clip is ready, so the preview and filmstrip must already be stored (a crash
    between the two leaves the clip `uploaded` and the retry makes them again)."""
    from oc_worker.stages import JobRun

    aid = upload_clip(db_factory, storage, pvfx_project, (FIX / "stomp_landscape.mp4").read_bytes())
    seen = []
    store = JobRun.store_asset

    def spy(self, role, *a, **kw):
        out = store(self, role, *a, **kw)
        seen.append((role, load(db_factory, Asset, aid).state))  # committed state seen by another session
        return out

    monkeypatch.setattr(JobRun, "store_asset", spy)
    job = inspect(ctx, db_factory, pvfx_project, aid)
    assert job.state == "succeeded", (job.error_code, job.error_message)
    assert seen == [("clip_preview", "uploaded"), ("clip_filmstrip", "uploaded")]
    assert load(db_factory, Asset, aid).state == "ready"


def test_clip_inspect_heartbeats_through_probe_and_preview_decode(ctx, db_factory, storage, pvfx_project,
                                                                  monkeypatch):
    """A long clip keeps its lease: the per-frame probe and preview decode run frame_checkpoint (heartbeat + cancel)."""
    import importlib

    mod = importlib.import_module("oc_worker.executors.inspect_asset")
    calls: list[int] = []
    real = mod.frame_checkpoint

    def spy(run, i, *a, **kw):
        calls.append(i)
        return real(run, i, *a, **kw)

    monkeypatch.setattr(mod, "frame_checkpoint", spy)
    aid = upload_clip(db_factory, storage, pvfx_project, (FIX / "stomp_landscape.mp4").read_bytes())
    job = inspect(ctx, db_factory, pvfx_project, aid)
    assert job.state == "succeeded", (job.error_code, job.error_message)
    assert len(calls) >= 2 * 120  # probe pass + every decoded preview frame


def test_cancel_during_clip_decode_cancels_instead_of_rejecting(ctx, db_factory, storage, pvfx_project, monkeypatch):
    import importlib

    from oc_worker.stages import Cancelled

    mod = importlib.import_module("oc_worker.executors.inspect_asset")

    def cancel(run, i, *a, **kw):
        if i == 30:
            raise Cancelled()

    monkeypatch.setattr(mod, "frame_checkpoint", cancel)
    aid = upload_clip(db_factory, storage, pvfx_project, (FIX / "stomp_landscape.mp4").read_bytes())
    job = inspect(ctx, db_factory, pvfx_project, aid)
    assert job.state == "cancelled"
    assert load(db_factory, Asset, aid).state == "uploaded"  # not rejected as an unsupported codec
