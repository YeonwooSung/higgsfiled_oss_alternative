"""Node wrappers are exercised with numpy arrays shaped like Comfy tensors (IMAGE=[B,H,W,C] 0..1, MASK=[B,H,W])."""

import json

import numpy as np
import pytest

from open_creative.comfy_nodes import (
    NODE_CLASS_MAPPINGS,
    PLBackgroundPrompt,
    PLCompositeSequence,
    PLCoreIntegrityCheck,
    PLLockedProduct,
    PLProductMask,
)


def product():
    rgb = np.random.default_rng(0).random((1, 200, 120, 3), dtype=np.float32)
    alpha = np.zeros((1, 200, 120), np.float32)
    alpha[0, 20:180, 10:110] = 1.0
    return rgb, alpha


def test_all_five_nodes_registered_with_comfy_contract():
    pl = {k for k in NODE_CLASS_MAPPINGS if k.startswith("PL")}
    assert pl == {"PLProductMask", "PLLockedProduct", "PLBackgroundPrompt",
                  "PLCompositeSequence", "PLCoreIntegrityCheck"}
    for cls in NODE_CLASS_MAPPINGS.values():
        assert "required" in cls.INPUT_TYPES()
        assert len(cls.RETURN_TYPES) == len(cls.RETURN_NAMES)
        assert callable(getattr(cls(), cls.FUNCTION)) and cls.CATEGORY.startswith("open_creative")


def test_product_mask_from_loadimage_mask_is_inverted_alpha():
    rgb, alpha = product()
    mask, diag = PLProductMask().run(rgb, "{}", mask=1.0 - alpha, mask_is_inverted=True)
    assert np.array_equal(mask, alpha)
    assert json.loads(diag)["source"] == "alpha"


def test_product_mask_without_alpha_needs_sam2():
    rgb, _ = product()
    with pytest.raises(RuntimeError, match="SAM 2"):
        PLProductMask().run(rgb, json.dumps({"points": [{"x": 0.5, "y": 0.5, "label": 1}]}))


def test_background_prompt_node():
    prompt, version = PLBackgroundPrompt().run("neon_studio_v1", "#965EFF", "center", "")
    assert "purple" in prompt and version == "neon_studio_v1@1.0.0"


def test_locked_composite_and_check_pass_on_every_frame():
    rgb, alpha = product()
    locked_rgb, locked_alpha, core, placement = PLLockedProduct().run(
        rgb, alpha, json.dumps({"center_x": 0.5, "center_y": 0.55, "width_ratio": 0.4}), "9:16", 1.0)
    assert locked_rgb.shape[0] == 1 and core.sum() > 0
    bg = np.random.default_rng(1).random((3, 1280, 720, 3), dtype=np.float32)
    (frames, manifest) = PLCompositeSequence().run(bg, locked_rgb, locked_alpha, core, placement, 0.3, 18.0, 0.1,
                                                   "bokeh", 7)
    assert frames.shape == (3, 1280, 720, 3)
    assert json.loads(manifest)["frames"] == 3
    (qa,) = PLCoreIntegrityCheck().run(frames, locked_rgb, locked_alpha, core, placement)
    report = json.loads(qa)
    assert report["passed"] and report["frames_checked"] == 3
