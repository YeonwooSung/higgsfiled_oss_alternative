import uuid

import pytest
from pydantic import ValidationError

from helpers import configure, put_asset
from oc_api.apps import get_app
from oc_api.models import Asset
from oc_api.pocket_vfx.config import PocketVfxConfig, default_config
from oc_api.pocket_vfx.jobs import plate_length_errors

CLIP_SHA = "c" * 64


@pytest.fixture
def vproject(session_client):
    r = session_client.post("/api/v1/projects", json={"app_slug": "pocket-vfx", "title": "Stomp"})
    assert r.status_code == 201, r.text
    return r.json()


def put_clip(app, project_id, duration_ms=6000):
    aid = put_asset(app, project_id, "clip", b"\x00" * 64, "video/mp4", 1920, 1080,
                    meta={"source": {"duration_ms": duration_ms}, "original_sha256": CLIP_SHA})
    with app.state.db() as db:
        db.get(Asset, uuid.UUID(aid)).duration_ms = duration_ms
        db.commit()
    return aid


def put_work(app, project_id, clip_id, trim=None, aspect="16:9"):
    meta = {"source_asset_id": clip_id, "trim": trim or {"source_start_ms": 0, "duration_ms": 5000},
            "aspect": aspect}
    return put_asset(app, project_id, "work_clip", b"\x00" * 32, "video/mp4", 1280, 720, meta=meta)


def make_ready(app, client, project, duration_ms=6000, **changes):
    """Clip + analysis attached to the project; returns (project json, clip id, work id)."""
    clip = put_clip(app, project["id"], duration_ms)
    configure(client, project, video_asset_id=clip, work_asset_id=None)
    work = put_work(app, project["id"], clip)
    proj = configure(client, project, work_asset_id=work, **changes)
    return proj, clip, work


def validate(client, project, revision):
    r = client.post(f"/api/v1/projects/{project['id']}/vfx/validate", json={"revision": revision})
    assert r.status_code == 200, r.text
    return r.json()


def codes(errors):
    return [e["code"] for e in errors]


def test_default_config_values():
    cfg = default_config("mock_v1")
    PocketVfxConfig.model_validate(cfg.model_dump(mode="json"))
    assert cfg.model_profile == "mock_v1" and cfg.schema_version == 1
    assert cfg.trim.model_dump() == {"source_start_ms": 0, "duration_ms": 5000}
    e = cfg.effect
    assert (e.preset_id, e.start_ms, e.peak_ms, e.end_ms) == ("impact_wave_v1", 1400, 1800, 3400)
    assert (e.scale, e.intensity, e.palette, e.rotation_deg, e.squash, e.layer_order) == (
        1.0, 0.8, "amber", 0, 0.5, "behind_subject")  # impact_wave_v1 preset defaults
    assert cfg.anchor.model_dump() == {"frame": 43, "x": 0.5, "y": 0.7, "follow": True, "track_window_frames": None}
    assert cfg.subject_prompts == [] and cfg.corrections == [] and cfg.mask_edits == []
    assert cfg.plate.model_dump() == {"mode": "bundled", "bundled_id": None, "asset_id": None, "seed": 12345,
                                      "matte": {"threshold": 0.06, "knee": 0.04}}
    assert cfg.audio.model_dump() == {"keep_original": True, "sfx_enabled": False, "sfx_gain_db": -9}
    assert cfg.output.model_dump() == {"aspect": "16:9", "fps": 24}
    assert cfg.render_profile == "quick" and cfg.video_asset_id is None and cfg.work_asset_id is None


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(bogus=1),
    lambda c: c["output"].update(fps=30),
    lambda c: c["output"].update(aspect="1:1"),
    lambda c: c["trim"].update(duration_ms=999),
    lambda c: c["trim"].update(duration_ms=5001),
    lambda c: c["trim"].update(source_start_ms=-1),
    lambda c: c["effect"].update(preset_id="nope_v1"),
    lambda c: c["effect"].update(palette="green"),
    lambda c: c["effect"].update(layer_order="on_top"),
    lambda c: c["effect"].update(scale=2.5),
    lambda c: c["effect"].update(squash=0.1),
    lambda c: c["effect"].update(rotation_deg=181),
    lambda c: c["anchor"].update(x=1.1),
    lambda c: c["anchor"].update(frame=-1),
    lambda c: c["plate"].update(seed=-1),
    lambda c: c["plate"].update(seed=2**31),
    lambda c: c["plate"].update(mode="ai"),
    lambda c: c["plate"]["matte"].update(threshold=0.31),
    lambda c: c["plate"]["matte"].update(knee=0.25),
    lambda c: c["audio"].update(sfx_gain_db=1),
    lambda c: c.update(render_profile="best"),
    lambda c: c.update(corrections=[{"frame": i, "x": 0.5, "y": 0.5} for i in range(33)]),
    lambda c: c.update(subject_prompts=[{"frame": 0, "points": []}] * 9),
    lambda c: c.update(subject_prompts=[{"frame": 0, "points": [{"x": 0.5, "y": 0.5, "label": 2}]}]),
    lambda c: c.update(subject_prompts=[{"frame": 0, "points": [{"x": 0.5, "y": 0.5, "label": 1}] * 17}]),
    lambda c: c.update(mask_edits=[{"frame": 0, "asset_id": str(uuid.uuid4())}] * 17),
])
def test_config_rejects(mutate):
    c = default_config().model_dump(mode="json")
    mutate(c)
    with pytest.raises(ValidationError):
        PocketVfxConfig.model_validate(c)


