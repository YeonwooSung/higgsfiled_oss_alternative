"""Product Lock custom nodes for ComfyUI (new nodes; none of these exist upstream).

Conventions: IMAGE is [B,H,W,C] float 0..1 (RGB only — alpha travels as a separate MASK,
never as a 4th IMAGE channel); MASK is [B,H,W] float 0..1. Comfy's LoadImage emits its
MASK as 1 - alpha, hence `mask_is_inverted`. The math lives in `open_creative.product_lock`
so the CPU worker and these nodes produce identical results.
"""

from __future__ import annotations

import json

import numpy as np

from .product_lock.composite import LayerConfig, composite_sequence
from .product_lock.geometry import Placement, output_size, place_product
from .product_lock.integrity import CoreChecker
from .product_lock.locked import LockedProduct, build_locked_product
from .product_lock.masks import compute_core
from .product_lock.prompts import build_background_prompt, load_presets

CATEGORY = "open_creative/product_lock"


def _np(x) -> np.ndarray:
    if hasattr(x, "detach"):
        x = x.detach().cpu().numpy()
    return np.asarray(x, dtype=np.float32)


def _out(arr: np.ndarray):
    try:
        import torch

        return torch.from_numpy(np.ascontiguousarray(arr))
    except ImportError:
        return arr


def _u8(img: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(img * 255.0), 0, 255).astype(np.uint8)


def _locked(product, alpha, core, placement_json: str) -> LockedProduct:
    p = json.loads(placement_json)
    return LockedProduct(rgb=_u8(_np(product)[0]), alpha=_np(alpha)[0], core=_np(core)[0] >= 0.5,
                         placement=Placement(p["x0"], p["y0"], p["w"], p["h"]))


class PLProductMask:
    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("MASK", "STRING")
    RETURN_NAMES = ("mask", "diagnostics")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"image": ("IMAGE",), "prompts": ("STRING", {"default": "{}", "multiline": True})},
            "optional": {"mask": ("MASK",), "mask_is_inverted": ("BOOLEAN", {"default": True})},
        }

    def run(self, image, prompts, mask=None, mask_is_inverted=True):
        if mask is not None:
            m = _np(mask)
            m = 1.0 - m if mask_is_inverted else m
            return _out(m.astype(np.float32)), json.dumps({"source": "alpha", "coverage": float((m >= 0.5).mean())})
        return self._sam2(_np(image), json.loads(prompts or "{}"))

    def _sam2(self, image: np.ndarray, prompts: dict):
        try:
            from sam2.build_sam import build_sam2
            from sam2.sam2_image_predictor import SAM2ImagePredictor
        except ImportError as e:
            raise RuntimeError("SAM 2 is not installed in this Comfy build; provide an alpha mask instead") from e
        import folder_paths  # Comfy runtime module

        ckpt = folder_paths.get_full_path("sam2", "sam2.1_hiera_small.pt")
        predictor = SAM2ImagePredictor(build_sam2("configs/sam2.1/sam2.1_hiera_s.yaml", ckpt))
        rgb = _u8(image[0])
        h, w = rgb.shape[:2]
        predictor.set_image(rgb)
        pts = prompts.get("points") or []
        coords = np.array([[p["x"] * w, p["y"] * h] for p in pts], np.float32) if pts else None
        labels = np.array([p.get("label", 1) for p in pts], np.int32) if pts else None
        box = prompts.get("box")
        box_xyxy = (np.array([box["x"] * w, box["y"] * h, (box["x"] + box["w"]) * w, (box["y"] + box["h"]) * h],
                             np.float32) if box else None)
        masks, scores, _ = predictor.predict(point_coords=coords, point_labels=labels, box=box_xyxy,
                                             multimask_output=False)
        m = masks[0].astype(np.float32)[None]
        return _out(m), json.dumps({"source": "sam2.1_small", "score": float(scores[0]),
                                    "coverage": float(m.mean())})


