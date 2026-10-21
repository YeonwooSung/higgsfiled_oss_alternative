"""Pocket VFX worker executors: upload -> inspect -> vfx.analyze -> vfx.track -> vfx.render (Quick)
(mock profile, SQLite, temp storage, committed synthetic fixtures)."""

import importlib
import io
import json
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from oc_api.db import utcnow
from oc_api.models import Asset, Job, JobEvent, JobStage, Project, ProjectRevision
from oc_api.models import Session as SessionRow
from oc_api.pocket_vfx.config import PocketVfxConfig, default_config
from oc_api.pocket_vfx.jobs import analysis_matches, config_track_key
from oc_worker.comfy.mock import MockRunner
from oc_worker.main import run_once
from open_creative.media.video import decode_frames, probe
from open_creative.pocket_vfx.timeline import frame_index
from PIL import Image
from sqlalchemy import func, select
from worker_helpers import add_job, load, make_ctx, set_revision_config

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "pocket-vfx"
vfx_render_mod = importlib.import_module("oc_worker.executors.vfx_render")  # the package re-exports the function
GT = json.loads((FIX / "tracks.json").read_text())["hand"]


class Crash(BaseException):
    """Simulates the worker process dying."""


@pytest.fixture
def pv(db_factory):
    with db_factory() as db:
        s = SessionRow(expires_at=utcnow() + timedelta(days=1))
        db.add(s)
        db.flush()
        p = Project(session_id=s.id, app_slug="pocket-vfx", title="t", current_revision=1)
        db.add(p)
        db.flush()
        db.add(ProjectRevision(project_id=p.id, revision=1,
                               config_json=default_config("mock_v1").model_dump(mode="json"),
                               config_schema_version=1))
        db.commit()
        db.refresh(p)
        db.expunge(p)
        return p


def upload_clip(db_factory, storage, project, name: str) -> uuid.UUID:
    data = (FIX / name).read_bytes()
    aid = uuid.uuid4()
    key = f"{project.session_id}/{project.id}/{aid}/clip-original.mp4"
    storage.put_bytes(key, data, "video/mp4")
    with db_factory() as db:
        db.add(Asset(id=aid, project_id=project.id, role="clip", state="uploaded", object_key=key, mime="video/mp4",
                     bytes=len(data), sha256=json.loads((FIX / "manifest.json").read_text())["files"][name]["sha256"]))
        db.commit()
    return aid


def run_kind(ctx, db_factory, project, kind, options, revision, expect="succeeded", profile="mock_v1"):
    jid = add_job(db_factory, project, kind=kind, options=options, uses_gpu=False, revision=revision,
                  profile=profile, reserved=0)
    assert run_once(ctx)
    job = load(db_factory, Job, jid)
    assert job.state == expect, (job.error_code, job.error_message)
    return job


class Proj:
    """The project's config history as the client would PATCH it (one new revision per change)."""

    def __init__(self, ctx, db_factory, storage, project):
        self.ctx, self.db_factory, self.storage, self.project = ctx, db_factory, storage, project
        self.rev, self.cfg = 1, default_config("mock_v1").model_dump(mode="json")

    def revise(self, **changes) -> int:
        for path, value in changes.items():
            node, *keys = path.split("__")
            if keys:
                self.cfg[node][keys[0]] = value
            else:
                self.cfg[node] = value
        self.rev += 1
        set_revision_config(self.db_factory, self.project, PocketVfxConfig.model_validate(self.cfg)
                            .model_dump(mode="json"), revision=self.rev)
        return self.rev

    def run(self, kind, options=None, expect="succeeded"):
        return run_kind(self.ctx, self.db_factory, self.project, kind, options or {}, self.rev, expect)

    def prepare(self, name: str, **changes):
        """Upload + inspect the clip, set it (+ trim/aspect changes), analyze, apply the work clip."""
        aid = upload_clip(self.db_factory, self.storage, self.project, name)
        run_kind(self.ctx, self.db_factory, self.project, "asset.inspect", {"asset_id": str(aid)}, self.rev,
                 profile="cpu")
        self.revise(video_asset_id=str(aid), **changes)
        analyze = self.run("vfx.analyze")
        self.revise(work_asset_id=analyze.result_json["work_asset_id"])
        return analyze

    def asset(self, aid) -> Asset:
        return load(self.db_factory, Asset, aid)

    def path(self, aid) -> Path:
        return self.storage._path(self.asset(aid).object_key)

    def json(self, aid) -> dict:
        return json.loads(self.storage.get_bytes(self.asset(aid).object_key))


