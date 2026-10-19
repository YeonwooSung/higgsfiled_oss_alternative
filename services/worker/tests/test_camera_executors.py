"""Camera Sketch worker executors: analyze -> guide -> cinematic -> export (mock profile, SQLite, temp storage)."""

import hashlib
import io
import json
import time
import zipfile
from datetime import timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from sqlalchemy import select

from oc_api.camera_sketch.config import CameraSketchConfig, default_config
from oc_api.camera_sketch.jobs import geometry_hash
from oc_api.db import utcnow
from oc_api.models import Asset, Job, JobStage, Project, ProjectRevision
from oc_api.models import Session as SessionRow
from oc_worker.comfy.mock import MockRunner
from oc_worker.main import run_once
from open_creative.camera_sketch.scene import decode_depth_png16
from open_creative.media.video import decode_frames, probe
from test_executors import upload_raw
from worker_helpers import add_job, load, make_ctx, set_revision_config

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "camera-sketch"
SCENES = json.loads((FIX / "scenes.json").read_text())["scenes"]
OUTPUT_CONTRACT = {"width": 1280, "height": 720, "frame_count": 120, "fps": 24, "codec": "h264",
                   "pix_fmt": "yuv420p"}
TIMINGS: dict[str, float] = {}


class Crash(BaseException):
    """Simulates the worker process dying."""


class CrashAfterSubmit(MockRunner):
    def inspect(self, provider_job_id):
        raise Crash()


@pytest.fixture
def cam_project(db_factory):
    with db_factory() as db:
        s = SessionRow(expires_at=utcnow() + timedelta(days=1))
        db.add(s)
        db.flush()
        p = Project(session_id=s.id, app_slug="camera-sketch", title="t", current_revision=1)
        db.add(p)
        db.flush()
        db.add(ProjectRevision(project_id=p.id, revision=1,
                               config_json=default_config("mock_v1").model_dump(mode="json"),
                               config_schema_version=1))
        db.commit()
        return p


