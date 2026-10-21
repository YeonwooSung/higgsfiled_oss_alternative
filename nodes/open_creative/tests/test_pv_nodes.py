"""Pocket VFX workflow contract (vfx_masks_v1), profile status, model manifest and PV* Comfy nodes."""

import io
import json
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from open_creative.comfy.bindings import (
    REQUIRED_BINDINGS,
    REQUIRED_OUTPUTS,
    WORKFLOW_APP,
)
from open_creative.comfy.profiles import (
    MOCK_PROFILE,
    OPEN_WEIGHTS_PROFILE,
    VFX_WORKFLOWS,
    app_workflows_dir,
    profile_status,
)
from open_creative.pocket_vfx.masks import build_prompts, read_masks_zip
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
PV_NODES = ("PVTrackAnchor", "PVSubjectMasks", "PVProceduralEffect", "PVTrackedComposite", "PVOutsideROIIntegrity",
            "PVPlateMatte")


def test_vfx_masks_contract():
    assert REQUIRED_BINDINGS["vfx_masks_v1"] == {"frames": "video", "prompts": "json", "width": "int", "height": "int"}
    assert REQUIRED_OUTPUTS["vfx_masks_v1"] == ("masks",)
    assert WORKFLOW_APP["vfx_masks_v1"] == WORKFLOW_APP["vfx_plate_v1"] == "pocket-vfx"
    assert WORKFLOW_APP["product_segment_v1"] == "product-lock" and WORKFLOW_APP["camera_depth_v1"] == "camera-sketch"
    assert app_workflows_dir(ROOT / "workflows", "vfx_masks_v1") == ROOT / "workflows" / "pocket-vfx"


def test_open_weights_vfx_unavailable_until_graphs_exist():
    d = app_workflows_dir(ROOT / "workflows", "vfx_masks_v1")
    st = profile_status(OPEN_WEIGHTS_PROFILE, d, "http://127.0.0.1:8189", workflows=VFX_WORKFLOWS)
    assert not st.available and st.reason == "vfx_masks_v1: binding manifest not exported"
    st = profile_status(OPEN_WEIGHTS_PROFILE, d, "http://x", workflows=("vfx_masks_v1",))
    assert not st.available and "vfx_masks_v1" in st.reason
    assert profile_status(MOCK_PROFILE, d, None, workflows=VFX_WORKFLOWS).available


def min_graph(workflow):
    graph = {"1": {"inputs": dict.fromkeys(REQUIRED_BINDINGS[workflow])}, "9": {"inputs": {}}}
    bindings = {name: {"node_id": "1", "input": name, "type": kind} for name, kind in REQUIRED_BINDINGS[workflow].items()}
    return graph, {"workflow": workflow, "graph": f"{workflow}.api.json", "bindings": bindings,
                   "outputs": {o: {"node_id": "9"} for o in REQUIRED_OUTPUTS[workflow]}}


def test_vfx_masks_manifest_validates_and_product_lock_selection_excludes_vfx(tmp_path):
    from open_creative.comfy.bindings import validate_manifest

    validate_manifest(*min_graph("vfx_masks_v1"))
    for name in ("product_segment_v1", "product_background_image_v1", "product_background_video_v1"):
        g, m = min_graph(name)
        (tmp_path / f"{name}.api.json").write_text(json.dumps(g))
        (tmp_path / f"{name}.bindings.json").write_text(json.dumps(m))
    assert profile_status(OPEN_WEIGHTS_PROFILE, tmp_path, "http://x").available  # no vfx graph needed


def test_model_manifest_pocket_vfx():
    m = json.loads((ROOT / "models" / "pocket-vfx.manifest.json").read_text())
    assert m["app"] == "pocket-vfx" and m["profile"] == "open_weights_v1"
    roles = {x["role"]: x for x in m["models"]}
    assert "sam2.1" in roles["subject_masks"]["id"].lower() and "small" in roles["subject_masks"]["id"].lower()
    assert roles["subject_masks"]["license"] == "Apache-2.0"
    assert "LTX-2.3" in roles["effect_plate"]["id"] and "v1.1" in roles["effect_plate"]["id"]
    assert "community" in roles["effect_plate"]["license"].lower()
    assert "gemma" in roles["text_encoder"]["id"].lower() and roles["text_encoder"]["license"].startswith("Gemma")
    for x in m["models"]:
        assert x["verified"] is False and all(f["sha256"] is None for f in x["files"])


