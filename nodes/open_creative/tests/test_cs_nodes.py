"""Camera Sketch workflow contract, profile status and CS* Comfy nodes."""

import json
import subprocess
import sys

import numpy as np
import pytest

from open_creative.comfy.bindings import (
    REQUIRED_BINDINGS, REQUIRED_OUTPUTS, TYPES, WORKFLOW_APP, BindingError, validate_manifest, validate_values,
)
from open_creative.comfy.profiles import (
    CONTROL_PRESETS, MOCK_PROFILE, OPEN_WEIGHTS_PROFILE, app_workflows_dir, profile_status,
)

CAMERA = ("camera_depth_v1", "camera_cinematic_v1")


def min_graph(workflow):
    """Minimal in-memory graph + manifest for a workflow (node ids are test-local)."""
    graph = {"1": {"inputs": {}}, "9": {"inputs": {}}}
    bindings = {}
    for name, kind in REQUIRED_BINDINGS[workflow].items():
        graph["1"]["inputs"][name] = None
        bindings[name] = {"node_id": "1", "input": name, "type": kind}
    outputs = {o: {"node_id": "9"} for o in REQUIRED_OUTPUTS[workflow]}
    return graph, {"workflow": workflow, "graph": f"{workflow}.api.json", "bindings": bindings, "outputs": outputs}


def write_camera(d):
    for name in CAMERA:
        g, m = min_graph(name)
        (d / f"{name}.api.json").write_text(json.dumps(g))
        (d / f"{name}.bindings.json").write_text(json.dumps(m))


def test_camera_contract_values():
    assert REQUIRED_BINDINGS["camera_depth_v1"] == {"image": "image"}
    assert REQUIRED_OUTPUTS["camera_depth_v1"] == ("depth",)
    assert REQUIRED_BINDINGS["camera_cinematic_v1"] == {
        "guide_rgb": "video", "guide_depth": "video", "reference": "image", "prompt": "string", "seed": "int",
        "width": "int", "height": "int", "frames": "int", "control_strength": "float"}
    assert REQUIRED_OUTPUTS["camera_cinematic_v1"] == ("video",)
    assert "video" in TYPES
    assert all(WORKFLOW_APP[n] == "camera-sketch" for n in CAMERA)
    assert WORKFLOW_APP["product_segment_v1"] == "product-lock"


@pytest.mark.parametrize("name", CAMERA)
def test_camera_manifests_validate_against_min_graph(name):
    g, m = min_graph(name)
    validate_manifest(g, m)
    m["bindings"].pop(next(iter(m["bindings"])))
    with pytest.raises(BindingError):
        validate_manifest(g, m)


def test_video_binding_type_enforced():
    g, m = min_graph("camera_cinematic_v1")
    m["bindings"]["guide_rgb"]["type"] = "image"
    with pytest.raises(BindingError, match="must be video"):
        validate_manifest(g, m)
    g, m = min_graph("camera_cinematic_v1")
    vals = {"guide_rgb": "a.mp4", "guide_depth": "b.mp4", "reference": "r.png", "prompt": "p", "seed": 1,
            "width": 64, "height": 64, "frames": 8, "control_strength": 0.5}
    validate_values(m, vals)
    with pytest.raises(BindingError):
        validate_values(m, {**vals, "guide_rgb": 3})


def test_open_weights_camera_unavailable_until_presets_calibrated(tmp_path):
    assert CONTROL_PRESETS == {"low": None, "medium": None, "high": None}
    write_camera(tmp_path)
    st = profile_status(OPEN_WEIGHTS_PROFILE, tmp_path, "http://127.0.0.1:8189", workflows=CAMERA)
    assert not st.available and st.reason == "control presets not calibrated"
    st = profile_status(MOCK_PROFILE, tmp_path, None, workflows=CAMERA)
    assert st.available and st.is_mock


def test_open_weights_camera_available_when_calibrated(tmp_path, monkeypatch):
    from open_creative.comfy import profiles

    write_camera(tmp_path)
    monkeypatch.setattr(profiles, "CONTROL_PRESETS", {"low": 0.4, "medium": 0.6, "high": 0.8})
    st = profile_status(OPEN_WEIGHTS_PROFILE, tmp_path, "http://x", workflows=CAMERA)
    assert st.available, st.reason
    (tmp_path / "camera_cinematic_v1.bindings.json").unlink()
    st = profile_status(OPEN_WEIGHTS_PROFILE, tmp_path, "http://x", workflows=CAMERA)
    assert not st.available and "camera_cinematic_v1" in st.reason


def test_product_lock_status_unaffected_by_camera(tmp_path):
    for name in ("product_segment_v1", "product_background_image_v1", "product_background_video_v1"):
        g, m = min_graph(name)
        (tmp_path / f"{name}.api.json").write_text(json.dumps(g))
        (tmp_path / f"{name}.bindings.json").write_text(json.dumps(m))
    st = profile_status(OPEN_WEIGHTS_PROFILE, tmp_path, "http://x")
    assert st.available, st.reason
    st = profile_status(OPEN_WEIGHTS_PROFILE, tmp_path, "http://x", workflows=(
        "product_segment_v1", "product_background_image_v1", "product_background_video_v1"))
    assert st.available


def test_camera_app_module_declares_workflows_and_reports_unavailable(tmp_path):
    from oc_api.apps.camera_sketch import MODULE

    assert MODULE.workflows == CAMERA
    write_camera(tmp_path)
    st = profile_status(OPEN_WEIGHTS_PROFILE, tmp_path, "http://x", workflows=MODULE.workflows)
    assert not st.available and "calibrated" in st.reason
    assert app_workflows_dir(tmp_path, "camera_depth_v1") == tmp_path  # flat layout accepted