@pytest.fixture
def proj(ctx, db_factory, storage, pv):
    return Proj(ctx, db_factory, storage, pv)


def _gt_uv(f):
    return {"frame": f, "x": GT[f]["u"], "y": GT[f]["v"]}


def _contract(path, w, h, n):
    info = probe(path)
    assert {k: info[k] for k in ("width", "height", "frame_count", "fps", "codec", "pix_fmt")} == \
        {"width": w, "height": h, "frame_count": n, "fps": 24, "codec": "h264", "pix_fmt": "yuv420p"}


# ---- analyze ---------------------------------------------------------------------------------------------


def test_analyze_stores_work_clip_proxy_filmstrip_and_motion(proj):
    analyze = proj.prepare("stomp_landscape.mp4")
    r = analyze.result_json
    assert r["frame_count"] == 120 and r["has_audio"] is False and r["audio_asset_id"] is None
    assert r["camera_motion"]["status"] == "fixed"
    assert len(r["filmstrip_frames"]) == 12 and r["filmstrip_frames"][0] == 0 and r["filmstrip_frames"][-1] == 119
    work = proj.asset(r["work_asset_id"])
    assert work.role == "work_clip" and (work.width, work.height, work.frame_count) == (1280, 720, 120)
    m = work.meta_json
    assert m["trim"] == {"source_start_ms": 0, "duration_ms": 5000} and m["aspect"] == "16:9"
    assert m["source_asset_id"] == proj.cfg["video_asset_id"] and m["mock"] is False
    assert len(m["pts_map"]) == 120 and m["pts_map"][1] == pytest.approx(1000 / 24, abs=1e-3)
    assert work.transform_json["scale"] == 1.0 and work.transform_json["offset_x"] == 0.0
    assert analysis_matches(work, proj.cfg)  # the API accepts it as the analysis of this config
    proxy = proj.asset(r["proxy_asset_id"])
    assert proxy.role == "work_proxy" and (proxy.width, proxy.height, proxy.frame_count) == (640, 360, 120)
    assert proxy.source_asset_id == work.id
    strip, first = proj.asset(r["filmstrip_asset_id"]), proj.asset(r["first_frame_asset_id"])
    assert strip.mime == first.mime == "image/png" and (first.width, first.height) == (1280, 720)
    assert proj.json(r["motion_asset_id"])["status"] == "fixed"
    with proj.db_factory() as db:
        stages = {s.stage_name: s.state for s in db.scalars(select(JobStage).where(JobStage.job_id == analyze.id))}
        visible = {e.stage for e in db.scalars(select(JobEvent).where(JobEvent.job_id == analyze.id,
                                                                     JobEvent.type == "state"))}
    assert stages == dict.fromkeys(("normalize", "proxy", "filmstrip", "audio", "motion"), "succeeded")
    assert {"normalize", "proxy", "filmstrip", "audio", "motion"} <= visible  # the visible stage advances


def test_analyze_trims_and_extracts_audio(proj):
    r = proj.prepare("with_audio.mp4", trim={"source_start_ms": 1000, "duration_ms": 3000}).result_json
    assert r["has_audio"] is True and r["frame_count"] == 72
    work = proj.asset(r["work_asset_id"])
    assert work.meta_json["trim"] == {"source_start_ms": 1000, "duration_ms": 3000}
    assert work.meta_json["pts_map"][0] == pytest.approx(1000, abs=1)  # the trim start, applied once
    audio = proj.asset(r["audio_asset_id"])
    assert audio.role == "source_audio" and audio.mime == "audio/mp4" and audio.source_asset_id == work.id


# ---- render ----------------------------------------------------------------------------------------------


def _luma_gain(render_path, work_path) -> np.ndarray:
    """Per-frame summed luminance the composite added over the work clip (decoded)."""
    gains = []
    for out, src in zip(decode_frames(render_path), decode_frames(work_path), strict=True):
        d = out.astype(np.int32).sum(axis=2) - src.astype(np.int32).sum(axis=2)
        gains.append(float(np.clip(d, 0, None).sum()))
    return np.array(gains)


