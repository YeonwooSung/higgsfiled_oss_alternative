"""vfx.generate_plate (mock vfx_plate_v1), renders with bundled / generated plates, and reuse (spec §7, §8, §14)."""

import importlib
import json

import numpy as np
import pytest
from oc_api.models import Asset, Job, JobStage
from open_creative.media.video import decode_frames, probe
from open_creative.pocket_vfx.timeline import frame_index
from sqlalchemy import func, select
from test_vfx_executors import (  # noqa: F401 - pv is the project fixture
    Proj,
    _contract,
    _luma_gain,
    pv,
)

mock_mod = importlib.import_module("oc_worker.comfy.mock")  # (not a from-import: root and worker isort disagree)
MockRunner = mock_mod.MockRunner
QUICK = {"track_job_id": None, "render_profile": "quick"}


@pytest.fixture
def proj(ctx, db_factory, storage, pv):  # noqa: F811
    p = Proj(ctx, db_factory, storage, pv)
    p.prepare("stomp_landscape.mp4")
    p.revise(anchor__follow=False, effect__layer_order="in_front")
    return p


def use_plate(proj, plate_job) -> int:
    return proj.revise(plate={**proj.cfg["plate"], "mode": "generated",
                              "asset_id": plate_job.result_json["plate_asset_id"]})


def generated(track_job=None):
    return {"track_job_id": str(track_job.id) if track_job else None, "render_profile": "generated"}


def stage_counts(proj, job_ids) -> dict:
    with proj.db_factory() as db:
        return {str(j): db.scalar(select(func.count()).select_from(JobStage).where(JobStage.job_id == j))
                for j in job_ids}


def job_count(proj, kind) -> int:
    with proj.db_factory() as db:
        return db.scalar(select(func.count()).select_from(Job).where(Job.project_id == proj.project.id,
                                                                     Job.kind == kind))


# ---- mock provider ------------------------------------------------------------------------------------------


def test_mock_plate_is_deterministic_and_checks_the_ltx_shape(tmp_path):
    r = MockRunner(tmp_path / "m")
    b = {"prompt": "isolated bright violet electrical energy wisps", "negative": "people", "seed": 5, "width": 448,
         "height": 768, "frames": 17}
    outs = []
    for key in ("a", "b"):
        jid = r.submit("vfx_plate_v1", b, key)
        assert r.inspect(jid)["state"] == "succeeded"
        files = r.collect(jid, tmp_path / key, "vfx_plate_v1")
        video = next(p for p in files if p.suffix == ".mp4")
        side = json.loads(next(p for p in files if p.suffix == ".json").read_text())
        assert side["mock"] is True and side["label"] == "Mock" and side["seed"] == 5
        info = probe(video)
        assert (info["width"], info["height"], info["frame_count"]) == (448, 768, 17)
        outs.append(list(decode_frames(video)))
    assert all(np.array_equal(x, y) for x, y in zip(*outs, strict=True))
    lit = outs[0][8].sum(axis=2) > 90
    assert lit.any() and (outs[0][8][lit][:, 2] >= outs[0][8][lit][:, 1]).all()  # violet: blue over green
    jid = r.submit("vfx_plate_v1", {**b, "frames": 16}, "c")  # not 8k+1
    assert r.inspect(jid)["state"] == "failed" and "8k+1" in r.inspect(jid)["error"]


# ---- generate_plate -----------------------------------------------------------------------------------------


def test_generate_plate_stores_raw_plate_matte_preview_and_qa(proj):
    job = proj.run("vfx.generate_plate", {"seed": 7})
    r = job.result_json
    assert (r["frames"], r["width"], r["height"]) == (49, 768, 448)  # window 34..82 = 49 frames -> 8k+1 = 49
    assert r["seed"] == 7 and r["palette"] == "amber" and r["preset_id"] == "impact_wave_v1" and r["mock"] is True
    assert r["matte_qa"]["status"] == "ok" and r["matte_qa"]["reasons"] == []
    raw = proj.asset(r["plate_asset_id"])
    assert raw.role == "raw_plate" and raw.job_id == job.id and (raw.width, raw.height, raw.frame_count) == (768, 448, 49)
    assert {k: raw.meta_json[k] for k in ("preset_id", "palette", "seed", "frames", "mock")} == {
        "preset_id": "impact_wave_v1", "palette": "amber", "seed": 7, "frames": 49, "mock": True}
    assert "amber" in raw.meta_json["prompt"] and "{palette}" not in raw.meta_json["prompt"]
    preview = proj.asset(r["matte_preview_asset_id"])
    assert preview.role == "plate_matte_preview" and preview.source_asset_id == raw.id
    _contract(proj.path(preview.id), 768, 448, 49)
    frame = next(decode_frames(proj.path(preview.id)))
    assert (frame[..., 0] == frame[..., 2]).all()  # alpha as gray
    qa = proj.json(r["matte_qa_asset_id"])
    assert proj.asset(r["matte_qa_asset_id"]).role == "matte_qa" and qa["status"] == "ok"
    assert qa["matte"] == {"threshold": 0.06, "knee": 0.04} and qa["frames"] == 49
    with proj.db_factory() as db:
        stages = sorted(db.scalars(select(JobStage.stage_name).where(JobStage.job_id == job.id)))
        plate_stage = db.scalar(select(JobStage).where(JobStage.job_id == job.id, JobStage.stage_name == "plate"))
    assert stages == ["matte", "plate"] and plate_stage.submission_key is not None
    # the seed defaults to config.plate.seed
    assert proj.run("vfx.generate_plate", {}).result_json["seed"] == 12345