def test_model_manifest_camera():
    from pathlib import Path

    p = Path(__file__).resolve().parents[3] / "models" / "camera-sketch.manifest.json"
    m = json.loads(p.read_text())
    assert m["app"] == "camera-sketch"
    roles = {x["role"]: x for x in m["models"]}
    assert "Depth Anything V2 Small" in roles["depth"]["id"] and roles["depth"]["license"] == "Apache-2.0"
    assert "LTX-2.3" in roles["cinematic"]["id"] and "union" in roles["control_lora"]["id"].lower()
    for x in m["models"]:
        assert x["verified"] is False
        assert all(f["sha256"] is None for f in x["files"])


def test_node_mappings_import_without_torch():
    code = ("import sys; sys.modules['torch'] = None\n"
            "from open_creative.comfy_nodes import NODE_CLASS_MAPPINGS as M\n"
            "assert {'CSDepthToScene','CSCompileCameraPath','CSRenderGuides','CSPathDiagnostics',"
            "'CSGeneratedMotionReport'} <= set(M)\n"
            "assert 'torch' not in [k for k, v in sys.modules.items() if v is not None]\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_cs_nodes_contract():
    from open_creative.comfy_nodes import NODE_CLASS_MAPPINGS

    for n in ("CSDepthToScene", "CSCompileCameraPath", "CSRenderGuides", "CSPathDiagnostics",
              "CSGeneratedMotionReport"):
        cls = NODE_CLASS_MAPPINGS[n]
        assert "required" in cls.INPUT_TYPES()
        assert len(cls.RETURN_TYPES) == len(cls.RETURN_NAMES)
        assert callable(getattr(cls(), cls.FUNCTION)) and cls.CATEGORY.startswith("open_creative")


def scene_arrays(w=96, h=64):
    rng = np.random.default_rng(3)
    img = rng.random((1, h, w, 3), dtype=np.float32)
    yy = np.linspace(0.2, 0.9, h, dtype=np.float32)[:, None] * np.ones((1, w), np.float32)
    depth = np.repeat(yy[None, :, :, None], 3, axis=3)  # near = bright, bottom nearest
    return img, depth


def test_cs_pipeline_depth_compile_render_diagnostics_qa():
    from open_creative.comfy_nodes import (
        CSCompileCameraPath, CSDepthToScene, CSGeneratedMotionReport, CSPathDiagnostics, CSRenderGuides,
    )

    img, raw = scene_arrays()
    depth, scene = CSDepthToScene().run(img, raw, 50.0, 0.5, 0.6)
    depth = np.asarray(depth)
    assert depth.shape == (1, 64, 96) and 0.0 <= depth.min() and depth.max() <= 1.0
    meta = json.loads(scene)
    assert meta["depth_png16"]["min"] < meta["depth_png16"]["max"] and meta["convention"]["kind"] == "inverse_relative"

    cfg = json.dumps({"preset": "push_in", "sketch_points": []})
    path, status = CSCompileCameraPath().run(scene, cfg, 64, 36, 24)
    assert status in ("ok", "warning"), (status, path[:300])
    p = json.loads(path)
    assert len(p["samples"]) == 24

    diag, dstatus = CSPathDiagnostics().run(path)
    assert dstatus == status and "hole_ratio" in json.loads(diag)

    rgb, gdepth, manifest = CSRenderGuides().run(img, depth, scene, path, 4)
    rgb, gdepth = np.asarray(rgb), np.asarray(gdepth)
    assert rgb.shape == (24, 36, 64, 3) and gdepth.shape == (24, 36, 64, 3)
    assert rgb.dtype == np.float32 and 0 <= rgb.min() and rgb.max() <= 1
    assert np.allclose(gdepth[..., 0], gdepth[..., 1])  # depth guide is gray, separate range/output
    m = json.loads(manifest)
    assert m["frames"] == 24 and m["depth_guide"]["near"] < m["depth_guide"]["far"]

    (report,) = CSGeneratedMotionReport().run(rgb, rgb, 32.0, 18.0)
    r = json.loads(report)
    assert r["status"] in ("ok", "warning", "unknown", "fail")


def test_cs_compile_invalid_returns_status_not_exception():
    from open_creative.comfy_nodes import CSCompileCameraPath, CSDepthToScene

    img, raw = scene_arrays()
    _, scene = CSDepthToScene().run(img, raw, 50.0, 0.5, 0.6)
    path, status = CSCompileCameraPath().run(scene, json.dumps({"preset": "nope"}), 64, 36, 24)
    assert status == "invalid"


def test_render_guides_prealloc_and_frame_cap():
    from open_creative.comfy_nodes import CS_MAX_FRAMES, CSCompileCameraPath, CSDepthToScene, CSRenderGuides

    assert CS_MAX_FRAMES == 121
    assert CSCompileCameraPath.INPUT_TYPES()["required"]["frames"][1]["max"] == 121
    img, raw = scene_arrays()
    depth, scene = CSDepthToScene().run(img, raw, 50.0, 0.5, 0.6)
    path, _ = CSCompileCameraPath().run(scene, json.dumps({"preset": "push_in", "sketch_points": []}), 64, 36, 8)
    rgb, gd, m = CSRenderGuides().run(img, depth, scene, path, 4)
    assert np.asarray(rgb).shape == (8, 36, 64, 3) and np.asarray(gd).shape == (8, 36, 64, 3)
    assert np.asarray(rgb).dtype == np.float32 and json.loads(m)["frames"] == 8
    p = json.loads(path)
    p["samples"] = p["samples"] * 16  # 128 > 121
    with pytest.raises(ValueError, match="121"):
        CSRenderGuides().run(img, depth, scene, json.dumps(p), 4)