def test_quick_render_with_static_anchor(proj):
    analyze = proj.prepare("stomp_landscape.mp4")
    proj.revise(anchor__follow=False, effect__layer_order="in_front")
    render = proj.run("vfx.render", {"track_job_id": None, "render_profile": "quick"})
    r = render.result_json
    assert r["render_profile"] == "quick" and r["plate_source"] == "procedural" and r["occlusion_qa"] is None
    assert r["frame_count"] == 120 and r["mock"] is False and r["has_audio"] is False
    assert r["roi_qa"]["status"] == "pass" and r["roi_qa"]["frames"] == 120 and r["roi_qa"]["max_err"] <= 1
    assert r["tracking_warnings"] == []  # static anchor on a fixed camera
    video = proj.asset(r["render_video_asset_id"])
    assert video.role == "render_video" and video.meta_json["label"] == "Quick"
    _contract(proj.path(video.id), 1280, 720, 120)
    peak = frame_index(proj.cfg["effect"]["peak_ms"])
    gains = _luma_gain(proj.path(video.id), proj.path(analyze.result_json["work_asset_id"]))
    assert abs(int(np.argmax(gains)) - peak) <= 1
    start = frame_index(proj.cfg["effect"]["start_ms"])
    stats = proj.json(r["roi_qa_asset_id"])["frame_stats"]
    assert all(stats["roi_px"][i] == 0 and stats["changed_px"][i] == 0 for i in range(start))  # before the effect
    assert stats["roi_px"][peak] > 0
    x0, y0, x1, y1 = stats["roi_bbox"][peak]  # centred on the static anchor (uv x canvas)
    assert x0 < 0.5 * 1280 < x1 and y0 < 0.7 * 720 < y1
    cover = proj.asset(r["cover_asset_id"])
    assert (cover.width, cover.height) == (1280, 720) and cover.meta_json["frame"] == peak


def test_rotated_portrait_renders_portrait(proj):
    analyze = proj.prepare("rotated_portrait.mp4", trim={"source_start_ms": 0, "duration_ms": 4000},
                           output={"aspect": "9:16", "fps": 24})
    work = proj.asset(analyze.result_json["work_asset_id"])
    assert (work.width, work.height) == (720, 1280) and work.meta_json["aspect"] == "9:16"
    proxy = proj.asset(analyze.result_json["proxy_asset_id"])
    assert (proxy.width, proxy.height) == (360, 640)
    proj.revise(anchor__follow=False, effect__layer_order="in_front")
    r = proj.run("vfx.render", {"track_job_id": None, "render_profile": "quick"}).result_json
    assert r["roi_qa"]["status"] == "pass" and (r["width"], r["height"]) == (720, 1280)
    _contract(proj.path(r["render_video_asset_id"]), 720, 1280, 96)


# ---- track + render ----------------------------------------------------------------------------------------


def test_occluded_track_hides_effect_and_retrack_reuses_segments(proj):
    proj.prepare("occluded_hand.mp4")
    proj.revise(anchor={**proj.cfg["anchor"], **_gt_uv(20), "follow": True}, effect__layer_order="in_front")
    first = proj.run("vfx.track").result_json
    assert first["invalid_intervals"] == [[50, 119]]  # lost behind the occluder, never filled
    assert first["segments_total"] == 2 and first["segments_reused"] == 0

    proj.revise(corrections=[_gt_uv(63)])
    track = proj.run("vfx.track")
    r = track.result_json
    assert r["invalid_intervals"] == [[50, 62]]
    assert r["segments_total"] == 3 and r["segments_reused"] == 1  # the backward run before the anchor
    assert r["valid_fraction"] == pytest.approx(107 / 120, abs=1e-4) and r["peak_valid"] is True
    with proj.db_factory() as db:
        clip = db.get(Asset, uuid.UUID(proj.cfg["video_asset_id"]))
        assert r["track_key"] == config_track_key(PocketVfxConfig.model_validate(proj.cfg), clip)
    doc = proj.json(r["track_asset_id"])
    assert doc["invalid_intervals"] == [[50, 62]] and doc["points"][55]["x"] is None
    err = [np.hypot(doc["points"][f]["x"] * 1280 - GT[f]["x"], doc["points"][f]["y"] * 720 - GT[f]["y"])
           for f in range(120) if doc["points"][f]["valid"]]
    assert np.median(err) <= 5.0

    again = proj.run("vfx.track").result_json  # unchanged config: everything comes from the cache
    assert again["segments_reused"] == 3 and again["invalid_intervals"] == [[50, 62]]
    assert proj.json(again["track_asset_id"])["points"] == doc["points"]

    out = proj.run("vfx.render", {"track_job_id": str(track.id), "render_profile": "quick"}).result_json
    assert out["roi_qa"]["status"] == "pass" and out["track_job_id"] == str(track.id)
    assert {"code": "track_lost", "frames": [[50, 62]], "policy": "hide"} in out["tracking_warnings"]
    stats = proj.json(out["roi_qa_asset_id"])["frame_stats"]
    for f in range(50, 63):  # Ruling R4: hidden -> the composite is the source frame, the ROI is empty
        assert stats["roi_px"][f] == 0 and stats["changed_px"][f] == 0 and stats["roi_bbox"][f] is None
    assert stats["roi_px"][49] > 0 and stats["roi_px"][63] > 0
    for f in (45, 70):  # the effect follows the tracked hand
        x0, y0, x1, y1 = stats["roi_bbox"][f]
        assert x0 < GT[f]["x"] < x1 and y0 < GT[f]["y"] < y1


