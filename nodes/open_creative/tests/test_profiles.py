import json

import pytest

from open_creative.comfy.profiles import (
    MOCK_PROFILE,
    OPEN_WEIGHTS_PROFILE,
    background_image_size,
    background_video_shape,
    get_profile,
    profile_status,
)

WORKFLOWS = {
    "product_segment_v1": ({"1": {"inputs": {"image": ""}}, "2": {"inputs": {"prompts": ""}}, "3": {"inputs": {}}},
                           {"image": ("1", "image", "image"), "prompts": ("2", "prompts", "json")}, {"mask": "3"}),
    "product_background_image_v1": ({"1": {"inputs": {"text": "", "seed": 0, "w": 0, "h": 0}}, "2": {"inputs": {}}},
                                    {"prompt": ("1", "text", "string"), "seed": ("1", "seed", "int"),
                                     "width": ("1", "w", "int"), "height": ("1", "h", "int")}, {"image": "2"}),
    "product_background_video_v1": ({"1": {"inputs": {"image": "", "text": "", "seed": 0, "w": 0, "h": 0, "n": 0}},
                                     "2": {"inputs": {}}},
                                    {"image": ("1", "image", "image"), "prompt": ("1", "text", "string"),
                                     "seed": ("1", "seed", "int"), "width": ("1", "w", "int"),
                                     "height": ("1", "h", "int"), "frames": ("1", "n", "int")}, {"video": "2"}),
}


def write_workflows(d, skip=None):
    for name, (graph, binds, outs) in WORKFLOWS.items():
        if name == skip:
            continue
        (d / f"{name}.api.json").write_text(json.dumps(graph))
        (d / f"{name}.bindings.json").write_text(json.dumps({
            "workflow": name, "graph": f"{name}.api.json",
            "bindings": {k: {"node_id": n, "input": f, "type": t} for k, (n, f, t) in binds.items()},
            "outputs": {k: {"node_id": n} for k, n in outs.items()},
        }))


def test_mock_profile_is_always_available_and_labelled(tmp_path):
    st = profile_status(MOCK_PROFILE, workflows_dir=tmp_path, comfy_base_url=None)
    assert st.available and st.is_mock and st.label == "Mock"


def test_open_weights_unavailable_without_exported_graphs(tmp_path):
    write_workflows(tmp_path, skip="product_background_video_v1")
    st = profile_status(OPEN_WEIGHTS_PROFILE, workflows_dir=tmp_path, comfy_base_url="http://127.0.0.1:8189")
    assert not st.available and "product_background_video_v1" in st.reason


def test_open_weights_unavailable_without_base_url(tmp_path):
    write_workflows(tmp_path)
    st = profile_status(OPEN_WEIGHTS_PROFILE, workflows_dir=tmp_path, comfy_base_url=None)
    assert not st.available and "COMFY_BASE_URL" in st.reason


def test_open_weights_available_with_valid_exports(tmp_path):
    write_workflows(tmp_path)
    st = profile_status(OPEN_WEIGHTS_PROFILE, workflows_dir=tmp_path, comfy_base_url="http://127.0.0.1:8189")
    assert st.available, st.reason
    assert not st.is_mock


def test_open_weights_rejects_manifest_pointing_to_missing_node(tmp_path):
    write_workflows(tmp_path)
    p = tmp_path / "product_segment_v1.bindings.json"
    m = json.loads(p.read_text())
    m["bindings"]["image"]["node_id"] = "42"
    p.write_text(json.dumps(m))
    st = profile_status(OPEN_WEIGHTS_PROFILE, workflows_dir=tmp_path, comfy_base_url="http://x")
    assert not st.available and "node 42" in st.reason


def test_unknown_profile():
    with pytest.raises(KeyError):
        get_profile("nope")


def test_model_input_sizes_follow_stride_rules():
    assert background_image_size("9:16") == (720, 1280)
    assert background_video_shape("9:16") == (736, 1280, 121)
    assert background_video_shape("16:9") == (1280, 736, 121)
