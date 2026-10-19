import hashlib
import io
import json
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from oc_api.models import Asset, Job, Project
from oc_api.product_lock.config import default_config
from open_creative.media.video import probe
from oc_worker.comfy.mock import MockRunner
from oc_worker.main import run_once
from worker_helpers import add_job, load, make_ctx, set_revision_config

FIXTURE = Path(__file__).parents[3] / "fixtures" / "product-lock" / "can_lock_cola.png"


def upload_raw(db_factory, storage, project, data, role="product", mime="image/png"):
    aid = uuid.uuid4()
    key = f"{project.session_id}/{project.id}/{aid}/{role}-original.png"
    storage.put_bytes(key, data, mime)
    with db_factory() as db:
        db.add(Asset(id=aid, project_id=project.id, role=role, state="uploaded", object_key=key, mime=mime,
                     bytes=len(data)))
        db.commit()
    return aid


def inspect(ctx, db_factory, project, aid):
    jid = add_job(db_factory, project, kind="asset.inspect", options={"asset_id": str(aid)}, uses_gpu=False,
                  reserved=0, profile="cpu")
    assert run_once(ctx)
    return load(db_factory, Job, jid)


def run_job(ctx, db_factory, project, kind, options, revision=1, uses_gpu=True):
    jid = add_job(db_factory, project, kind=kind, options=options, uses_gpu=uses_gpu, revision=revision)
    assert run_once(ctx)
    job = load(db_factory, Job, jid)
    assert job.state == "succeeded", (job.error_code, job.error_message)
    return job


def test_alpha_product_is_normalized_and_suggests_mask(ctx, db_factory, storage, project):
    aid = upload_raw(db_factory, storage, project, FIXTURE.read_bytes())
    job = inspect(ctx, db_factory, project, aid)
    assert job.state == "succeeded"
    product = load(db_factory, Asset, aid)
    assert product.state == "ready" and product.mime == "image/png"
    assert (product.width, product.height) == (300, 560)
    assert product.meta_json["has_alpha"] is True and product.meta_json["original_key"].endswith("original.png")
    assert product.sha256 == hashlib.sha256(storage.get_bytes(product.object_key)).hexdigest()
    assert product.meta_json["original_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    mask = load(db_factory, Asset, job.result_json["suggested_mask_asset_id"])
    assert mask.role == "mask" and (mask.width, mask.height) == (300, 560)
    assert mask.meta_json["origin"] == "alpha" and mask.source_asset_id == product.id


def test_scene_upload_normalizes_without_suggested_mask(ctx, db_factory, storage, project):
    aid = upload_raw(db_factory, storage, project, FIXTURE.read_bytes(), role="scene")
    job = inspect(ctx, db_factory, project, aid)
    assert job.state == "succeeded" and "suggested_mask_asset_id" not in job.result_json
    scene = load(db_factory, Asset, aid)
    assert scene.state == "ready" and (scene.width, scene.height) == (300, 560)
    with db_factory() as db:
        assert db.query(Asset).filter(Asset.project_id == project.id, Asset.role == "mask").count() == 0


def test_exif_rotated_jpeg_is_upright(ctx, db_factory, storage, project):
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (40, 20), (10, 20, 30)).save(buf, "JPEG", exif=exif.tobytes())
    aid = upload_raw(db_factory, storage, project, buf.getvalue(), mime="image/jpeg")
    inspect(ctx, db_factory, project, aid)
    a = load(db_factory, Asset, aid)
    assert (a.width, a.height) == (20, 40) and a.meta_json["has_alpha"] is False


def test_garbage_upload_is_rejected(ctx, db_factory, storage, project):
    aid = upload_raw(db_factory, storage, project, b"definitely not an image")
    job = inspect(ctx, db_factory, project, aid)
    assert job.state == "failed" and job.error_code == "unsupported_format"
    assert load(db_factory, Asset, aid).state == "rejected"