def test_invalid_policy_hide_and_freeze_anchor_resolution():
    cfg = SimpleNamespace(anchor=SimpleNamespace(x=0.5, y=0.5))
    valid = {2: (0.25, 0.5), 3: (0.5, 0.25), 6: (0.75, 0.75)}
    track = {"points": [{"frame": f, "valid": f in valid, "x": valid.get(f, (None,))[0],
                         "y": valid.get(f, (None, None))[1]} for f in range(8)]}
    hide = vfx_render_mod._anchors(track, cfg, 100, 200, 8, "hide")
    assert hide == [None, None, [25, 100], [50, 50], None, None, [75, 150], None]
    freeze = vfx_render_mod._anchors(track, cfg, 100, 200, 8, "freeze")
    # frames before the first valid point stay hidden; later gaps hold the last valid position
    assert freeze == [None, None, [25, 100], [50, 50], [50, 50], [50, 50], [75, 150], [75, 150]]
    assert vfx_render_mod._anchors(None, cfg, 100, 200, 3, "hide") == [[50, 100]] * 3  # follow=false


def test_render_fails_early_without_required_masks_or_plate(proj):
    proj.prepare("stomp_landscape.mp4")
    proj.revise(anchor__follow=False, effect__layer_order="in_front")
    job = proj.run("vfx.render", {"track_job_id": None, "render_profile": "generated"}, expect="failed")
    assert job.error_code == "plate_required"
    proj.revise(effect={**proj.cfg["effect"], "preset_id": "blink_out_v1"})
    job = proj.run("vfx.render", {"track_job_id": None, "render_profile": "quick"}, expect="failed")
    assert job.error_code == "mask_required"
    assert not job.output_asset_ids


def test_outside_roi_change_fails_the_render(proj, monkeypatch):
    proj.prepare("stomp_landscape.mp4")
    proj.revise(anchor__follow=False, effect__layer_order="in_front")
    real, calls = vfx_render_mod.composite_frame, {"n": 0}

    def leaky(inp, order):
        out, roi = real(inp, order)
        calls["n"] += 1
        if calls["n"] == 6:  # one pixel outside the ROI moves by 3/255
            assert not roi[0, 0]
            out = out.copy()
            out[0, 0, 0] = out[0, 0, 0] + 3 if out[0, 0, 0] < 250 else out[0, 0, 0] - 3
        return out, roi

    monkeypatch.setattr(vfx_render_mod, "composite_frame", leaky)
    job = proj.run("vfx.render", {"track_job_id": None, "render_profile": "quick"}, expect="failed")
    assert job.error_code == "roi_integrity" and "frame 5" in job.error_message
    with proj.db_factory() as db:
        roles = set(db.scalars(select(Asset.role).where(Asset.job_id == job.id)))
    assert roles == {"render_layers"}  # no video delivered


def test_follow_render_needs_a_fresh_track(proj):
    proj.prepare("stomp_landscape.mp4")
    track = proj.run("vfx.track")
    proj.revise(corrections=[_gt_uv(80)])
    job = proj.run("vfx.render", {"track_job_id": str(track.id), "render_profile": "quick"}, expect="failed")
    assert job.error_code == "track_stale"


