"""A handed-off result is inspected exactly like an upload of the target role (POST /handoffs -> asset.inspect)."""

import io
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from oc_api.app import create_app
from oc_api.models import Asset, Job, Project
from oc_worker.main import run_once
from PIL import Image
from sqlalchemy import select
from worker_helpers import load

FIX = Path(__file__).parents[3] / "fixtures" / "pocket-vfx"


def _png(w=320, h=240) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (40, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def _finished_output(db_factory, storage, project_id, role, data, mime):
    """A ready Product Lock output made by a succeeded mock render (``asset.job_id`` points at it)."""
    with db_factory() as db:
        project = db.get(Project, uuid.UUID(project_id))
        job = Job(project_id=project.id, session_id=project.session_id, input_revision=1, kind="product.render",
                  model_profile="mock_v1", state="succeeded")
        db.add(job)
        db.flush()
        aid = uuid.uuid4()
        key = f"{project.session_id}/{project.id}/{aid}/{role}{'.mp4' if mime == 'video/mp4' else '.png'}"
        storage.put_bytes(key, data, mime)
        db.add(Asset(id=aid, project_id=project.id, role=role, state="ready", object_key=key, mime=mime,
                     bytes=len(data), sha256="c" * 64, job_id=job.id))
        job.output_asset_ids = [str(aid)]
        db.commit()
        return str(aid)


def test_handed_off_clip_and_scene_inspect_ready(settings, db_factory, storage, ctx):
    app = create_app(settings.model_copy(update={"cookie_secure": False}), create_tables=True)
    client = TestClient(app)
    assert client.post("/api/v1/sessions").status_code == 201
    source = client.post("/api/v1/projects", json={"app_slug": "product-lock", "title": "Can ad"}).json()
    clip_bytes = (FIX / "stomp_landscape.mp4").read_bytes()
    render = _finished_output(db_factory, storage, source["id"], "render", clip_bytes, "video/mp4")
    cover = _finished_output(db_factory, storage, source["id"], "cover", _png(), "image/png")

    to_vfx = client.post("/api/v1/handoffs", json={"source_asset_id": render, "target_app": "pocket-vfx"})
    to_cam = client.post("/api/v1/handoffs", json={"source_asset_id": cover, "target_app": "camera-sketch"})
    assert to_vfx.status_code == 201 and to_cam.status_code == 201, (to_vfx.text, to_cam.text)
    while run_once(ctx):
        pass

    for out in (to_vfx.json(), to_cam.json()):
        job = load(db_factory, Job, out["inspect_job_id"])
        assert job.state == "succeeded", (job.error_code, job.error_message)

    clip = load(db_factory, Asset, to_vfx.json()["asset_id"])
    assert clip.state == "ready" and clip.role == "clip" and clip.mime == "video/mp4"
    assert clip.duration_ms and clip.frame_count and clip.width and clip.height
    assert storage.get_bytes(clip.object_key) == clip_bytes  # the original copy stays untouched
    with db_factory() as db:
        kids = {a.role for a in db.scalars(select(Asset).where(Asset.source_asset_id == clip.id))}
    assert kids == {"clip_preview", "clip_filmstrip"}

    scene = load(db_factory, Asset, to_cam.json()["asset_id"])
    assert scene.state == "ready" and scene.role == "scene" and scene.mime == "image/png"
    assert (scene.width, scene.height) == (320, 240)
    assert scene.object_key.endswith("/scene.png")  # normalized like an upload; the original key is kept in meta
    assert scene.meta_json["original_key"].endswith("/scene-original.png")

    # Provenance (and the upstream-mock flag) must survive the inspector rewriting the asset meta.
    for asset in (clip, scene):
        assert asset.meta_json["handoff"]["source_project_id"] == source["id"]
        assert asset.meta_json["handoff"]["mock"] is True