def test_project_create_uses_default_config(vproject):
    assert vproject["config"] == default_config("mock_v1").model_dump(mode="json")


def test_module_contract():
    m = get_app("pocket-vfx")
    assert m.kinds == ("vfx.analyze", "vfx.track", "vfx.generate_plate", "vfx.render", "vfx.export")
    assert m.export_only_kinds == frozenset({"vfx.export"}) and m.render_kinds == frozenset({"vfx.render"})
    assert m.export_formats == {"vfx.render": ("mp4", "cover_png", "recipe_zip", "layers_zip")}
    assert (m.export_kind, m.export_source_key) == ("vfx.export", "render_job_id")
    assert set(m.upload_roles) == {"clip", "clean_plate", "mask_edit"}
    assert m.upload_limits == {"clip": 100 * 1024 * 1024}
    assert m.kind_workflows == {"vfx.track": ("vfx_masks_v1",), "vfx.generate_plate": ("vfx_plate_v1",)}


def test_patch_trim_change_with_old_analysis_is_stale(app, session_client, vproject):
    proj, *_ = make_ready(app, session_client, vproject)
    cfg = proj["config"]
    cfg["trim"]["source_start_ms"] = 500
    r = session_client.patch(f"/api/v1/projects/{vproject['id']}",
                             json={"expected_revision": proj["current_revision"], "config": cfg})
    assert r.status_code == 422 and r.json()["error"]["code"] == "analysis_stale"
    assert r.json()["error"]["field"] == "config.work_asset_id"
    cfg["trim"]["source_start_ms"], cfg["output"]["aspect"] = 0, "9:16"
    r = session_client.patch(f"/api/v1/projects/{vproject['id']}",
                             json={"expected_revision": proj["current_revision"], "config": cfg})
    assert r.status_code == 422 and r.json()["error"]["code"] == "analysis_stale"
    cfg["output"]["aspect"], cfg["work_asset_id"], cfg["trim"]["source_start_ms"] = "16:9", None, 500
    r = session_client.patch(f"/api/v1/projects/{vproject['id']}",
                             json={"expected_revision": proj["current_revision"], "config": cfg})
    assert r.status_code == 200, r.text