# ---- restart -----------------------------------------------------------------------------------------------


def test_render_resumes_after_restart_from_stage_outputs(settings, db_factory, storage, mock_root, proj,
                                                         monkeypatch):
    proj.prepare("stomp_landscape.mp4")
    proj.revise(anchor__follow=False, effect__layer_order="in_front")
    calls = {"n": 0}
    real = vfx_render_mod.composite_frame

    def crashing(inp, order):
        calls["n"] += 1
        if calls["n"] == 30:
            raise Crash()
        return real(inp, order)

    monkeypatch.setattr(vfx_render_mod, "composite_frame", crashing)
    jid = add_job(db_factory, proj.project, kind="vfx.render", options={"track_job_id": None,
                                                                        "render_profile": "quick"},
                  uses_gpu=False, revision=proj.rev, reserved=0)
    with pytest.raises(Crash):
        run_once(make_ctx(settings, db_factory, storage, MockRunner(mock_root), worker_id="w1"))
    with db_factory() as db:
        stages = {s.stage_name: s for s in db.scalars(select(JobStage).where(JobStage.job_id == jid))}
        assert stages["layers"].state == "succeeded" and stages["composite"].state != "succeeded"
        layers_out = list(stages["layers"].output_asset_ids)
        db.get(Job, jid).lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    assert run_once(make_ctx(settings, db_factory, storage, MockRunner(mock_root), worker_id="w2"))
    job = load(db_factory, Job, jid)
    assert job.state == "succeeded", (job.error_code, job.error_message)
    assert job.result_json["layers_asset_id"] == layers_out[0]  # the finished stage was not re-run
    with db_factory() as db:
        counts = dict(db.execute(select(Asset.role, func.count()).where(Asset.job_id == jid)
                                 .group_by(Asset.role)).all())
    assert counts == {"render_layers": 1, "render_video": 1, "cover": 1, "roi_qa": 1}
    _contract(proj.path(job.result_json["render_video_asset_id"]), 1280, 720, 120)
    assert Image.open(io.BytesIO(proj.storage.get_bytes(proj.asset(job.result_json["cover_asset_id"]).object_key)))


# ---- subject masks (vfx.track ``masks`` stage) and occlusion ------------------------------------------------------

STOMP_MASKS = FIX / "stomp_masks.zip"
BODY_PROMPT = [{"frame": 43, "points": [{"x": 0.44, "y": 0.45, "label": 1}]}]


def _gt_stomp(f: int) -> np.ndarray:
    import zipfile

    with zipfile.ZipFile(STOMP_MASKS) as z:
        return np.asarray(Image.open(io.BytesIO(z.read(f"mask_{f:04d}.png"))).convert("L")) > 127


def _iou(a, b) -> float:
    return float((a & b).sum() / max((a | b).sum(), 1))


def _masks(proj, result):
    from open_creative.pocket_vfx.masks import read_masks_zip

    m = result["masks"]
    return read_masks_zip(proj.path(m["masks_zip_asset_id"]), expect_frames=result.get("frame_count"),
                          expect_size=tuple(m["size"]))


def test_track_masks_stage_stores_masks_and_preview(proj):
    proj.prepare("stomp_landscape.mp4")
    proj.revise(subject_prompts=BODY_PROMPT)
    track = proj.run("vfx.track")
    m = track.result_json["masks"]
    assert m["source"] == "fixture_gt" and m["mock"] is True and m["reused"] is False and m["size"] == [640, 360]
    assert m["frame_count"] == 120 and isinstance(m["diagnostics"], list)
    zip_asset, preview = proj.asset(m["masks_zip_asset_id"]), proj.asset(m["mask_preview_asset_id"])
    assert zip_asset.role == "masks_zip" and zip_asset.mime == "application/zip"
    assert preview.role == "mask_preview" and (preview.width, preview.height, preview.frame_count) == (640, 360, 120)
    assert {m["masks_zip_asset_id"], m["mask_preview_asset_id"]} <= set(map(str, track.output_asset_ids))
    seq = _masks(proj, m | {"masks": m}) if False else _masks(proj, {"masks": m, "frame_count": 120})
    from open_creative.pocket_vfx.masks import upscale_mask

    scores = [_iou(upscale_mask(seq.masks[f], 1280, 720) >= 0.5, _gt_stomp(f)) for f in range(0, 120, 17)]
    assert min(scores) >= 0.95, scores
    with proj.db_factory() as db:
        st = db.scalar(select(JobStage).where(JobStage.job_id == track.id, JobStage.stage_name == "masks"))
        assert st.state == "succeeded" and st.submission_key is not None


