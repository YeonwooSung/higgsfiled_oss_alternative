import uuid

import pytest
from sqlalchemy import func, select

from helpers import configure, put_asset, set_job_state
from oc_api.models import Asset, Job, JobCost, Project, ProjectRevision
from oc_api.pocket_vfx import jobs as vfx_jobs
from oc_api.pocket_vfx.config import MaskEdit, PocketVfxConfig, SubjectPrompt
from open_creative.pocket_vfx.keys import masks_key, model_rev, track_key
from test_vfx_config import CLIP_SHA, codes, make_ready, put_clip, put_motion, validate, vproject  # noqa: F401


@pytest.fixture
def ready(app, session_client, vproject):  # noqa: F811
    proj, clip, work = make_ready(app, session_client, vproject)
    return vproject, proj["current_revision"]


def post(client, project, kind, options=None, revision=1):
    return client.post(f"/api/v1/projects/{project['id']}/jobs",
                       json={"kind": kind, "revision": revision, "options": options or {}})


def err(r):
    return r.json()["error"]


def job_row(app, job_id):
    with app.state.db() as db:
        j = db.get(Job, uuid.UUID(job_id))
        c = db.scalar(select(JobCost).where(JobCost.job_id == j.id))
        db.expunge_all()
        return j, c


SUBJECT = [{"frame": 43, "points": [{"x": 0.5, "y": 0.4, "label": 1}]}]


def make_track(app, project, revision, state="succeeded", key=None, result=None, masks=True):
    """A vfx.track job for ``revision`` whose result carries the keys the worker would compute (with masks unless
    ``masks=False``)."""
    with app.state.db() as db:
        p = db.get(Project, uuid.UUID(project["id"]))
        cfg = db.get(ProjectRevision, (p.id, revision)).config_json
        rev = model_rev(cfg["model_profile"])
        k = key or track_key(cfg, CLIP_SHA, rev)
        m = {"masks_key": masks_key(cfg, CLIP_SHA, rev), "masks_zip_asset_id": str(uuid.uuid4())} if masks else None
        j = Job(project_id=p.id, session_id=p.session_id, input_revision=revision, kind="vfx.track",
                options_json={}, model_profile=cfg["model_profile"], state=state,
                result_json={"track_key": k, "invalid_intervals": [], "masks": m, **(result or {})})
        db.add(j)
        db.commit()
        return str(j.id)


def stages(app, job_id):
    from oc_api.models import JobEvent
    with app.state.db() as db:
        ev = db.scalar(select(JobEvent).where(JobEvent.job_id == uuid.UUID(job_id)).order_by(JobEvent.sequence))
        return ev.payload_json["stages"]


# --- vfx.analyze ---------------------------------------------------------------------------------------------

def test_analyze_cpu_with_stages(app, session_client, ready):
    p, rev = ready
    r = post(session_client, p, "vfx.analyze", revision=rev)
    assert r.status_code == 202, r.text
    j, c = job_row(app, r.json()["id"])
    assert j.uses_gpu is False and c.reserved_units == 0 and j.options_json == {}
    assert stages(app, r.json()["id"]) == ["normalize", "proxy", "filmstrip", "audio", "motion"]


def test_analyze_requires_clip_and_rejects_options(app, session_client, vproject):  # noqa: F811
    r = post(session_client, vproject, "vfx.analyze")
    assert r.status_code == 422 and err(r)["code"] == "video_required"
    clip = put_clip(app, vproject["id"])
    proj = configure(session_client, vproject, video_asset_id=clip)
    rev = proj["current_revision"]
    assert post(session_client, vproject, "vfx.analyze", revision=rev).status_code == 202  # no analysis needed
    r = post(session_client, vproject, "vfx.analyze", {"trim": {}}, rev)
    assert r.status_code == 422 and err(r)["code"] == "invalid_options"


def test_analyze_trim_past_clip_end(app, session_client, vproject):  # noqa: F811
    clip = put_clip(app, vproject["id"], duration_ms=5500)
    proj = configure(session_client, vproject, video_asset_id=clip, trim__source_start_ms=1000)
    r = post(session_client, vproject, "vfx.analyze", revision=proj["current_revision"])
    assert r.status_code == 422 and err(r)["code"] == "trim_range" and err(r)["field"] == "config.trim.duration_ms"


# --- vfx.track -----------------------------------------------------------------------------------------------