class PLLockedProduct:
    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("IMAGE", "MASK", "MASK", "STRING")
    RETURN_NAMES = ("product", "alpha", "core", "placement")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",), "mask": ("MASK",),
                "transform": ("STRING", {"default": '{"center_x":0.5,"center_y":0.55,"width_ratio":0.36}'}),
                "aspect": (["9:16", "16:9"],),
                "feather_px": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 4.0, "step": 0.5}),
            },
            "optional": {"core_mask": ("MASK",)},
        }

    def run(self, image, mask, transform, aspect, feather_px, core_mask=None):
        rgb = _u8(_np(image)[0])
        m = _np(mask)[0]
        user = (_np(core_mask)[0] >= 0.5) if core_mask is not None else None
        t = json.loads(transform)
        w, h = output_size(aspect)
        placement = place_product(rgb.shape[1], rgb.shape[0], w, h, t["center_x"], t["center_y"],
                                  t["width_ratio"], t.get("rotation_deg", 0))
        locked = build_locked_product(rgb, m, compute_core(m, user), placement, feather_px)
        return (_out(locked.rgb[None].astype(np.float32) / 255.0), _out(locked.alpha[None]),
                _out(locked.core[None].astype(np.float32)), json.dumps(placement.__dict__))


class PLBackgroundPrompt:
    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("prompt", "preset_version")

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "preset_id": (sorted(load_presets()),),
            "accent_hex": ("STRING", {"default": "#965EFF"}),
            "composition": (["center"],),
            "extra_prompt": ("STRING", {"default": "", "multiline": True}),
        }}

    def run(self, preset_id, accent_hex, composition, extra_prompt):
        return build_background_prompt(load_presets()[preset_id], accent_hex, composition, extra_prompt)


class PLCompositeSequence:
    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("frames", "manifest")

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "background": ("IMAGE",), "product": ("IMAGE",), "alpha": ("MASK",), "core": ("MASK",),
            "placement": ("STRING", {"forceInput": True}),
            "shadow_opacity": ("FLOAT", {"default": 0.28, "min": 0.0, "max": 0.6, "step": 0.01}),
            "shadow_blur_px": ("FLOAT", {"default": 18.0, "min": 0.0, "max": 40.0, "step": 1.0}),
            "background_brightness": ("FLOAT", {"default": 0.0, "min": -0.2, "max": 0.2, "step": 0.01}),
            "particles": (["none", "bokeh", "neon"],),
            "seed": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
        }}

    def run(self, background, product, alpha, core, placement, shadow_opacity, shadow_blur_px,
            background_brightness, particles, seed):
        locked = _locked(product, alpha, core, placement)
        bg = _np(background)
        cfg = LayerConfig(brightness=background_brightness, shadow_enabled=shadow_opacity > 0,
                          shadow_opacity=shadow_opacity, shadow_blur_px=shadow_blur_px, particles=particles,
                          seed=seed)
        out = np.empty_like(bg)
        for i, frame in enumerate(composite_sequence((_u8(f) for f in bg), locked, cfg)):
            out[i] = frame.astype(np.float32) / 255.0
        manifest = {"frames": int(bg.shape[0]), "width": int(bg.shape[2]), "height": int(bg.shape[1]),
                    "placement": json.loads(placement), "particles": particles, "seed": seed}
        return _out(out), json.dumps(manifest)


class PLCoreIntegrityCheck:
    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("qa",)
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"frames": ("IMAGE",), "product": ("IMAGE",), "alpha": ("MASK",), "core": ("MASK",),
                             "placement": ("STRING", {"forceInput": True})}}

    def run(self, frames, product, alpha, core, placement):
        locked = _locked(product, alpha, core, placement)
        f = _np(frames)
        checker = CoreChecker(locked, f.shape[2], f.shape[1])
        for i in range(f.shape[0]):
            checker.check(_u8(f[i]), i)
        return (json.dumps(checker.report()),)


NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in
                       (PLProductMask, PLLockedProduct, PLBackgroundPrompt, PLCompositeSequence, PLCoreIntegrityCheck)}
NODE_DISPLAY_NAME_MAPPINGS = {
    "PLProductMask": "Product Lock · Product Mask",
    "PLLockedProduct": "Product Lock · Locked Product",
    "PLBackgroundPrompt": "Product Lock · Background Prompt",
    "PLCompositeSequence": "Product Lock · Composite Sequence",
    "PLCoreIntegrityCheck": "Product Lock · Core Integrity Check",
}