def test_correction_only_retrack_reuses_masks_without_a_submission(proj):
    runner = proj.ctx.runner_for("mock_v1")
    proj.prepare("stomp_landscape.mp4")
    proj.revise(subject_prompts=BODY_PROMPT)
    first = proj.run("vfx.track").result_json["masks"]
    submitted = runner.submit_count
    proj.revise(corrections=[_gt_uv(80)])
    again = proj.run("vfx.track")
    m = again.result_json["masks"]
    assert runner.submit_count == submitted  # no new provider submission
    assert m["reused"] is True and m["masks_zip_asset_id"] == first["masks_zip_asset_id"]
    assert again.result_json["track_key"] != config_track_key(PocketVfxConfig.model_validate(
        {**proj.cfg, "corrections": []}), proj.asset(proj.cfg["video_asset_id"]))
    with proj.db_factory() as db:
        st = db.scalar(select(JobStage).where(JobStage.job_id == again.id, JobStage.stage_name == "masks"))
        assert st.state == "succeeded" and st.submission_key is None and st.provider_job_id is None
    proj.revise(subject_prompts=[{"frame": 40, "points": BODY_PROMPT[0]["points"]}])  # new prompt: new masks
    assert proj.run("vfx.track").result_json["masks"]["reused"] is False
    assert runner.submit_count == submitted + 1


def test_track_without_subject_skips_masks_and_render_reports_mask_required(proj):
    proj.prepare("stomp_landscape.mp4")
    proj.revise(anchor__follow=False)
    track = proj.run("vfx.track")
    assert track.result_json["masks"] is None
    with proj.db_factory() as db:
        assert {s.stage_name for s in db.scalars(select(JobStage).where(JobStage.job_id == track.id))} == {"track"}
    job = proj.run("vfx.render", {"track_job_id": str(track.id), "render_profile": "quick"}, expect="failed")
    assert job.error_code == "mask_required"
    job = proj.run("vfx.render", {"track_job_id": None, "render_profile": "quick"}, expect="failed")
    assert job.error_code == "track_required"


def test_behind_subject_render_never_covers_the_subject_core(proj):
    proj.prepare("stomp_landscape.mp4")
    proj.revise(anchor__follow=False, subject_prompts=BODY_PROMPT)  # fixed ground anchor next to the stomping foot
    track = proj.run("vfx.track")
    r = proj.run("vfx.render", {"track_job_id": str(track.id), "render_profile": "quick"}).result_json
    assert r["occlusion"] is True and r["occlusion_qa"] == 0
    assert r["roi_qa"]["status"] == "pass" and r["roi_qa"]["max_err"] <= 1
    qa = proj.json(r["roi_qa_asset_id"])
    assert qa["occlusion_qa"]["max"] == 0 and qa["occlusion_qa"]["frames"] == 120
    peak = frame_index(proj.cfg["effect"]["peak_ms"])
    # the ring really passes behind the person: its ROI box overlaps the subject on frames after the peak
    overlap = 0
    for f in range(peak, peak + 20):
        box = qa["frame_stats"]["roi_bbox"][f]
        if box:
            x0, y0, x1, y1 = box
            overlap += int(_gt_stomp(f)[y0:y1, x0:x1].sum())
    assert overlap > 1000
    layers = proj.json(r["layers_asset_id"])
    assert layers["subject_masks"]["masks_zip_asset_id"] == track.result_json["masks"]["masks_zip_asset_id"]