def prepared_project(ctx, db_factory, storage, project):
    aid = upload_raw(db_factory, storage, project, FIXTURE.read_bytes())
    mask_id = inspect(ctx, db_factory, project, aid).result_json["suggested_mask_asset_id"]
    cfg = default_config("mock_v1").model_dump(mode="json")
    cfg.update(product_asset_id=str(aid), mask_asset_id=mask_id, protect_boxes=[
        {"x": 0.05, "y": 0.35, "w": 0.9, "h": 0.25}])
    cfg["scene"]["preset_id"] = "neon_studio_v1"
    set_revision_config(db_factory, project, cfg)
    return aid, mask_id, cfg


def test_late_segmentation_never_overwrites_config(ctx, db_factory, storage, project):
    aid, mask_id, cfg = prepared_project(ctx, db_factory, storage, project)
    job = run_job(ctx, db_factory, project, "product.segment",
                  {"points": [{"x": 0.5, "y": 0.5, "label": 1}], "box": {"x": 0.02, "y": 0.02, "w": 0.96, "h": 0.96}})
    seg_mask = load(db_factory, Asset, job.result_json["mask_asset_id"])
    assert seg_mask.role == "mask" and (seg_mask.width, seg_mask.height) == (300, 560)
    assert seg_mask.meta_json["origin"] == "segmentation" and seg_mask.meta_json["revision"] == 1
    assert job.result_json["applied"] is False
    with db_factory() as db:
        from oc_api.models import ProjectRevision
        p = db.get(Project, project.id)
        assert db.get(ProjectRevision, (p.id, p.current_revision)).config_json["mask_asset_id"] == mask_id


def test_background_then_video_then_render_and_reuse(settings, db_factory, storage, mock_root, project):
    runner = MockRunner(mock_root)
    ctx = make_ctx(settings, db_factory, storage, runner)
    aid, mask_id, cfg = prepared_project(ctx, db_factory, storage, project)
    bg = run_job(ctx, db_factory, project, "product.background", {"preset_id": "neon_studio_v1", "seed": 5})
    bg_id = bg.output_asset_ids[0]
    cfg["scene"]["background_asset_id"] = bg_id
    set_revision_config(db_factory, project, cfg, revision=2)

    render = run_job(ctx, db_factory, project, "product.render", {}, revision=2)
    mp4, cover, report = (load(db_factory, Asset, a) for a in render.output_asset_ids[:3])
    assert (mp4.role, cover.role, report.role) == ("render", "cover", "integrity")
    path = storage._path(mp4.object_key)
    info = probe(path)
    assert (info["width"], info["height"], info["frame_count"], info["fps"]) == (720, 1280, 120, 24)
    integ = render.result_json["integrity"]
    assert integ["passed"] and integ["core_valid"] and integ["preencode_max_error"] <= 1
    assert integ["frames_checked"] == 120 and integ["revision"] == 2
    assert integ["postencode_metrics"]["frames"] == 120
    assert integ["output_check"]["ok"] is True
    video_id = render.result_json["background_video_asset_id"]
    video = load(db_factory, Asset, video_id)
    assert (video.width, video.height, video.frame_count) == (736, 1280, 121)

    # Re-render reusing the generated background video: no provider stage runs.
    submits_before = runner.submit_count
    cfg["shadow"]["opacity"] = 0.5
    set_revision_config(db_factory, project, cfg, revision=3)
    again = run_job(ctx, db_factory, project, "product.render", {"reuse_background_video_asset_id": video_id},
                    revision=3, uses_gpu=False)
    assert runner.submit_count == submits_before
    assert again.result_json["integrity"]["passed"]

    # Inspection crops for the first render reference that render's revision.
    insp = run_job(ctx, db_factory, project, "product.inspect", {"render_job_id": str(render.id)}, uses_gpu=False)
    crop = load(db_factory, Asset, insp.output_asset_ids[0])
    assert crop.role == "inspection" and crop.mime == "image/png"
    assert insp.result_json["revision"] == 2
