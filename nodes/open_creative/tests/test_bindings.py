import pytest

from open_creative.comfy.bindings import BindingError, apply_bindings, validate_manifest, validate_values

GRAPH = {
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["11", 0]}},
    "25": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
    "27": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "bg"}},
}
MANIFEST = {
    "workflow": "product_background_image_v1",
    "graph": "product_background_image_v1.api.json",
    "bindings": {
        "prompt": {"node_id": "6", "input": "text", "type": "string", "max_length": 2000},
        "seed": {"node_id": "25", "input": "noise_seed", "type": "int", "min": 0, "max": 2147483647},
        "width": {"node_id": "27", "input": "width", "type": "int", "min": 256, "max": 2048},
        "height": {"node_id": "27", "input": "height", "type": "int", "min": 256, "max": 2048},
    },
    "outputs": {"image": {"node_id": "9", "kind": "image"}},
}
VALUES = {"prompt": "studio", "seed": 1, "width": 720, "height": 1280}


def test_valid_manifest_passes():
    validate_manifest(GRAPH, MANIFEST)


def test_manifest_with_unknown_node_rejected():
    bad = {**MANIFEST, "bindings": {**MANIFEST["bindings"], "seed": {"node_id": "99", "input": "noise_seed", "type": "int"}}}
    with pytest.raises(BindingError, match="node 99"):
        validate_manifest(GRAPH, bad)


def test_manifest_with_unknown_field_rejected():
    bad = {**MANIFEST, "bindings": {**MANIFEST["bindings"], "seed": {"node_id": "25", "input": "seed", "type": "int"}}}
    with pytest.raises(BindingError, match="field seed"):
        validate_manifest(GRAPH, bad)


def test_manifest_missing_required_logical_binding_rejected():
    bad = {**MANIFEST, "bindings": {k: v for k, v in MANIFEST["bindings"].items() if k != "seed"}}
    with pytest.raises(BindingError, match="seed"):
        validate_manifest(GRAPH, bad)


def test_manifest_output_node_must_exist():
    bad = {**MANIFEST, "outputs": {"image": {"node_id": "77", "kind": "image"}}}
    with pytest.raises(BindingError):
        validate_manifest(GRAPH, bad)


@pytest.mark.parametrize("values", [
    {**VALUES, "seed": -1},
    {**VALUES, "width": 4096},
    {**VALUES, "prompt": "x" * 2001},
    {**VALUES, "seed": "1"},
    {k: v for k, v in VALUES.items() if k != "width"},
    {**VALUES, "graph": {}},
])
def test_invalid_values_rejected(values):
    with pytest.raises(BindingError):
        validate_values(MANIFEST, values)


def test_apply_bindings_substitutes_only_bound_inputs_without_mutating():
    out = apply_bindings(GRAPH, MANIFEST, VALUES)
    assert out["6"]["inputs"]["text"] == "studio"
    assert out["27"]["inputs"]["width"] == 720 and out["27"]["inputs"]["batch_size"] == 1
    assert out["6"]["inputs"]["clip"] == ["11", 0]
    assert GRAPH["6"]["inputs"]["text"] == ""