def test_track_gpu_with_masks_stage(app, session_client, ready):
    p, _ = ready
    proj = configure(session_client, p, subject_prompts=SUBJECT)
    r = post(session_client, p, "vfx.track", revision=proj["current_revision"])
    assert r.status_code == 202, r.text
    j, c = job_row(app, r.json()["id"])
    assert j.uses_gpu is True and c.reserved_units == 120
    assert stages(app, r.json()["id"]) == ["track", "masks"]


def test_masks_need_and_stage():
    cfg = PocketVfxConfig()
    impact = {"input_requirements": {"subject_mask": True, "clean_plate": False}}
    blink = {"input_requirements": {"subject_mask": True, "clean_plate": True}}
    no_mask = {"input_requirements": {"subject_mask": False}}
    for order, needed in (("behind_subject", True), ("split_glow", True), ("in_front", False)):
        cfg.effect.layer_order = order
        assert vfx_jobs.masks_needed(cfg, impact) is needed, order
        assert vfx_jobs.masks_needed(cfg, blink) is True  # drawn inside the mask
    # the stage runs only when a subject is selected (prompts or mask edits) for a preset that uses masks
    assert vfx_jobs.masks_stage_runs(cfg, impact) is False
    cfg.subject_prompts = [SubjectPrompt.model_validate(SUBJECT[0])]
    assert vfx_jobs.masks_stage_runs(cfg, impact) is True and vfx_jobs.masks_stage_runs(cfg, no_mask) is False
    # a render that needs masks always gets them from a track with a selection, even for a preset without
    # subject_mask (else mask_required would never clear)
    for order in ("behind_subject", "split_glow", "in_front"):
        cfg.effect.layer_order = order
        for preset in (impact, blink, no_mask):
            assert not vfx_jobs.masks_needed(cfg, preset) or vfx_jobs.masks_stage_runs(cfg, preset), (order, preset)
    cfg.effect.layer_order = "behind_subject"
    assert vfx_jobs.masks_stage_runs(cfg, no_mask) is True
    cfg.effect.layer_order = "in_front"
    cfg.subject_prompts = []
    cfg.mask_edits = [MaskEdit(frame=3, asset_id=uuid.uuid4())]
    assert vfx_jobs.masks_stage_runs(cfg, impact) is True


def test_track_without_subject_is_cpu(app, session_client, ready):
    p, rev = ready  # behind_subject, but no subject selected: track only
    r = post(session_client, p, "vfx.track", revision=rev)
    assert r.status_code == 202, r.text
    j, c = job_row(app, r.json()["id"])
    assert j.uses_gpu is False and c.reserved_units == 0 and stages(app, r.json()["id"]) == ["track"]


def test_track_requires_analysis(app, session_client, vproject):  # noqa: F811
    clip = put_clip(app, vproject["id"])
    proj = configure(session_client, vproject, video_asset_id=clip)
    r = post(session_client, vproject, "vfx.track", revision=proj["current_revision"])
    assert r.status_code == 422 and err(r)["code"] == "analysis_required"


def test_track_rejects_anchor_outside_clip(app, session_client, ready):
    p, _ = ready
    proj = configure(session_client, p, anchor__frame=130)
    r = post(session_client, p, "vfx.track", revision=proj["current_revision"])
    assert r.status_code == 422 and err(r)["code"] == "anchor_range"


# --- vfx.generate_plate --------------------------------------------------------------------------------------

def test_generate_plate_gpu_and_seed(app, session_client, ready):
    p, rev = ready
    r = post(session_client, p, "vfx.generate_plate", revision=rev)
    assert r.status_code == 202, r.text
    j, c = job_row(app, r.json()["id"])
    assert j.uses_gpu is True and c.reserved_units == 600 and j.options_json == {"seed": 12345}
    assert stages(app, r.json()["id"]) == ["plate", "matte"]
    set_job_state(app, r.json()["id"], "succeeded")
    r = post(session_client, p, "vfx.generate_plate", {"seed": 7}, rev)
    assert r.status_code == 202 and job_row(app, r.json()["id"])[0].options_json == {"seed": 7}


@pytest.mark.parametrize("seed", [-1, 2**31, "x"])
def test_generate_plate_bad_seed(session_client, ready, seed):
    p, rev = ready
    r = post(session_client, p, "vfx.generate_plate", {"seed": seed}, rev)
    assert r.status_code == 422 and err(r)["code"] == "invalid_options" and err(r)["field"] == "options.seed"