def smooth_scene_png(w=320, h=180) -> bytes:
    """Textured image whose mock (heuristic) depth is smooth: no depth discontinuities -> no mesh cuts."""
    rng = np.random.default_rng(7)
    # bright with moderate texture: the mock depth (gradient + blurred luminance) stays smooth everywhere
    small = np.clip(200 + rng.integers(-40, 40, (h // 6, w // 6, 3)), 0, 255).astype(np.uint8)
    img = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    img = cv2.GaussianBlur(img, (0, 0), 1.2)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "PNG")
    return buf.getvalue()


def run_kind(ctx, db_factory, project, kind, options, revision, uses_gpu=True, profile="mock_v1", expect="succeeded"):
    jid = add_job(db_factory, project, kind=kind, options=options, uses_gpu=uses_gpu, revision=revision,
                  profile=profile, reserved=0)
    t0 = time.monotonic()
    assert run_once(ctx)
    TIMINGS[kind] = round(time.monotonic() - t0, 1)
    job = load(db_factory, Job, jid)
    assert job.state == expect, (job.error_code, job.error_message)
    return job


def prepared(ctx, db_factory, storage, project, image: bytes, *, target=(0.5, 0.5), path=None):
    """Upload + inspect the scene (rev 1), analyze, then rev 2 with the depth and path applied (as the client
    would via PATCH)."""
    aid = upload_raw(db_factory, storage, project, image, role="scene")
    run_kind(ctx, db_factory, project, "asset.inspect", {"asset_id": str(aid)}, 1, uses_gpu=False, profile="cpu")
    cfg = default_config("mock_v1").model_dump(mode="json")
    cfg["scene_asset_id"] = str(aid)
    cfg["target"].update(u=target[0], v=target[1])
    set_revision_config(db_factory, project, cfg, revision=1)
    analyze = run_kind(ctx, db_factory, project, "camera.analyze", {}, 1)
    cfg["depth_asset_id"] = analyze.result_json["depth_asset_id"]
    cfg["path"].update(path or {"preset": "slide", "lateral_ratio": 0.0, "push_ratio": 0.0, "arc_deg": 0.0,
                                 "sketch_points": []})
    set_revision_config(db_factory, project, cfg, revision=2)
    return aid, analyze, cfg


def asset_bytes(db_factory, storage, aid) -> bytes:
    return storage.get_bytes(load(db_factory, Asset, aid).object_key)


def assert_output_contract(path):
    info = probe(path)
    assert {k: info[k] for k in OUTPUT_CONTRACT} == OUTPUT_CONTRACT


# ---- analyze ---------------------------------------------------------------------------------------------


def test_analyze_stores_depth_preview_and_never_patches_config(ctx, db_factory, storage, cam_project):
    sc = SCENES["room"]
    aid = upload_raw(db_factory, storage, cam_project, (FIX / sc["image"]).read_bytes(), role="scene")
    run_kind(ctx, db_factory, cam_project, "asset.inspect", {"asset_id": str(aid)}, 1, uses_gpu=False,
             profile="cpu")
    cfg = default_config("mock_v1").model_dump(mode="json")
    cfg["scene_asset_id"] = str(aid)
    set_revision_config(db_factory, cam_project, cfg, revision=1)

    job = run_kind(ctx, db_factory, cam_project, "camera.analyze", {}, 1)
    r = job.result_json
    depth = load(db_factory, Asset, r["depth_asset_id"])
    assert depth.role == "depth" and depth.mime == "image/png" and (depth.width, depth.height) == (960, 540)
    assert depth.source_asset_id == aid
    m = depth.meta_json
    assert m["source_asset_id"] == str(aid) and m["model"] == "depth-anything-v2-small"
    assert m["convention"] == {"kind": "inverse_relative", "scale": 1.0, "offset": 0.0, "eps": 1e-6}
    assert (m["min"], m["max"]) == (sc["depth_png16"]["min"], sc["depth_png16"]["max"])
    assert m["mock"] is True
    data = asset_bytes(db_factory, storage, depth.id)
    assert data == (FIX / sc["depth"]).read_bytes()  # png16 stored as-is

    prev = load(db_factory, Asset, r["depth_preview_asset_id"])
    assert prev.role == "depth_preview" and (prev.width, prev.height) == (960, 540)
    p8 = np.asarray(Image.open(io.BytesIO(asset_bytes(db_factory, storage, prev.id))))
    assert p8.dtype == np.uint8 and p8.ndim == 2
    d = decode_depth_png16(data, m)
    near, far = np.unravel_index(np.argmax(d), d.shape), np.unravel_index(np.argmin(d), d.shape)
    assert p8[near] > p8[far]  # near (larger inverse depth) is bright

    K = np.array(r["intrinsics_suggestion"]["K"])
    assert r["intrinsics_suggestion"]["focal_length_mm_equiv"] == 50
    assert K[0, 0] == pytest.approx(50 / 36 * 960) and K[0, 2] == 480 and K[1, 2] == 270
    with db_factory() as db:  # the executor never patches the project config
        assert db.get(ProjectRevision, (cam_project.id, 1)).config_json["depth_asset_id"] is None
        assert db.get(Project, cam_project.id).current_revision == 1


# ---- guide -----------------------------------------------------------------------------------------------


def test_guide_static_path_identical_frames_no_holes(ctx, db_factory, storage, cam_project):
    _, _, cfg = prepared(ctx, db_factory, storage, cam_project, smooth_scene_png())
    guide = run_kind(ctx, db_factory, cam_project, "camera.guide", {}, 2, uses_gpu=False)
    r = guide.result_json
    assert r["frame_count"] == 120 and r["mode"] == "geometry"
    assert r["geometry_hash"] == geometry_hash(CameraSketchConfig.model_validate(cfg))
    assert r["hole_ratio_max"] == 0 and r["hole_ratio_mean"] == 0
    assert r["target_visibility_min"] == pytest.approx(1.0)
    assert r["warnings"] == [] and r["revision"] == 2
    assert set(r["fill_strategy"]) == {"rgb_small_holes", "rgb_guide", "depth_guide"}

    geo = load(db_factory, Asset, r["geometry_video_asset_id"])
    assert geo.role == "geometry_video" and geo.meta_json["label"] == "Path render"
    geo_path = storage._path(geo.object_key)
    assert_output_contract(geo_path)
    frames = decode_frames(geo_path)
    first = next(frames).astype(int)
    assert max(float(np.abs(first - f).mean()) for f in frames) < 0.5  # only H.264 GOP noise after encoding

    dep = load(db_factory, Asset, r["guide_depth_video_asset_id"])
    assert dep.role == "guide_depth_video"
    assert_output_contract(storage._path(dep.object_key))
    assert dep.meta_json["depth_guide"]["mapping"] == "inverse_depth_near_white"

    val = load(db_factory, Asset, r["guide_validity_asset_id"])
    assert val.role == "guide_validity" and val.mime == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(asset_bytes(db_factory, storage, val.id)))
    names = z.namelist()
    assert [n for n in names if n.startswith("validity_")] == [f"validity_{i:03d}.png" for i in range(120)]
    man = json.loads(z.read("manifest.json"))
    assert man["frame_count"] == 120 and (man["width"], man["height"]) == (1280, 720)
    assert len(man["hole_ratio"]) == 120 and max(man["hole_ratio"]) == 0
    assert len(man["rgb_sha256"]) == 120 and len(set(man["rgb_sha256"])) == 1  # static -> identical frames
    v0 = Image.open(io.BytesIO(z.read("validity_000.png")))
    assert v0.mode == "1" and v0.size == (1280, 720) and np.asarray(v0).all()

    art = json.loads(asset_bytes(db_factory, storage, r["path_asset_id"]))
    assert load(db_factory, Asset, r["path_asset_id"]).role == "path_artifact"
    assert art["user_config"] == cfg and art["revision"] == 2
    assert art["compiled"]["coordinate_convention"] == "opencv_T_cw" and len(art["compiled"]["samples"]) == 120
    assert set(guide.output_asset_ids) == {r["path_asset_id"], geo.id.__str__(), str(dep.id), str(val.id)}


# ---- cinematic -------------------------------------------------------------------------------------------


def test_cinematic_restart_output_contract_and_exports(settings, db_factory, storage, mock_root, cam_project):
    ctx = make_ctx(settings, db_factory, storage, MockRunner(mock_root))
    scene_id, analyze, cfg = prepared(ctx, db_factory, storage, cam_project, smooth_scene_png())
    guide = run_kind(ctx, db_factory, cam_project, "camera.guide", {}, 2, uses_gpu=False)
    geo_before = {a: hashlib.sha256(asset_bytes(db_factory, storage, a)).hexdigest()
                  for a in guide.output_asset_ids}

    # Generation settings changed in rev 3 (geometry unchanged) -> the guide is still valid.
    cfg["generation"]["seed"] = 99
    set_revision_config(db_factory, cam_project, cfg, revision=3)
    jid = add_job(db_factory, cam_project, kind="camera.cinematic", options={"guide_job_id": str(guide.id)},
                  revision=3, reserved=600)
    crashing = make_ctx(settings, db_factory, storage, CrashAfterSubmit(mock_root), worker_id="w1")
    with pytest.raises(Crash):
        run_once(crashing)
    with db_factory() as db:
        st = db.scalar(select(JobStage).where(JobStage.job_id == jid, JobStage.stage_name == "generate"))
        assert st.provider_job_id and st.submission_key
        db.get(Job, jid).lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    runner = MockRunner(mock_root)
    resumed = make_ctx(settings, db_factory, storage, runner, worker_id="w2")
    t0 = time.monotonic()
    assert run_once(resumed)
    TIMINGS["camera.cinematic(resume)"] = round(time.monotonic() - t0, 1)
    assert runner.submit_count == 0  # inspected and collected, never resubmitted
    cin = load(db_factory, Job, jid)
    assert cin.state == "succeeded", (cin.error_code, cin.error_message)
    r = cin.result_json
    assert r["mode"] == "cinematic" and r["guide_job_id"] == str(guide.id) and r["frame_count"] == 120
    assert r["qa"]["status"] in ("ok", "check", "unknown") and "reasons" in r["qa"]
    assert r["exact_camera_control"] is False and r["mock"] is True
    video = load(db_factory, Asset, r["cinematic_video_asset_id"])
    assert video.role == "cinematic_video" and video.meta_json["label"] == "AI generated"
    assert video.meta_json["seed"] == 99 and video.meta_json["control_strength"] == 0.0
    assert_output_contract(storage._path(video.object_key))
    # geometry assets untouched
    assert load(db_factory, Job, guide.id).output_asset_ids == guide.output_asset_ids
    assert {a: hashlib.sha256(asset_bytes(db_factory, storage, a)).hexdigest()
            for a in guide.output_asset_ids} == geo_before
    assert not set(cin.output_asset_ids) & set(guide.output_asset_ids)

    # ---- exports -----------------------------------------------------------------------------------------
    def export(source, fmt):
        return run_kind(ctx, db_factory, cam_project, "camera.export",
                        {"source_job_id": str(source.id), "format": fmt}, source.input_revision,
                        uses_gpu=False, profile="cpu")

    assert export(guide, "mp4").output_asset_ids == [guide.result_json["geometry_video_asset_id"]]
    assert export(cin, "mp4").output_asset_ids == [r["cinematic_video_asset_id"]]

    pj = export(cin, "path_json")
    path_doc = json.loads(asset_bytes(db_factory, storage, pj.output_asset_ids[0]))
    assert path_doc["revision"] == 3 and path_doc["source_job_id"] == str(cin.id)
    assert path_doc["guide_job_id"] == str(guide.id) and path_doc["guide_revision"] == 2
    assert len(path_doc["compiled"]["samples"]) == 120 and path_doc["user_config"]["generation"]["seed"] == 99

    gz = export(guide, "guides_zip")
    z = zipfile.ZipFile(io.BytesIO(asset_bytes(db_factory, storage, gz.output_asset_ids[0])))
    assert {"guide_depth.mp4", "guide_validity.zip", "manifest.json"} <= set(z.namelist())
    assert json.loads(z.read("manifest.json"))["revision"] == 2

    for source, rev, mode_line in ((guide, 2, "Path render"), (cin, 3, "AI generated")):
        rz = export(source, "recipe_zip")
        a = load(db_factory, Asset, rz.output_asset_ids[0])
        assert a.role == "recipe" and a.meta_json["revision"] == rev
        z = zipfile.ZipFile(io.BytesIO(storage.get_bytes(a.object_key)))
        names = set(z.namelist())
        assert {"README.md", "config.json", "path.json", "jobs.json", "inputs.json",
                "models/camera-sketch.manifest.json", "workflows/README.md"} <= names
        readme = z.read("README.md").decode()
        assert mode_line in readme and "경로 렌더" in readme and "Mock" in readme
        assert "no exact camera control" in readme and "models/camera-sketch.manifest.json" in readme
        assert "workflows/camera-sketch/" in readme
        conf = json.loads(z.read("config.json"))
        assert conf["revision"] == rev
        assert json.loads(z.read("path.json"))["revision"] == rev
        jobs = json.loads(z.read("jobs.json"))
        assert {j["kind"] for j in jobs} >= {source.kind, "camera.analyze"}
        assert all("submission_key" not in s for j in jobs for s in j["stages"])
        inputs = json.loads(z.read("inputs.json"))
        assert inputs["scene"]["sha256"] == load(db_factory, Asset, scene_id).sha256
        assert inputs["depth"]["sha256"] == load(db_factory, Asset, analyze.result_json["depth_asset_id"]).sha256
        assert "object_key" not in json.dumps(inputs)
    print("timings", TIMINGS, "qa", r.get("qa"))


def _fake_guide(db_factory, project, cfg, revision, result):
    with db_factory() as db:
        job = Job(project_id=project.id, session_id=project.session_id, input_revision=revision,
                  kind="camera.guide", options_json={}, model_profile="mock_v1", uses_gpu=False,
                  state="succeeded", result_json=result, output_asset_ids=[])
        db.add(job)
        db.commit()
        return job.id


def test_cinematic_rejects_stale_guide(ctx, db_factory, storage, cam_project):
    cfg = default_config("mock_v1").model_dump(mode="json")
    set_revision_config(db_factory, cam_project, cfg, revision=1)
    gid = _fake_guide(db_factory, cam_project, cfg, 1, {"frame_count": 120, "geometry_hash": "0" * 64})
    job = run_kind(ctx, db_factory, cam_project, "camera.cinematic", {"guide_job_id": str(gid)}, 1,
                   expect="failed")
    assert job.error_code == "guide_stale"


def test_cinematic_open_weights_requires_calibrated_control(ctx, db_factory, storage, cam_project):
    cfg = default_config("open_weights_v1").model_dump(mode="json")
    set_revision_config(db_factory, cam_project, cfg, revision=1)
    h = geometry_hash(CameraSketchConfig.model_validate(cfg))
    gid = _fake_guide(db_factory, cam_project, cfg, 1, {"frame_count": 120, "geometry_hash": h})
    runner = MockRunner(Path(ctx.settings.work_dir).parent / "unused")
    c = make_ctx(ctx.settings, db_factory, storage, runner)
    job = run_kind(c, db_factory, cam_project, "camera.cinematic", {"guide_job_id": str(gid)}, 1,
                   profile="open_weights_v1", expect="failed")
    assert job.error_code == "control_uncalibrated" and runner.submit_count == 0


# ---- full-resolution end-to-end on a fixture scene (slow) ------------------------------------------------


@pytest.mark.slow
def test_room_slide_end_to_end(ctx, db_factory, storage, cam_project):
    sc = SCENES["room"]
    prepared(ctx, db_factory, storage, cam_project, (FIX / sc["image"]).read_bytes(),
                         target=tuple(sc["target_uv01"]),
                         path={"preset": "slide", "lateral_ratio": 0.04, "sketch_points": []})
    guide = run_kind(ctx, db_factory, cam_project, "camera.guide", {}, 2, uses_gpu=False)
    r = guide.result_json
    assert r["frame_count"] == 120 and r["hole_ratio_max"] > 0
    geo = decode_frames(storage._path(load(db_factory, Asset, r["geometry_video_asset_id"]).object_key))
    f0 = next(geo)
    assert any(not np.array_equal(f0, f) for f in geo)  # the camera moves
    cin = run_kind(ctx, db_factory, cam_project, "camera.cinematic", {"guide_job_id": str(guide.id)}, 2)
    assert cin.result_json["qa"]["status"] in ("ok", "check", "unknown")
    assert_output_contract(storage._path(load(db_factory, Asset,
                                              cin.result_json["cinematic_video_asset_id"]).object_key))
    for fmt in ("mp4", "path_json", "recipe_zip"):
        run_kind(ctx, db_factory, cam_project, "camera.export", {"source_job_id": str(cin.id), "format": fmt}, 2,
                 uses_gpu=False, profile="cpu")
    print("timings", TIMINGS, "guide", {k: r[k] for k in ("hole_ratio_max", "target_visibility_min",
                                                           "framing_rms_rel", "warnings")},
          "qa", cin.result_json["qa"])