def test_pv_nodes_import_without_torch():
    code = ("import sys; sys.modules['torch'] = None\n"
            "from open_creative.comfy_nodes import NODE_CLASS_MAPPINGS as M, NODE_DISPLAY_NAME_MAPPINGS as D\n"
            f"names = {PV_NODES!r}\n"
            "assert set(names) <= set(M) and set(names) <= set(D)\n"
            "assert 'torch' not in [k for k, v in sys.modules.items() if v is not None]\n"
            "assert 'sam2' not in sys.modules\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


def test_pv_nodes_contract():
    from open_creative.comfy_nodes import NODE_CLASS_MAPPINGS

    for n in PV_NODES:
        cls = NODE_CLASS_MAPPINGS[n]
        assert "required" in cls.INPUT_TYPES()
        assert len(cls.RETURN_TYPES) == len(cls.RETURN_NAMES)
        assert callable(getattr(cls(), cls.FUNCTION)) and cls.CATEGORY == "open_creative/pocket_vfx"


# ---- numeric wrappers ------------------------------------------------------------------------------------------

W, H = 320, 180


def textured_frames(n=12, step=3):
    """A textured square moving right by ``step`` px/frame over a smooth background (IMAGE batch, float 0..1)."""
    rng = np.random.default_rng(1)
    tex = rng.integers(0, 255, (40, 40, 3), dtype=np.uint8)
    out = np.empty((n, H, W, 3), np.float32)
    for i in range(n):
        f = np.full((H, W, 3), 90, np.uint8)
        x = 60 + step * i
        f[70:110, x : x + 40] = tex
        out[i] = f / 255.0
    return out


def test_track_anchor_follows_and_static():
    from open_creative.comfy_nodes import PVTrackAnchor

    frames = textured_frames()
    anchor = {"frame": 0, "x": 80 / W, "y": 90 / H, "follow": True, "track_window_frames": None}
    (doc,) = PVTrackAnchor().run(frames, json.dumps(anchor), "[]", 1280, 720)
    t = json.loads(doc)
    p = t["points"][10]
    assert p["valid"] and abs(p["x"] * W - (80 + 30)) < 1.5 and abs(p["y"] * H - 90) < 1.5
    (doc,) = PVTrackAnchor().run(frames, json.dumps({**anchor, "follow": False}), "[]", 1280, 720)
    assert {(q["x"], q["y"]) for q in json.loads(doc)["points"]} == {(anchor["x"], anchor["y"])}


def test_procedural_effect_node():
    from open_creative.comfy_nodes import PVProceduralEffect

    params = json.dumps({"scale": 1.0, "intensity": 0.8, "palette": "amber", "rotation_deg": 0, "squash": 0.5})
    rgb, alpha = PVProceduralEffect().run("impact_wave_v1", params, json.dumps({"start": 2, "peak": 4, "end": 10}),
                                          4, 160.0, 120.0, W, H, 7)
    assert rgb.shape == (1, H, W, 3) and alpha.shape == (1, H, W) and alpha[0, 120, 160] > 0.3
    _, none = PVProceduralEffect().run("impact_wave_v1", params, json.dumps({"start": 2, "peak": 4, "end": 10}),
                                       0, 160.0, 120.0, W, H, 7)
    assert not np.asarray(none).any()  # before the window: an empty layer


def test_tracked_composite_occludes_and_integrity_reports():
    from open_creative.comfy_nodes import PVOutsideROIIntegrity, PVTrackedComposite

    n = 6
    src = np.full((n, H, W, 3), 89 / 255, np.float32)
    track = {"points": [{"frame": i, "x": 0.5, "y": 0.6, "valid": i != 3} for i in range(n)]}
    mask = np.zeros((n, H // 2, W // 2), np.float32)  # proxy-size masks are upscaled to the source
    cv2.rectangle(mask[0], (70, 40), (90, 70), 1.0, -1)
    mask[:] = mask[0]
    cfg = {"preset_id": "impact_wave_v1", "window": {"start": 0, "peak": 1, "end": 5}, "layer_order": "behind_subject",
           "seed": 3, "params": {"scale": 1.0, "intensity": 0.9, "palette": "amber", "rotation_deg": 0, "squash": 0.5}}
    frames, roi, manifest = PVTrackedComposite().run(src, json.dumps(track), json.dumps(cfg), mask)
    assert frames.shape == src.shape and roi.shape == (n, H, W)
    m = json.loads(manifest)
    assert m["frames"] == n and m["roi_px"][3] == 0 and m["roi_px"][1] > 0  # frame 3 invalid -> hidden
    core = slice(84, 136), slice(144, 176)  # inside the (upscaled) subject rectangle
    assert np.array_equal(np.rint(frames[1][core] * 255), np.rint(src[1][core] * 255))
    (qa,) = PVOutsideROIIntegrity().run(src, frames, roi, mask)
    q = json.loads(qa)
    assert q["integrity"]["status"] == "pass" and q["occlusion_qa"] == 0.0
    leaked = np.array(frames)
    leaked[2, 0, 0] += 0.05  # outside the ROI
    q = json.loads(PVOutsideROIIntegrity().run(src, leaked, roi)[0])
    assert q["integrity"]["status"] == "fail" and q["occlusion_qa"] is None


# ---- PVSubjectMasks with a stand-in predictor (SAM 2 is not installed here) -----------------------------------


def test_subject_masks_needs_sam2():
    from open_creative.comfy_nodes import PVSubjectMasks

    prompts = build_prompts([{"frame": 0, "points": [{"x": 0.5, "y": 0.5, "label": 1}]}], {}, 32, 18, 2)
    with pytest.raises(RuntimeError, match="SAM 2"):
        PVSubjectMasks().run(np.zeros((2, 18, 32, 3), np.float32), json.dumps(prompts), 32, 18)


class FakePredictor:
    """Records the calls the node makes; masks are a disk at the last prompt point (logits > 0 inside)."""

    def __init__(self, log):
        self.log = log

    def init_state(self, video_path, **kw):
        files = sorted(Path(video_path).iterdir())
        state = {"n": len(files), "size": Image.open(files[0]).size, "point": None, "id": object()}
        self.log.append(("init_state", len(files), kw.get("offload_video_to_cpu")))
        return state

    def add_new_points_or_box(self, inference_state, frame_idx, obj_id, points, labels):
        inference_state["point"] = tuple(np.asarray(points)[0])
        self.log.append(("points", frame_idx, obj_id, np.asarray(points).tolist(), np.asarray(labels).tolist()))

    def add_new_mask(self, inference_state, frame_idx, obj_id, mask):
        self.log.append(("mask", frame_idx, obj_id, int(np.asarray(mask).sum())))

    def propagate_in_video(self, inference_state, reverse=False):
        self.log.append(("propagate", reverse, id(inference_state["id"])))
        w, h = inference_state["size"]
        frames = range(inference_state["n"] - 1, -1, -1) if reverse else range(inference_state["n"])
        for f in frames:
            logits = np.full((1, 1, h, w), -1.0, np.float32)
            x, y = inference_state["point"]
            cv2.circle(logits[0, 0], (int(x), int(y)), 4, 5.0, -1)
            yield f, [1], logits

    def reset_state(self, inference_state):
        self.log.append(("reset",))


def test_subject_masks_video_predictor_per_call_state(tmp_path, monkeypatch):
    from open_creative import comfy_nodes

    log, built = [], []

    def build_sam2_video_predictor(cfg, ckpt, **kw):
        built.append((cfg, ckpt))
        return FakePredictor(log)

    sam2 = types.ModuleType("sam2")
    build = types.ModuleType("sam2.build_sam")
    build.build_sam2_video_predictor = build_sam2_video_predictor
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_full_path = lambda kind, name: f"/models/{kind}/{name}"
    folder_paths.get_output_directory = lambda: str(tmp_path)
    monkeypatch.setitem(sys.modules, "sam2", sam2)
    monkeypatch.setitem(sys.modules, "sam2.build_sam", build)
    monkeypatch.setitem(sys.modules, "folder_paths", folder_paths)

    n, pw, ph = 5, 32, 18
    edit = np.zeros((ph, pw), np.uint8)
    edit[2:6, 2:6] = 255
    prompts = build_prompts([{"frame": 2, "points": [{"x": 0.5, "y": 0.5, "label": 1}]}], {4: edit}, pw, ph, n)
    frames = np.zeros((n, ph, pw, 3), np.float32)
    node = comfy_nodes.PVSubjectMasks()
    out1 = node.run(frames, json.dumps(prompts), pw, ph)
    out2 = node.run(frames, json.dumps(prompts), pw, ph)
    masks, manifest = out1["result"]
    assert np.asarray(masks).shape == (n, ph, pw) and np.asarray(masks)[0, 9, 16] == 1.0
    assert json.loads(manifest)["model"] == "sam2.1_hiera_small" and len(json.loads(manifest)["frames"]) == n
    assert built[0] == ("configs/sam2.1/sam2.1_hiera_s.yaml", "/models/sam2/sam2.1_hiera_small.pt")
    assert ("points", 2, 1, [[16.0, 9.0]], [1]) in log and ("mask", 4, 1, 16) in log
    props = [e for e in log if e[0] == "propagate"]
    assert [p[1] for p in props] == [False, True, False, True]  # forward, then reverse, per call
    assert props[0][2] != props[2][2]  # a fresh video state per call (never shared)
    assert sum(1 for e in log if e[0] == "init_state") == 2 and log.count(("reset",)) == 2
    files = out1["ui"]["masks"]
    assert len(files) == 1 and files[0]["type"] == "output"
    seq = read_masks_zip(tmp_path / files[0]["subfolder"] / files[0]["filename"], expect_frames=n,
                         expect_size=(pw, ph))
    assert seq.masks[0][9, 16] == 255 and seq.model == "sam2.1_hiera_small"
    with zipfile.ZipFile(tmp_path / files[0]["subfolder"] / files[0]["filename"]) as z:
        assert Image.open(io.BytesIO(z.read("mask_0000.png"))).mode == "L"
    assert out2["ui"]["masks"][0]["filename"] != files[0]["filename"]


def _fake_sam2(monkeypatch, tmp_path, full_path):
    built = []
    sam2 = types.ModuleType("sam2")
    build = types.ModuleType("sam2.build_sam")
    build.build_sam2_video_predictor = lambda cfg, ckpt, **kw: built.append(ckpt)
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_full_path = full_path
    folder_paths.get_output_directory = lambda: str(tmp_path)
    monkeypatch.setitem(sys.modules, "sam2", sam2)
    monkeypatch.setitem(sys.modules, "sam2.build_sam", build)
    monkeypatch.setitem(sys.modules, "folder_paths", folder_paths)
    return built


@pytest.mark.parametrize("full_path", [lambda kind, name: None, lambda kind, name: (_ for _ in ()).throw(KeyError(kind))],
                         ids=["missing_file", "unregistered_folder"])
def test_subject_masks_refuses_a_missing_checkpoint(tmp_path, monkeypatch, full_path):
    """Comfy's get_full_path returns None for a missing file: building SAM 2 with ckpt=None would run random weights
    and 'succeed' with garbage masks, so the node must fail before building the predictor."""
    from open_creative.comfy_nodes import PVSubjectMasks

    built = _fake_sam2(monkeypatch, tmp_path, full_path)
    prompts = build_prompts([{"frame": 0, "points": [{"x": 0.5, "y": 0.5, "label": 1}]}], {}, 32, 18, 2)
    with pytest.raises(RuntimeError, match=r"sam2\.1_hiera_small\.pt not found in models/sam2"):
        PVSubjectMasks().run(np.zeros((2, 18, 32, 3), np.float32), json.dumps(prompts), 32, 18)
    assert built == []


def test_sam2_model_folder_is_registered_inside_comfy(monkeypatch):
    from open_creative import comfy_nodes

    calls = []
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.models_dir = "/comfy/models"
    folder_paths.add_model_folder_path = lambda name, path: calls.append((name, path))
    monkeypatch.setitem(sys.modules, "folder_paths", folder_paths)
    comfy_nodes._register_sam2_folder()
    assert calls == [("sam2", "/comfy/models/sam2")]


def test_pv_plate_matte_node():
    from open_creative.comfy_nodes import PVPlateMatte

    plate = np.zeros((4, 32, 48, 3), np.float32)
    plate[:, 8:20, 10:30] = 0.9
    rgb, alpha = PVPlateMatte().run(plate, 0.06, 0.04)
    assert rgb.shape == (4, 32, 48, 3) and alpha.shape == (4, 32, 48)
    assert alpha[:, 12, 20].min() > 0.99 and alpha[:, 0, 0].max() == 0.0