def test_generate_plate_length_covers_the_window(proj):
    proj.revise(effect={**proj.cfg["effect"], "start_ms": 1400, "peak_ms": 1500, "end_ms": 1800})  # 34..43 = 10
    assert proj.run("vfx.generate_plate").result_json["frames"] == 17


# ---- render with plates -------------------------------------------------------------------------------------


def test_render_with_generated_plate(proj):
    quick = proj.run("vfx.render", QUICK).result_json
    plate = proj.run("vfx.generate_plate", {"seed": 3})
    use_plate(proj, plate)
    job = proj.run("vfx.render", generated())
    r = job.result_json
    assert r["render_profile"] == "generated" and r["plate_source"] == "generated"
    assert r["plate_asset_id"] == plate.result_json["plate_asset_id"] and r["bundled_plate_id"] is None
    assert r["label"] == "Generated plate" and r["mock"] is True
    assert r["badge"] == {"render_profile": "generated", "label": "Generated plate", "label_ko": "생성 plate",
                          "plate_source": "generated", "mock": True}
    assert r["roi_qa"]["status"] == "pass" and r["roi_qa"]["max_err"] <= 1
    video = proj.asset(r["render_video_asset_id"])
    assert video.meta_json["plate_source"] == "generated" and video.meta_json["label"] == "Generated plate"
    _contract(proj.path(video.id), 1280, 720, 120)
    layers = proj.json(r["layers_asset_id"])
    assert layers["plate"]["source"] == "generated" and layers["plate"]["indices"] == list(range(49))
    stats = proj.json(r["roi_qa_asset_id"])["frame_stats"]
    start, end = frame_index(proj.cfg["effect"]["start_ms"]), frame_index(proj.cfg["effect"]["end_ms"])
    assert all(stats["changed_px"][i] == 0 for i in range(start)) and all(
        stats["changed_px"][i] == 0 for i in range(end + 1, 120))  # the plate lives in the effect window only
    # the plate is drawn: same effect, another plate -> other pixels, and above the anchor (behind the ring)
    qstats = proj.json(quick["roi_qa_asset_id"])["frame_stats"]
    peak = frame_index(proj.cfg["effect"]["peak_ms"])
    assert stats["roi_bbox"][peak + 10] != qstats["roi_bbox"][peak + 10]
    assert stats["roi_bbox"][peak + 10][1] < 0.7 * 720 - 0.2 * 720


def test_quick_uses_the_bundled_plate_and_loops_it(proj):
    proj.revise(plate={**proj.cfg["plate"], "asset_id": None, "bundled_id": "dust_burst_v1"},
                effect={**proj.cfg["effect"], "start_ms": 0, "peak_ms": 400, "end_ms": 5000})  # 0..119 = 120 frames
    r = proj.run("vfx.render", QUICK).result_json
    assert r["plate_source"] == "bundled" and r["bundled_plate_id"] == "dust_burst_v1" and r["mock"] is False
    plate = proj.json(r["layers_asset_id"])["plate"]
    assert plate["frames"] == 49 and plate["seam_ok"] is True and plate["size"] == [768, 448]
    assert plate["indices"] == [k % 49 for k in range(120)]  # 120 > 2 x 49: loop (seam QA passed)
    assert plate["tint"] == "amber" and r["roi_qa"]["status"] == "pass"


def test_bundled_plate_follows_the_output_orientation(ctx, db_factory, storage, pv):  # noqa: F811
    p = Proj(ctx, db_factory, storage, pv)
    p.prepare("rotated_portrait.mp4", trim={"source_start_ms": 0, "duration_ms": 4000},
              output={"aspect": "9:16", "fps": 24})
    p.revise(anchor__follow=False, effect__layer_order="in_front")
    r = p.run("vfx.render", QUICK).result_json
    plate = p.json(r["layers_asset_id"])["plate"]
    assert plate["portrait"] is True and plate["size"] == [448, 768] and r["roi_qa"]["status"] == "pass"
    assert p.run("vfx.generate_plate").result_json["width"] == 448


