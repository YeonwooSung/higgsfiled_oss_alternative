import pytest
from pydantic import ValidationError

from oc_api.product_lock.config import ProductLockConfig, default_config

SPEC_EXAMPLE = {
    "schema_version": 1,
    "product_asset_id": "7d3c2f2e-1d7e-4d55-9a37-0d3a1e1d2b11",
    "mask_asset_id": "7d3c2f2e-1d7e-4d55-9a37-0d3a1e1d2b12",
    "core_mask_asset_id": "7d3c2f2e-1d7e-4d55-9a37-0d3a1e1d2b13",
    "transform": {"center_x": 0.5, "center_y": 0.55, "width_ratio": 0.36, "rotation_deg": 0},
    "scene": {"preset_id": "neon_studio_v1", "accent_hex": "#965EFF", "extra_prompt": "",
              "background_asset_id": "7d3c2f2e-1d7e-4d55-9a37-0d3a1e1d2b14"},
    "shadow": {"enabled": True, "opacity": 0.28, "blur_px": 18},
    "motion": "subtle",
    "output": {"aspect": "9:16", "duration_ms": 5000, "fps": 24},
    "seed": 12345,
    "model_profile": "open_weights_v1",
}


def test_spec_example_validates_with_defaults_for_extensions():
    c = ProductLockConfig.model_validate(SPEC_EXAMPLE)
    assert c.transform.feather_px == 1.0
    assert c.lighting.background_brightness == 0.0
    assert c.protect_boxes == []


def test_default_config_is_valid_and_empty():
    c = default_config()
    assert c.product_asset_id is None and c.scene.preset_id == "soft_studio_v1"


@pytest.mark.parametrize(
    "path, value",
    [
        (("transform", "rotation_deg"), 5),
        (("transform", "width_ratio"), 0.71),
        (("transform", "width_ratio"), 0.14),
        (("transform", "feather_px"), 4.5),
        (("shadow", "opacity"), 0.61),
        (("shadow", "blur_px"), 41),
        (("scene", "accent_hex"), "965EFF"),
        (("scene", "extra_prompt"), "x" * 301),
        (("scene", "preset_id"), "unknown_v1"),
        (("output", "duration_ms"), 6000),
        (("output", "fps"), 30),
        (("output", "aspect"), "1:1"),
        (("motion",), "orbit"),
        (("schema_version",), 2),
    ],
)
def test_out_of_range_values_rejected(path, value):
    data = {**SPEC_EXAMPLE, "transform": dict(SPEC_EXAMPLE["transform"]), "scene": dict(SPEC_EXAMPLE["scene"]),
            "shadow": dict(SPEC_EXAMPLE["shadow"]), "output": dict(SPEC_EXAMPLE["output"])}
    target = data
    for k in path[:-1]:
        target = target[k]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        ProductLockConfig.model_validate(data)


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        ProductLockConfig.model_validate({**SPEC_EXAMPLE, "graph": {"1": {}}})


def test_protect_box_must_be_normalized():
    with pytest.raises(ValidationError):
        ProductLockConfig.model_validate({**SPEC_EXAMPLE, "protect_boxes": [{"x": 0.2, "y": 0.2, "w": 0, "h": 0.1}]})
    c = ProductLockConfig.model_validate({**SPEC_EXAMPLE, "protect_boxes": [{"x": 0.2, "y": 0.2, "w": 0.3, "h": 0.1}]})
    assert c.protect_boxes[0].w == 0.3