def test_patch_analysis_from_other_clip_is_stale(app, session_client, vproject):
    proj, clip, _ = make_ready(app, session_client, vproject)
    other = put_clip(app, vproject["id"])
    work = put_work(app, vproject["id"], other)
    r = session_client.patch(f"/api/v1/projects/{vproject['id']}",
                             json={"expected_revision": proj["current_revision"],
                                   "config": {**proj["config"], "work_asset_id": work}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "analysis_stale"


@pytest.mark.parametrize("field,role", [("video_asset_id", "clean_plate"), ("work_asset_id", "clip"),
                                        ("clean_plate_asset_id", "mask_edit"), ("plate.asset_id", "clip")])
def test_patch_rejects_wrong_roles(app, session_client, vproject, field, role):
    aid = put_asset(app, vproject["id"], role, b"x", "image/png", 4, 4)
    cfg = vproject["config"]
    target, key = (cfg["plate"], "asset_id") if field == "plate.asset_id" else (cfg, field)
    target[key] = aid
    r = session_client.patch(f"/api/v1/projects/{vproject['id']}", json={"expected_revision": 1, "config": cfg})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] in ("invalid_asset_role", "analysis_stale")
    assert r.json()["error"]["field"] == f"config.{field}"


def test_patch_mask_edit_role_and_project(app, session_client, vproject):
    good = put_asset(app, vproject["id"], "mask_edit", b"x", "image/png", 4, 4)
    bad = put_asset(app, vproject["id"], "clean_plate", b"x", "image/png", 4, 4)
    other = session_client.post("/api/v1/projects", json={"app_slug": "pocket-vfx", "title": "o"}).json()
    foreign = put_asset(app, other["id"], "mask_edit", b"x", "image/png", 4, 4)
    cfg = vproject["config"]
    for aid, code in ((bad, "invalid_asset_role"), (foreign, "asset_not_found")):
        cfg["mask_edits"] = [{"frame": 3, "asset_id": good}, {"frame": 5, "asset_id": aid}]
        r = session_client.patch(f"/api/v1/projects/{vproject['id']}", json={"expected_revision": 1, "config": cfg})
        assert r.status_code == 422 and r.json()["error"]["code"] == code
        assert r.json()["error"]["field"] == "config.mask_edits.1.asset_id"
    cfg["mask_edits"] = [{"frame": 3, "asset_id": good}]
    assert session_client.patch(f"/api/v1/projects/{vproject['id']}",
                                json={"expected_revision": 1, "config": cfg}).status_code == 200


def test_presets_endpoint(session_client):
    r = session_client.get("/api/v1/pocket-vfx/presets")
    assert r.status_code == 200
    ps = {p["id"]: p for p in r.json()["presets"]}
    assert set(ps) == {"impact_wave_v1", "energy_aura_v1", "blink_out_v1"}
    blink = ps["blink_out_v1"]
    assert blink["input_requirements"]["clean_plate"] is True
    assert blink["availability"]["status"] == "requires_clean_plate"
    assert blink["availability"]["requires"] == ["clean_plate"]
    assert blink["availability"]["condition_en"] and blink["availability"]["condition_ko"]
    assert ps["impact_wave_v1"]["availability"] == {"status": "available", "requires": []}
    assert ps["impact_wave_v1"]["controls"]["squash"] == [0.2, 1.0]
    assert ps["impact_wave_v1"]["defaults"]["palette"] == "amber" and ps["impact_wave_v1"]["version"] == 1
    assert ps["impact_wave_v1"]["plate_modes"] == ["bundled", "generated"]
    assert ps["impact_wave_v1"]["bundled_plates"] == [{"id": "dust_burst_v1", "frames": 49, "loopable": True}]
    assert "plate_prompt" not in ps["impact_wave_v1"]


def test_capabilities(session_client):
    caps = session_client.get("/api/v1/capabilities", params={"app_slug": "pocket-vfx"}).json()
    assert "vfx.export" in caps["kinds"] and len(caps["presets"]) == 3
    assert caps["limits"]["trim_duration_ms"] == [1000, 5000] and caps["limits"]["fps"] == 24
    assert set(caps["kind_profiles"]) == {"vfx.track", "vfx.generate_plate"}
    mock = next(p for p in caps["kind_profiles"]["vfx.track"] if p["id"] == "mock_v1")
    ow = next(p for p in caps["kind_profiles"]["vfx.track"] if p["id"] == "open_weights_v1")
    assert mock["available"] is True and ow["available"] is False


def test_validate_default_project_reports_missing_inputs(session_client, vproject):
    v = validate(session_client, vproject, 1)
    assert v["valid"] is False and v["warnings"] == []
    assert codes(v["blocking_errors"]) == ["video_required", "analysis_required"]
    assert all(set(e) == {"code", "field", "message"} and e["message"] for e in v["blocking_errors"])


def test_validate_ready_project_is_valid(app, session_client, vproject):
    proj, *_ = make_ready(app, session_client, vproject)
    assert validate(session_client, vproject, proj["current_revision"]) == {
        "valid": True, "blocking_errors": [], "warnings": []}


def test_validate_timing_order(app, session_client, vproject):
    proj, *_ = make_ready(app, session_client, vproject, effect__peak_ms=1200)
    v = validate(session_client, vproject, proj["current_revision"])
    assert v["valid"] is False
    assert [(e["code"], e["field"]) for e in v["blocking_errors"]] == [("timing_order", "config.effect.start_ms")]


def test_validate_trim_and_frame_ranges(app, session_client, vproject):
    proj, *_ = make_ready(app, session_client, vproject, duration_ms=4500)
    v = validate(session_client, vproject, proj["current_revision"])
    assert [(e["code"], e["field"]) for e in v["blocking_errors"]] == [("trim_range", "config.trim.duration_ms")]
    proj, *_ = make_ready(app, session_client, vproject, anchor__frame=120,
                          corrections=[{"frame": 200, "x": 0.5, "y": 0.5}])
    v = validate(session_client, vproject, proj["current_revision"])
    assert [(e["code"], e["field"]) for e in v["blocking_errors"]] == [
        ("anchor_range", "config.anchor.frame"), ("frame_range", "config.corrections.0.frame")]


def test_validate_blink_requires_clean_plate(app, session_client, vproject):
    proj, *_ = make_ready(app, session_client, vproject, effect__preset_id="blink_out_v1")
    v = validate(session_client, vproject, proj["current_revision"])
    assert [(e["code"], e["field"]) for e in v["blocking_errors"]] == [
        ("clean_plate_required", "config.clean_plate_asset_id")]
    plate = put_asset(app, vproject["id"], "clean_plate", b"x", "image/png", 4, 4)
    proj = configure(session_client, vproject, clean_plate_asset_id=plate)
    assert validate(session_client, vproject, proj["current_revision"])["valid"] is True


def test_validate_bundled_plate_must_belong_to_preset(app, session_client, vproject):
    proj, *_ = make_ready(app, session_client, vproject, plate__bundled_id="energy_wisps_v1")
    v = validate(session_client, vproject, proj["current_revision"])
    assert [(e["code"], e["field"]) for e in v["blocking_errors"]] == [("plate_mismatch", "config.plate.bundled_id")]


def test_validate_endpoint_errors(session_client, vproject):
    r = session_client.post(f"/api/v1/projects/{vproject['id']}/vfx/validate", json={"revision": 9})
    assert r.status_code == 422 and r.json()["error"]["code"] == "revision_not_found"
    cam = session_client.post("/api/v1/projects", json={"app_slug": "camera-sketch", "title": "c"}).json()
    r = session_client.post(f"/api/v1/projects/{cam['id']}/vfx/validate", json={"revision": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_app"


def test_plate_length_check_is_a_stub_until_task_12():
    assert plate_length_errors(default_config()) == []
