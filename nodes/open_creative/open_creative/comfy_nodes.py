"""Product Lock (PL*) and Camera Sketch (CS*) custom nodes for ComfyUI (new nodes; none exist upstream).

Conventions: IMAGE is [B,H,W,C] float 0..1 (RGB only — alpha travels as a separate MASK,
never as a 4th IMAGE channel); MASK is [B,H,W] float 0..1. Comfy's LoadImage emits its
MASK as 1 - alpha, hence `mask_is_inverted`. The math lives in `open_creative.product_lock`
so the CPU worker and these nodes produce identical results.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np

from .camera_sketch.camera import intrinsics
from .camera_sketch.guides import DepthGuideRange, depth_guide
from .camera_sketch.path import SceneInfo, compile_path
from .camera_sketch.qa import motion_report
from .camera_sketch.render import hole_ratio, rasterize, render_sequence
from .camera_sketch.scene import (
    DAV2_SMALL, DepthConvention, build_mesh, decode_depth_png16, depth_to_z, encode_depth_png16, prepare_z,
)
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


# ---- Camera Sketch -------------------------------------------------------------------
# Thin wrappers over open_creative.camera_sketch. RGB IMAGE tensors are [B,H,W,3] float 0..1; depth is
# kept apart: a MASK [B,H,W] of inverse depth normalized to 0..1 by the (min, max) recorded in the `scene`
# JSON, and the depth *guide* is a separate gray IMAGE (near = white, shot-fixed range).

CS_CATEGORY = "open_creative/camera_sketch"
_SENSOR_W_MM = 36.0
CS_MAX_FRAMES = 121  # model length (LTX 121); also the cap for a batch guide render


def _frames_u8(x) -> np.ndarray:
    return _u8(_np(x))


def _scene_info(meta: dict) -> SceneInfo:
    w, h = int(meta["src_w"]), int(meta["src_h"])
    K = intrinsics(float(meta["focal_length_mm"]), w, h, float(meta.get("sensor_width_mm", _SENSOR_W_MM)))
    u, v = meta["target_uv01"]
    ray = np.linalg.inv(K) @ np.array([u * w, v * h, 1.0])
    c = meta["convention"]
    conv = DepthConvention(c["kind"], float(c.get("scale", 1.0)), float(c.get("offset", 0.0)),
                           float(c.get("eps", 1e-6)))
    return SceneInfo(K_src=K, src_w=w, src_h=h, target_world=ray, target_uv01=(float(u), float(v)),
                     depth_convention=conv, depth_params={"depth_scale": 1.0})


class CSDepthToScene:
    """Raw relative inverse depth (Depth Anything V2 Small, near = bright) -> 16-bit-quantized depth + scene JSON."""

    CATEGORY = CS_CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("MASK", "STRING")
    RETURN_NAMES = ("depth", "scene")

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",), "depth": ("IMAGE",),
            "focal_length_mm": ("FLOAT", {"default": 50.0, "min": 35.0, "max": 70.0, "step": 1.0}),
            "target_u": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.001}),
            "target_v": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.001}),
        }}

    def run(self, image, depth, focal_length_mm, target_u, target_v):
        h, w = _np(image).shape[1:3]
        d = _np(depth)[0]
        d = d.mean(axis=-1) if d.ndim == 3 else d
        data, dm = encode_depth_png16(d)
        q = decode_depth_png16(data, dm)  # exactly what the worker will decode
        span = dm["max"] - dm["min"]
        norm = ((q - dm["min"]) / span if span > 0 else np.zeros_like(q)).astype(np.float32)
        meta = {"src_w": int(w), "src_h": int(h), "focal_length_mm": float(focal_length_mm),
                "sensor_width_mm": _SENSOR_W_MM, "target_uv01": [float(target_u), float(target_v)],
                "convention": {"kind": DAV2_SMALL.kind, "scale": 1.0, "offset": 0.0, "eps": DAV2_SMALL.eps},
                "depth_png16": {"min": dm["min"], "max": dm["max"]}}
        return _out(norm[None]), json.dumps(meta)


class CSCompileCameraPath:
    CATEGORY = CS_CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("path", "status")

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "scene": ("STRING", {"forceInput": True}),
            "path_config": ("STRING", {"default": '{"preset":"push_in","sketch_points":[]}', "multiline": True}),
            "width": ("INT", {"default": 1280, "min": 64, "max": 4096}),
            "height": ("INT", {"default": 720, "min": 64, "max": 4096}),
            "frames": ("INT", {"default": 120, "min": 2, "max": CS_MAX_FRAMES}),
        }}

    def run(self, scene, path_config, width, height, frames):
        try:
            compiled = compile_path(json.loads(path_config), _scene_info(json.loads(scene)), width, height, frames)
        except (ValueError, TypeError, KeyError) as e:
            return json.dumps({"status": "invalid", "reasons": ["invalid_input"], "detail": str(e)[:200],
                               "samples": []}), "invalid"
        return json.dumps(compiled.to_dict()), compiled.status


class CSRenderGuides:
    """Batch node (Comfy IMAGE needs a full batch) that exists for the published workflow. Output tensors are
    preallocated once and filled in place. Production guides are rendered by the CPU worker, streaming."""

    CATEGORY = CS_CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("IMAGE", "IMAGE", "STRING")
    RETURN_NAMES = ("guide_rgb", "guide_depth", "manifest")

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",), "depth": ("MASK",), "scene": ("STRING", {"forceInput": True}),
            "path": ("STRING", {"forceInput": True}),
            "mesh_stride": ("INT", {"default": 4, "min": 1, "max": 16}),
        }}

    def run(self, image, depth, scene, path, mesh_stride):
        meta, p = json.loads(scene), json.loads(path)
        if p.get("status") == "invalid" or not p.get("samples"):
            raise ValueError("camera path is invalid; nothing to render")
        info = _scene_info(meta)
        lo, hi = meta["depth_png16"]["min"], meta["depth_png16"]["max"]
        d = lo + _np(depth)[0].astype(np.float64) * (hi - lo)
        z_norm, _ = prepare_z(depth_to_z(d, info.depth_convention), info.target_uv01)
        mesh = build_mesh(z_norm, info.K_src, mesh_stride)
        out_w, out_h = int(p["source_to_output"]["out_w"]), int(p["source_to_output"]["out_h"])
        compiled = SimpleNamespace(status=p["status"], samples=p["samples"], K=p["K"],
                                   source_to_output=p["source_to_output"])
        tex = _frames_u8(image)[0]
        n = len(p["samples"])
        if n > CS_MAX_FRAMES:
            raise ValueError(f"path has {n} frames; at most {CS_MAX_FRAMES} can be rendered as one batch")
        rgb = np.empty((n, out_h, out_w, 3), np.float32)
        gd = np.empty((n, out_h, out_w, 3), np.float32)
        K_out = np.asarray(p["K"], float)
        ends = [rasterize(mesh, None, K_out, np.asarray(p["samples"][i]["T_cw"], float), out_w, out_h,
                          texture=tex) for i in (0, n - 1)]
        rng, holes = DepthGuideRange.from_frames(*ends), []  # shot range: union of first and last frame
        for i, fr in enumerate(render_sequence(mesh, None, compiled, out_w, out_h, texture=tex)):
            g, fill = depth_guide(fr, rng)
            rgb[i] = fr.rgb.astype(np.float32) / 255.0
            gd[i] = (g.astype(np.float32) / 255.0)[..., None]
            holes.append(hole_ratio(fr))
        manifest = {"frames": n, "width": out_w, "height": out_h, "depth_guide": rng.to_dict(),
                    "fill": fill, "hole_ratio_max": float(max(holes)), "mesh_stride": int(mesh_stride),
                    "holes": "unfilled frames: uncovered pixels are black (0,0,0); stretched edge-layer "
                             "pixels keep their colour but are invalid (render.STRETCH_VALID)"}
        return _out(rgb), _out(gd), json.dumps(manifest)


class CSPathDiagnostics:
    CATEGORY = CS_CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("diagnostics", "status")
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"path": ("STRING", {"forceInput": True})}}

    def run(self, path):
        p = json.loads(path)
        diag = {**(p.get("diagnostics") or {}), "reasons": p.get("reasons", [])}
        return json.dumps(diag), p.get("status", "invalid")


class CSGeneratedMotionReport:
    CATEGORY = CS_CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("report",)
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "generated": ("IMAGE",), "geometry": ("IMAGE",),
            "target_x_px": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 8192.0}),
            "target_y_px": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 8192.0}),
        }}

    def run(self, generated, geometry, target_x_px, target_y_px):
        gen, geo = _frames_u8(generated), _frames_u8(geometry)
        return (json.dumps(motion_report(iter(gen), iter(geo), (target_x_px, target_y_px))),)


NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in
                       (PLProductMask, PLLockedProduct, PLBackgroundPrompt, PLCompositeSequence, PLCoreIntegrityCheck,
                        CSDepthToScene, CSCompileCameraPath, CSRenderGuides, CSPathDiagnostics,
                        CSGeneratedMotionReport)}
NODE_DISPLAY_NAME_MAPPINGS = {
    "PLProductMask": "Product Lock · Product Mask",
    "PLLockedProduct": "Product Lock · Locked Product",
    "PLBackgroundPrompt": "Product Lock · Background Prompt",
    "PLCompositeSequence": "Product Lock · Composite Sequence",
    "PLCoreIntegrityCheck": "Product Lock · Core Integrity Check",
    "CSDepthToScene": "Camera Sketch · Depth To Scene",
    "CSCompileCameraPath": "Camera Sketch · Compile Camera Path",
    "CSRenderGuides": "Camera Sketch · Render Guides",
    "CSPathDiagnostics": "Camera Sketch · Path Diagnostics",
    "CSGeneratedMotionReport": "Camera Sketch · Generated Motion Report",
}