def test_generate_plate_blocked_by_validation(app, session_client, ready):
    p, _ = ready
    proj = configure(session_client, p, effect__peak_ms=1200)
    r = post(session_client, p, "vfx.generate_plate", revision=proj["current_revision"])
    assert r.status_code == 422 and err(r)["code"] == "validation_failed"


def test_estimate_generate_plate_price_unknown(session_client, vproject):  # noqa: F811
    r = session_client.post(f"/api/v1/projects/{vproject['id']}/estimates",
                            json={"kind": "vfx.generate_plate", "revision": 1})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["price_status"] == "unknown" and e["price"] is None
    assert e["reserved_units"] == 600 and e["unit"] == "gpu_seconds" and e["uses_gpu"] is True
    assert e["profile"]["id"] == "mock_v1" and e["profile"]["available"] is True


def test_estimate_render_without_options(session_client, vproject):  # noqa: F811
    r = session_client.post(f"/api/v1/projects/{vproject['id']}/estimates",
                            json={"kind": "vfx.render", "revision": 1})
    assert r.status_code == 200, r.text
    assert r.json()["stages"] == ["layers", "composite", "encode"] and r.json()["reserved_units"] == 0


# --- vfx.render ----------------------------------------------------------------------------------------------

def render(client, p, rev, track_job_id=None, profile="quick"):
    return post(client, p, "vfx.render", {"track_job_id": track_job_id, "render_profile": profile}, rev)


def test_render_with_fresh_track(app, session_client, ready):
    p, rev = ready
    tid = make_track(app, p, rev)
    r = render(session_client, p, rev, tid)
    assert r.status_code == 202, r.text
    j, c = job_row(app, r.json()["id"])
    assert j.uses_gpu is False and c.reserved_units == 0
    assert j.options_json == {"track_job_id": tid, "render_profile": "quick"}
    assert stages(app, r.json()["id"]) == ["layers", "composite", "encode"]


def test_render_follow_requires_track(app, session_client, ready):
    p, rev = ready
    r = render(session_client, p, rev)
    assert r.status_code == 422 and err(r)["code"] == "track_required" and err(r)["field"] == "options.track_job_id"
    proj = configure(session_client, p, anchor__follow=False, effect__layer_order="in_front")
    assert render(session_client, p, proj["current_revision"]).status_code == 202  # fixed anchor, in front


def test_render_behind_subject_requires_a_track_with_masks(app, session_client, ready):
    p, _ = ready
    proj = configure(session_client, p, anchor__follow=False)  # fixed anchor, behind the subject
    rev = proj["current_revision"]
    r = render(session_client, p, rev)
    assert r.status_code == 422 and err(r)["code"] == "track_required" and "subject" in err(r)["message"]
    r = render(session_client, p, rev, make_track(app, p, rev, masks=False))
    assert r.status_code == 422 and err(r)["code"] == "validation_failed"
    assert codes(err(r)["details"]["blocking_errors"]) == ["mask_required"]
    assert render(session_client, p, rev, make_track(app, p, rev)).status_code == 202


def test_validate_mask_required_follows_the_masks_key(app, session_client, ready):
    p, rev = ready
    assert codes(validate(session_client, p, rev)["blocking_errors"]) == ["mask_required"]
    make_track(app, p, rev, masks=False)  # tracked without a subject
    assert codes(validate(session_client, p, rev)["blocking_errors"]) == ["mask_required"]
    make_track(app, p, rev)
    assert validate(session_client, p, rev)["valid"] is True
    # a correction changes the track key but not the masks key: the masks stay usable
    proj = configure(session_client, p, corrections=[{"frame": 60, "x": 0.4, "y": 0.4}])
    assert validate(session_client, p, proj["current_revision"])["valid"] is True
    # a new subject prompt changes the masks key: the old masks are stale
    proj = configure(session_client, p, subject_prompts=SUBJECT)
    assert codes(validate(session_client, p, proj["current_revision"])["blocking_errors"]) == ["mask_required"]


def test_generate_plate_is_not_blocked_by_missing_masks(app, session_client, ready):
    p, rev = ready
    assert codes(validate(session_client, p, rev)["blocking_errors"]) == ["mask_required"]
    assert post(session_client, p, "vfx.generate_plate", revision=rev).status_code == 202


