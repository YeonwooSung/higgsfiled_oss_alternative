import uuid

import numpy as np
import pytest
from pydantic import ValidationError

from helpers import configure, put_asset
from oc_api.apps import get_app
from oc_api.camera_sketch.config import CameraSketchConfig, default_config
from oc_api.models import Asset
from open_creative.camera_sketch.scene import downsample_depth, encode_depth_png16
from open_creative.media.image import to_png_bytes


def make_scene_and_depth(app, project_id, w=160, h=90, source_asset_id=None, near_left=True, lowres=False):
    """Scene + depth assets; ``lowres`` also stores the ``depth_lowres`` copy the analyze job writes."""
    rgb = np.full((h, w, 3), 128, np.uint8)
    scene = put_asset(app, project_id, "scene", to_png_bytes(rgb), "image/png", w, h)
    d = np.tile(np.linspace(1.0, 0.2, w, dtype=np.float32), (h, 1))
    png, rng = encode_depth_png16(d)
    meta = {"source_asset_id": source_asset_id or scene,
            "convention": {"kind": "inverse_relative", "scale": 1.0, "offset": 0.0, "eps": 1e-6},
            "min": rng["min"], "max": rng["max"], "model": "depth-anything-v2-small"}
    depth = put_asset(app, project_id, "depth", png, "image/png", w, h, meta=meta)
    if lowres:
        low = downsample_depth(d)
        lpng, lrng = encode_depth_png16(low)
        lid = put_asset(app, project_id, "depth_lowres", lpng, "image/png", low.shape[1], low.shape[0],
                        meta={**meta, "min": lrng["min"], "max": lrng["max"], "source_depth_asset_id": depth})
        with app.state.db() as db:
            row = db.get(Asset, uuid.UUID(depth))
            row.meta_json = {**meta, "lowres_asset_id": lid}
            db.commit()
    return scene, depth


@pytest.fixture
def cproject(session_client):
    r = session_client.post("/api/v1/projects", json={"app_slug": "camera-sketch", "title": "Cam"})
    assert r.status_code == 201, r.text
    return r.json()


def test_default_config_validates():
    cfg = default_config("mock_v1")
    assert cfg.path.preset == "slide" and cfg.model_profile == "mock_v1"
    assert (cfg.path.lateral_ratio, cfg.path.push_ratio, cfg.path.arc_deg) == (0, 0, 0)
    CameraSketchConfig.model_validate(cfg.model_dump(mode="json"))


def test_extra_forbidden_and_literals():
    base = default_config().model_dump(mode="json")
    for mutate in (lambda c: c.update(bogus=1), lambda c: c["camera"].update(roll_deg=5),
                   lambda c: c["output"].update(fps=30), lambda c: c["target"].update(depth_scale=3)):
        c = default_config().model_dump(mode="json")
        mutate(c)
        with pytest.raises(ValidationError):
            CameraSketchConfig.model_validate(c)
    assert base["schema_version"] == 1


def test_sketch_points_max_5():
    c = default_config().model_dump(mode="json")
    c["path"]["sketch_points"] = [{"t": i / 5, "u": 0.5, "v": 0.5} for i in range(6)]
    with pytest.raises(ValidationError):
        CameraSketchConfig.model_validate(c)


def test_module_contract():
    m = get_app("camera-sketch")
    assert m.kinds == ("camera.analyze", "camera.guide", "camera.cinematic", "camera.export")
    assert m.asset_ref_fields == ("scene_asset_id", "depth_asset_id")
    assert set(m.upload_roles) == {"scene"}
    assert "camera.export" in m.export_only_kinds


def test_create_project_and_default(cproject):
    assert cproject["config"]["path"]["preset"] == "slide"
    assert cproject["config"]["model_profile"] == "mock_v1"


def test_lateral_out_of_range_is_422(session_client, cproject):
    cfg = cproject["config"]
    cfg["path"]["lateral_ratio"] = 0.06
    r = session_client.patch(f"/api/v1/projects/{cproject['id']}",
                             json={"expected_revision": 1, "config": cfg})
    assert r.status_code == 422
    assert r.json()["error"]["field"] == "config.path.lateral_ratio"


def test_scene_swap_with_old_depth_is_depth_stale(app, session_client, cproject):
    pid = cproject["id"]
    scene1, depth1 = make_scene_and_depth(app, pid)
    configure(session_client, cproject, scene_asset_id=scene1, depth_asset_id=depth1)  # lineage ok
    scene2, _ = make_scene_and_depth(app, pid)
    cur = session_client.get(f"/api/v1/projects/{pid}").json()
    cfg = cur["config"]
    cfg["scene_asset_id"] = scene2
    r = session_client.patch(f"/api/v1/projects/{pid}",
                             json={"expected_revision": cur["current_revision"], "config": cfg})
    assert r.status_code == 422 and r.json()["error"]["code"] == "depth_stale"
    cfg["depth_asset_id"] = None
    r = session_client.patch(f"/api/v1/projects/{pid}",
                             json={"expected_revision": cur["current_revision"], "config": cfg})
    assert r.status_code == 200


def test_depth_asset_must_be_depth_role(app, session_client, cproject):
    pid = cproject["id"]
    scene1, _ = make_scene_and_depth(app, pid)
    other = put_asset(app, pid, "scene", b"x", "image/png", 4, 4)
    cur = session_client.get(f"/api/v1/projects/{pid}").json()
    cfg = cur["config"]
    cfg["scene_asset_id"], cfg["depth_asset_id"] = scene1, other
    r = session_client.patch(f"/api/v1/projects/{pid}",
                             json={"expected_revision": cur["current_revision"], "config": cfg})
    assert r.status_code == 422 and r.json()["error"]["code"] == "depth_stale"


def test_presets_endpoint(session_client):
    r = session_client.get("/api/v1/camera-sketch/presets")
    assert r.status_code == 200
    ids = {p["id"]: p for p in r.json()["presets"]}
    assert set(ids) == {"push_in", "slide", "small_arc"}
    assert ids["slide"]["bounds"]["lateral_ratio"] == [-0.05, 0.05]


def test_export_kind_rejected_on_jobs_and_capabilities_lists_it(session_client, cproject):
    r = session_client.post(f"/api/v1/projects/{cproject['id']}/jobs",
                            json={"kind": "camera.export", "revision": 1, "options": {}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_kind"
    caps = session_client.get("/api/v1/capabilities", params={"app_slug": "camera-sketch"}).json()
    assert "camera.export" in caps["kinds"]


def test_scene_upload_role_accepts_jpeg(session_client, cproject):
    r = session_client.post(f"/api/v1/projects/{cproject['id']}/assets/init",
                            json={"role": "scene", "filename": "a.jpg", "mime": "image/jpeg", "bytes": 100})
    assert r.status_code == 201


def test_scene_asset_must_have_scene_role(app, session_client, cproject):
    pid = cproject["id"]
    _, depth = make_scene_and_depth(app, pid)
    cur = session_client.get(f"/api/v1/projects/{pid}").json()
    cfg = cur["config"]
    cfg["scene_asset_id"] = depth
    r = session_client.patch(f"/api/v1/projects/{pid}",
                             json={"expected_revision": cur["current_revision"], "config": cfg})
    assert r.status_code == 422 and r.json()["error"]["field"] == "config.scene_asset_id"