def test_energy_aura_render_draws_the_contour_band_behind_the_subject(proj):
    proj.prepare("stomp_landscape.mp4")
    eff = dict(proj.cfg["effect"], preset_id="energy_aura_v1", layer_order="behind_subject", palette="violet")
    proj.revise(effect=eff, anchor__follow=False, subject_prompts=BODY_PROMPT)
    track = proj.run("vfx.track")
    r = proj.run("vfx.render", {"track_job_id": str(track.id), "render_profile": "quick"}).result_json
    assert r["occlusion"] is True and r["occlusion_qa"] == 0 and r["roi_qa"]["status"] == "pass"
    qa = proj.json(r["roi_qa_asset_id"])
    peak = frame_index(proj.cfg["effect"]["peak_ms"])
    x0, y0, x1, _ = qa["frame_stats"]["roi_bbox"][peak]
    sx0, sy0, sx1, _ = _box(_gt_stomp(peak))
    # the effect ROI is the band around the whole silhouette (union with the anchor glow), not only the anchor
    assert x0 <= sx0 and x1 >= sx1 and y0 <= sy0


def _box(m):
    ys, xs = np.nonzero(m)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _put_mask_edit(proj, png: Image.Image) -> str:
    buf = io.BytesIO()
    png.save(buf, format="PNG")
    aid = uuid.uuid4()
    key = f"{proj.project.session_id}/{proj.project.id}/{aid}/mask_edit.png"
    proj.storage.put_bytes(key, buf.getvalue(), "image/png")
    with proj.db_factory() as db:
        db.add(Asset(id=aid, project_id=proj.project.id, role="mask_edit", state="ready", object_key=key,
                     mime="image/png", bytes=len(buf.getvalue()), sha256="0" * 64, width=png.width,
                     height=png.height))
        db.commit()
    return str(aid)


def test_mask_edit_replaces_its_frame(proj):
    proj.prepare("stomp_landscape.mp4")
    edit = np.zeros((720, 1280), np.uint8)  # painted at the work-clip size; any size is accepted
    edit[300:500, 500:700] = 255
    aid = _put_mask_edit(proj, Image.fromarray(edit, "L"))
    proj.revise(subject_prompts=BODY_PROMPT, mask_edits=[{"frame": 50, "asset_id": aid}])
    m = proj.run("vfx.track").result_json["masks"]
    seq = _masks(proj, {"masks": m, "frame_count": 120})
    assert np.array_equal(seq.masks[50] > 127, edit[::2, ::2] > 127)
    assert proj.asset(m["masks_zip_asset_id"]).meta_json["mask_edit_frames"] == [50]


def _load_make_fixtures():
    import importlib.util

    spec = importlib.util.spec_from_file_location("pv_make_fixtures", FIX / "make_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_portrait_masks_align_with_gt_at_output_size(proj):
    """Rotated 30 fps portrait source -> 24 fps 9:16 work clip -> 360x640 proxy masks (the mock serves the fixture's
    display-orientation GT through the PTS map) -> upscaled to 720x1280: aligned with the scene, never transposed or
    shifted in time."""
    mf = _load_make_fixtures()
    analyze = proj.prepare("rotated_portrait.mp4", trim={"source_start_ms": 0, "duration_ms": 2000},
                           output={"aspect": "9:16", "fps": 24})
    work = proj.asset(analyze.result_json["work_asset_id"])
    dw, dh = 720, 1280  # display size of the fixture == the 9:16 work clip (identity cover-fit)
    assert (work.width, work.height) == (dw, dh) and work.transform_json["scale"] == 1.0
    f0 = 24
    p = mf.pose(work.meta_json["pts_map"][f0] / 1000, dw, dh)
    proj.revise(anchor__follow=False, subject_prompts=[{"frame": f0, "points": [
        {"x": p["cx"] / dw, "y": (p["hip"] - 0.12 * p["u"]) / dh, "label": 1}]}])
    m = proj.run("vfx.track").result_json["masks"]
    assert m["source"] == "fixture_gt" and m["size"] == [360, 640]  # portrait proxy, never transposed
    seq = _masks(proj, {"masks": m, "frame_count": 48})
    from open_creative.pocket_vfx.masks import upscale_mask

    bg = mf.background(dw, dh, 11)
    scores = {}
    for f in (0, 12, f0, 36, 47):  # work frame f shows the scene at its source PTS (30 -> 24 fps)
        gt = mf.draw(bg, work.meta_json["pts_map"][f] / 1000)[1].astype(bool)
        up = upscale_mask(seq.masks[f], dw, dh) >= 0.5
        assert up.shape == gt.shape
        scores[f] = _iou(up, gt)
    assert min(scores.values()) >= 0.9, scores