def test_render_track_from_older_anchor_is_stale(app, session_client, ready):
    p, rev = ready
    tid = make_track(app, p, rev)
    proj = configure(session_client, p, anchor__x=0.3)
    r = render(session_client, p, proj["current_revision"], tid)
    assert r.status_code == 422 and err(r)["code"] == "track_stale" and err(r)["field"] == "options.track_job_id"


@pytest.mark.parametrize("path,value", [
    ("trim__duration_ms", 4000),
    ("corrections", [{"frame": 60, "x": 0.4, "y": 0.4}]),
    ("subject_prompts", [{"frame": 43, "points": [{"x": 0.5, "y": 0.4, "label": 1}]}]),
])
def test_render_track_stale_on_track_inputs(app, session_client, ready, path, value):
    p, rev = ready
    tid = make_track(app, p, rev)
    if path.startswith("trim"):
        cfg = session_client.get(f"/api/v1/projects/{p['id']}").json()["config"]
        from test_vfx_config import put_work
        work = put_work(app, p["id"], cfg["video_asset_id"], trim={"source_start_ms": 0, "duration_ms": 4000})
        proj = configure(session_client, p, trim__duration_ms=4000, work_asset_id=work, anchor__frame=40)
    else:
        proj = configure(session_client, p, **{path: value})
    r = render(session_client, p, proj["current_revision"], tid)
    assert r.status_code == 422 and err(r)["code"] == "track_stale"


def test_render_only_intensity_changed_reuses_track(app, session_client, ready):
    p, rev = ready
    tid = make_track(app, p, rev)
    proj = configure(session_client, p, effect__intensity=0.3, effect__scale=1.5, effect__peak_ms=2000,
                     plate__seed=99)
    r = render(session_client, p, proj["current_revision"], tid)
    assert r.status_code == 202, r.text


def test_render_track_job_checks(app, session_client, ready):
    p, rev = ready
    other = session_client.post("/api/v1/projects", json={"app_slug": "pocket-vfx", "title": "o"}).json()
    foreign = make_track(app, other, 1)
    r = render(session_client, p, rev, foreign)
    assert r.status_code == 422 and err(r)["code"] == "track_not_found"
    r = render(session_client, p, rev, str(uuid.uuid4()))
    assert r.status_code == 422 and err(r)["code"] == "track_not_found"
    running = make_track(app, p, rev, state="running")
    r = render(session_client, p, rev, running)
    assert r.status_code == 422 and err(r)["code"] == "track_not_finished"
    r = render(session_client, p, rev, make_track(app, p, rev), profile="best")
    assert r.status_code == 422 and err(r)["code"] == "invalid_options"


def test_render_validation_failed(app, session_client, ready):
    p, _ = ready
    proj = configure(session_client, p, effect__peak_ms=1200, anchor__follow=False, effect__layer_order="in_front")
    r = render(session_client, p, proj["current_revision"])
    e = err(r)
    assert r.status_code == 422 and e["code"] == "validation_failed"
    assert [x["code"] for x in e["details"]["blocking_errors"]] == ["timing_order"]


def test_second_concurrent_render_is_render_busy(app, session_client, ready):
    p, _ = ready
    proj = configure(session_client, p, anchor__follow=False, effect__layer_order="in_front")
    rev = proj["current_revision"]
    assert render(session_client, p, rev).status_code == 202
    r = render(session_client, p, rev)
    assert r.status_code == 429 and err(r)["code"] == "render_busy"


def test_render_blink_without_clean_plate(app, session_client, ready):
    p, _ = ready
    proj = configure(session_client, p, effect__preset_id="blink_out_v1", anchor__follow=False)
    rev = proj["current_revision"]
    r = render(session_client, p, rev, make_track(app, p, rev))
    assert r.status_code == 422 and err(r)["code"] == "validation_failed"
    assert [x["code"] for x in err(r)["details"]["blocking_errors"]] == ["clean_plate_required"]