def test_generated_plate_checks_in_the_worker(proj):
    job = proj.run("vfx.render", generated(), expect="failed")
    assert job.error_code == "plate_required"
    plate = proj.run("vfx.generate_plate")
    use_plate(proj, plate)
    proj.revise(effect={**proj.cfg["effect"], "preset_id": "energy_aura_v1"})
    job = proj.run("vfx.render", generated(), expect="failed")
    assert job.error_code == "plate_mismatch" and not job.output_asset_ids
    # a generated plate never loops: 49 frames cannot cover a 120-frame window
    proj.revise(effect={**proj.cfg["effect"], "preset_id": "impact_wave_v1", "start_ms": 0, "peak_ms": 400,
                        "end_ms": 5000})
    job = proj.run("vfx.render", generated(), expect="failed")
    assert job.error_code == "plate_length"


def test_121_frame_plate_trims_into_short_windows(proj):
    proj.revise(effect={**proj.cfg["effect"], "start_ms": 0, "peak_ms": 400, "end_ms": 4800})  # 0..115 = 116
    plate = proj.run("vfx.generate_plate")
    assert plate.result_json["frames"] == 121
    use_plate(proj, plate)
    for end_ms, n in ((3375, 48), (1800, 10)):  # 2 s and 0.4 s windows from frame 34
        proj.revise(effect={**proj.cfg["effect"], "start_ms": 1400, "peak_ms": 1500, "end_ms": end_ms})
        r = proj.run("vfx.render", generated()).result_json
        assert r["roi_qa"]["status"] == "pass"
        assert proj.json(r["layers_asset_id"])["plate"]["indices"] == list(range(n))  # Ruling R3: time-trim


# ---- reuse --------------------------------------------------------------------------------------------------


def test_intensity_revision_reuses_track_and_plate(proj):
    proj.revise(anchor__follow=True)
    track = proj.run("vfx.track")
    plate = proj.run("vfx.generate_plate")
    use_plate(proj, plate)
    first = proj.run("vfx.render", generated(track)).result_json
    before = stage_counts(proj, [track.id, plate.id])
    counts = (job_count(proj, "vfx.track"), job_count(proj, "vfx.generate_plate"))
    submitted = proj.ctx.runner_for("mock_v1").submit_count

    proj.revise(effect={**proj.cfg["effect"], "intensity": 0.3, "scale": 1.4, "peak_ms": 1900})
    second = proj.run("vfx.render", generated(track)).result_json
    assert second["track_job_id"] == first["track_job_id"] == str(track.id)
    assert second["plate_asset_id"] == first["plate_asset_id"] == plate.result_json["plate_asset_id"]
    assert stage_counts(proj, [track.id, plate.id]) == before  # no stage of the track/plate jobs re-ran
    assert (job_count(proj, "vfx.track"), job_count(proj, "vfx.generate_plate")) == counts
    assert proj.ctx.runner_for("mock_v1").submit_count == submitted  # the render never calls a provider
    assert second["roi_qa"]["status"] == "pass"
    a = _luma_gain(proj.path(first["render_video_asset_id"]), proj.path(proj.cfg["work_asset_id"]))
    b = _luma_gain(proj.path(second["render_video_asset_id"]), proj.path(proj.cfg["work_asset_id"]))
    assert b.sum() < a.sum()  # lower intensity, same plate


# ---- provider failure ---------------------------------------------------------------------------------------


def test_plate_provider_failure_keeps_the_track_outputs(proj, monkeypatch):
    proj.revise(anchor__follow=True)
    track = proj.run("vfx.track")
    with proj.db_factory() as db:
        assets = {a.id: (a.state, a.sha256) for a in db.scalars(select(Asset).where(Asset.job_id == track.id))}
    before = (track.state, track.result_json, stage_counts(proj, [track.id]))

    def oom(*_a, **_k):
        raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")

    monkeypatch.setattr(mock_mod, "mock_plate_frames", oom)
    failed = proj.run("vfx.generate_plate", expect="failed")
    assert failed.error_code == "provider_failed" and not failed.output_asset_ids
    with proj.db_factory() as db:
        assert db.scalar(select(func.count()).select_from(Asset).where(Asset.job_id == failed.id)) == 0
        after = {a.id: (a.state, a.sha256) for a in db.scalars(select(Asset).where(Asset.job_id == track.id))}
        t = db.get(Job, track.id)
        assert (t.state, t.result_json, stage_counts(proj, [track.id])) == before
    assert after == assets

    monkeypatch.undo()
    plate = proj.run("vfx.generate_plate")  # a new plate job, the track untouched
    use_plate(proj, plate)
    r = proj.run("vfx.render", generated(track)).result_json
    assert r["track_job_id"] == str(track.id) and r["plate_source"] == "generated"
