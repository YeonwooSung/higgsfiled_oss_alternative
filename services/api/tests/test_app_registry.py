import json

from fastapi.testclient import TestClient

from conftest_db import database_url_for_tests
from oc_api.app import create_app
from oc_api.apps import APPS, get_app
from oc_api.errors import ApiError
from oc_api.settings import Settings
from open_creative.comfy.bindings import REQUIRED_BINDINGS, REQUIRED_OUTPUTS

OLD_KINDS = ("product.segment", "product.background", "product.background_video", "product.render",
             "product.inspect")


def test_product_lock_module_contract():
    m = get_app("product-lock")
    assert m is APPS["product-lock"] and m.slug == "product-lock"
    assert m.kinds == OLD_KINDS
    assert m.render_kinds == frozenset({"product.render"})
    assert m.export_formats == {"product.render": ("mp4", "cover_png", "recipe_zip")}
    assert set(m.upload_roles) == {"product", "mask", "core_mask"}
    assert set(m.asset_ref_fields) >= {"product_asset_id", "mask_asset_id", "core_mask_asset_id",
                                       "scene.background_asset_id", "scene.background_video_asset_id"}
    assert set(m.workflows) == {"product_segment_v1", "product_background_image_v1",
                                "product_background_video_v1"}


def test_unknown_app_is_422():
    try:
        get_app("nope")
    except ApiError as e:
        assert e.status == 422 and e.code == "unknown_app"
    else:
        raise AssertionError("expected ApiError")


def test_capabilities_unknown_app_422(session_client):
    r = session_client.get("/api/v1/capabilities", params={"app_slug": "nope"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_app"


def test_camera_sketch_project_creates(session_client):
    r = session_client.post("/api/v1/projects", json={"app_slug": "camera-sketch", "title": "x"})
    assert r.status_code == 201 and r.json()["config"]["path"]["preset"] == "slide"


def _write_exports(root, names):
    for name in names:
        binds = REQUIRED_BINDINGS[name]
        graph = {"1": {"inputs": {b: None for b in binds}}, "2": {"inputs": {}}}
        (root / f"{name}.api.json").write_text(json.dumps(graph))
        (root / f"{name}.bindings.json").write_text(json.dumps({
            "workflow": name, "graph": f"{name}.api.json",
            "bindings": {b: {"node_id": "1", "input": b, "type": t} for b, t in binds.items()},
            "outputs": {o: {"node_id": "2"} for o in REQUIRED_OUTPUTS[name]},
        }))


def test_open_weights_ignores_other_apps_workflows(tmp_path):
    root = tmp_path / "workflows"
    (root / "product-lock").mkdir(parents=True)
    (root / "camera-sketch").mkdir()  # no camera exports at all
    _write_exports(root / "product-lock", get_app("product-lock").workflows)
    s = Settings(database_url=database_url_for_tests(tmp_path), storage_dir=tmp_path / "storage",
                 signing_secret="test-secret", cookie_secure=False, default_profile="mock_v1",
                 workflows_dir=root, COMFY_BASE_URL="http://127.0.0.1:8189")
    with TestClient(create_app(s, create_tables=True)) as c:
        c.post("/api/v1/sessions")
        caps = c.get("/api/v1/capabilities", params={"app_slug": "product-lock"}).json()
    ow = next(p for p in caps["profiles"] if p["id"] == "open_weights_v1")
    assert ow["available"], ow["reason"]