def test_render_blink_on_a_moving_camera_is_clean_plate_mismatch(app, session_client, vproject):  # noqa: F811
    proj, _, work = make_ready(app, session_client, vproject, effect__preset_id="blink_out_v1",
                               anchor__follow=False)
    put_motion(app, vproject["id"], work, "moving")
    plate = put_asset(app, vproject["id"], "clean_plate", b"x", "image/png", 4, 4)
    proj = configure(session_client, vproject, clean_plate_asset_id=plate)
    rev = proj["current_revision"]
    r = render(session_client, vproject, rev, make_track(app, vproject, rev))
    assert r.status_code == 422 and err(r)["code"] == "clean_plate_mismatch"
    assert err(r)["field"] == "config.effect.preset_id"
    assert [x["code"] for x in err(r)["details"]["blocking_errors"]] == ["clean_plate_mismatch"]
    with app.state.db() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "vfx.render")) == 0


# --- plates (Ruling R3) ---------------------------------------------------------------------------------------

def make_plate(app, project, frames=49, preset_id="impact_wave_v1", state="succeeded", kind="vfx.generate_plate"):
    """A raw_plate asset made by a ``kind`` job in ``state`` (what vfx.generate_plate stores)."""
    with app.state.db() as db:
        p = db.get(Project, uuid.UUID(project["id"]))
        j = Job(project_id=p.id, session_id=p.session_id, input_revision=1, kind=kind, options_json={"seed": 1},
                model_profile="mock_v1", state=state, result_json={})
        db.add(j)
        db.flush()
        aid = uuid.uuid4()
        db.add(Asset(id=aid, project_id=p.id, role="raw_plate", state="ready", object_key=f"x/{aid}.mp4",
                     mime="video/mp4", bytes=1, width=768, height=448, frame_count=frames, job_id=j.id,
                     meta_json={"preset_id": preset_id, "palette": "amber", "seed": 1, "frames": frames,
                                "mock": True}))
        db.commit()
        return str(aid)


def generated(client, p, plate_id, **changes):
    return configure(client, p, anchor__follow=False, effect__layer_order="in_front", plate__mode="generated",
                     plate__asset_id=plate_id, render_profile="generated", **changes)


def test_generated_render_needs_a_selected_plate(app, session_client, ready):
    p, _ = ready
    proj = configure(session_client, p, anchor__follow=False, effect__layer_order="in_front")
    rev = proj["current_revision"]
    r = render(session_client, p, rev, profile="generated")
    assert r.status_code == 422 and err(r)["code"] == "plate_required" and err(r)["field"] == "config.plate.asset_id"
    # asset id set, but mode still bundled: the option asks for a generated plate the config does not select
    proj = configure(session_client, p, plate__asset_id=make_plate(app, p))
    r = render(session_client, p, proj["current_revision"], profile="generated")
    assert r.status_code == 422 and err(r)["code"] == "plate_required"
    # quick ignores plate.asset_id and uses the bundled plate
    assert render(session_client, p, proj["current_revision"]).status_code == 202


def test_generated_render_plate_must_match(app, session_client, ready):
    p, _ = ready
    for plate in (make_plate(app, p, preset_id="energy_aura_v1"), make_plate(app, p, state="running"),
                  make_plate(app, p, kind="vfx.track")):
        proj = generated(session_client, p, plate)
        r = render(session_client, p, proj["current_revision"], profile="generated")
        assert r.status_code == 422 and err(r)["code"] == "plate_mismatch", plate
    proj = generated(session_client, p, make_plate(app, p))
    r = render(session_client, p, proj["current_revision"], profile="generated")
    assert r.status_code == 202, r.text
    assert job_row(app, r.json()["id"])[0].options_json["render_profile"] == "generated"


def test_plate_length_at_plan_time(app, session_client, ready):
    p, _ = ready
    plate = make_plate(app, p, frames=121)
    # 2 s window (frames 34..81 = 48): 121 > 2 x 48 -> the first 48 plate frames (Ruling R3 trim)
    proj = generated(session_client, p, plate, effect__end_ms=3375)
    r = render(session_client, p, proj["current_revision"], profile="generated")
    assert r.status_code == 202, r.text
    set_job_state(app, r.json()["id"], "succeeded")
    # 0.4 s window (34..43 = 10 frames): trimmed too, still fine
    proj = configure(session_client, p, effect__end_ms=1800)
    r = render(session_client, p, proj["current_revision"], profile="generated")
    assert r.status_code == 202, r.text
    set_job_state(app, r.json()["id"], "succeeded")
    # a 49-frame plate under a 5 s window (0..119 = 120 frames > 2 x 49): no loop for a generated plate -> 422
    short = make_plate(app, p, frames=49)
    proj = generated(session_client, p, short, effect__start_ms=0, effect__peak_ms=400, effect__end_ms=5000)
    rev = proj["current_revision"]
    r = render(session_client, p, rev, profile="generated")
    assert r.status_code == 422 and err(r)["code"] == "plate_length" and err(r)["field"] == "config.effect.end_ms"
    assert codes(validate(session_client, p, rev)["blocking_errors"]) == ["plate_length"]  # config profile
    assert codes(err(r)["details"]["blocking_errors"]) == ["plate_length"]  # same details as validation_failed
    with app.state.db() as db:
        n = db.scalar(select(func.count()).select_from(Job).where(Job.project_id == uuid.UUID(p["id"]),
                                                                  Job.kind == "vfx.render"))
    assert n == 2  # the refused render created no job
    # the bundled 49-frame plate is loopable: the same window renders quick (the option wins over the config)
    assert render(session_client, p, rev).status_code == 202
    # a new plate job for the long window is not blocked by the selected short plate
    r = post(session_client, p, "vfx.generate_plate", revision=rev)
    assert r.status_code == 202, r.text


def test_validate_warns_when_the_plate_palette_differs(app, session_client, ready):
    p, _ = ready
    plate = make_plate(app, p)  # made with the amber palette
    proj = generated(session_client, p, plate)
    report = validate(session_client, p, proj["current_revision"])
    assert "plate_palette_differs" not in codes(report["warnings"])
    proj = configure(session_client, p, effect__palette="cyan")
    report = validate(session_client, p, proj["current_revision"])
    assert report["valid"] is True and "plate_palette_differs" in codes(report["warnings"])


# --- track validity at peak in validate ---------------------------------------------------------------------

def test_validate_track_invalid_at_peak(app, session_client, ready):
    p, rev = ready  # peak_ms 1800 -> frame 43
    make_track(app, p, rev, result={"invalid_intervals": [[40, 50]]})
    v = validate(session_client, p, rev)
    assert codes(v["blocking_errors"]) == ["track_invalid_at_peak"] and v["warnings"] == []
    proj = configure(session_client, p, anchor__follow=False)
    make_track(app, p, proj["current_revision"], result={"invalid_intervals": [[40, 50]]})
    v = validate(session_client, p, proj["current_revision"])
    assert v["valid"] is True and codes(v["warnings"]) == ["track_invalid_at_peak"]
    proj = configure(session_client, p, anchor__follow=True, effect__peak_ms=2200)  # frame 53: valid again
    assert validate(session_client, p, proj["current_revision"])["valid"] is True


def test_render_track_invalid_at_peak_blocks_follow(app, session_client, ready):
    p, rev = ready
    tid = make_track(app, p, rev, result={"invalid_intervals": [[43, 43]]})
    r = render(session_client, p, rev, tid)
    assert r.status_code == 422 and err(r)["code"] == "validation_failed"
    assert [x["code"] for x in err(r)["details"]["blocking_errors"]] == ["track_invalid_at_peak"]


def test_stale_track_is_ignored_by_validate(app, session_client, ready):
    p, rev = ready
    make_track(app, p, rev, key="0" * 64, result={"invalid_intervals": [[0, 119]]})
    assert validate(session_client, p, rev)["valid"] is True


# --- exports -------------------------------------------------------------------------------------------------

def test_export_kind_only_via_exports(app, session_client, ready):
    p, _ = ready
    proj = configure(session_client, p, anchor__follow=False, effect__layer_order="in_front")
    rev = proj["current_revision"]
    r = post(session_client, p, "vfx.export", {"render_job_id": str(uuid.uuid4())}, rev)
    assert r.status_code == 422 and err(r)["code"] == "invalid_kind"
    rid = render(session_client, p, rev).json()["id"]
    set_job_state(app, rid, "succeeded")
    for fmt in ("mp4", "cover_png", "recipe_zip", "layers_zip"):
        r = session_client.post(f"/api/v1/projects/{p['id']}/exports", json={"job_id": rid, "format": fmt})
        assert r.status_code == 202, r.text
        j, _ = job_row(app, r.json()["id"])
        assert j.kind == "vfx.export" and j.options_json == {"render_job_id": rid, "format": fmt}
        assert j.input_revision == rev
    r = session_client.post(f"/api/v1/projects/{p['id']}/exports", json={"job_id": rid, "format": "path_json"})
    assert r.status_code == 422 and err(r)["code"] == "invalid_format"
