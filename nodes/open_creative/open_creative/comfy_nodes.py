"""Product Lock (PL*), Camera Sketch (CS*) and Pocket VFX (PV*) custom nodes for ComfyUI (new nodes; none exist
upstream).

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
from .pocket_vfx import masks as pv_masks
from .pocket_vfx.composite import FrameInputs
from .pocket_vfx.composite import composite_frame as pv_composite_frame
from .pocket_vfx.integrity import IntegrityAccumulator, subject_core_cover
from .pocket_vfx.matte import MatteConfig, MatteStats, matte_qa, matte_sequence
from .pocket_vfx.presets import load_presets as load_pv_presets
from .pocket_vfx.procedural import render_effect
from .pocket_vfx.timeline import EffectWindow
from .pocket_vfx.track import (
    SMOOTH_MAX_SHIFT_PX, SMOOTH_SIGMA_FRAMES, anchor_positions, smooth_track, static_track, track_to_json,
    track_with_corrections,
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



SAM2_FOLDER = "sam2"  # models/sam2/ of the Comfy install (registered below; Comfy has no built-in "sam2" folder)


def _register_sam2_folder() -> None:
    try:
        import os

        import folder_paths  # Comfy runtime module; absent outside Comfy
    except ImportError:
        return
    try:
        folder_paths.add_model_folder_path(SAM2_FOLDER, os.path.join(folder_paths.models_dir, SAM2_FOLDER))
    except AttributeError:  # an old Comfy without add_model_folder_path / models_dir: _sam2_checkpoint reports it
        pass


_register_sam2_folder()


def _sam2_checkpoint(name: str) -> str:
    """Full path of a SAM 2 checkpoint in models/sam2. Comfy returns None for a missing file; SAM 2 would then build
    the model with random weights and 'succeed' with garbage masks, so a missing checkpoint is an error."""
    import folder_paths  # Comfy runtime module

    try:
        path = folder_paths.get_full_path(SAM2_FOLDER, name)
    except KeyError:  # folder not registered
        path = None
    if not path:
        raise RuntimeError(f"{name} not found in models/{SAM2_FOLDER}")
    return path


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
        ckpt = _sam2_checkpoint("sam2.1_hiera_small.pt")
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


# ---- Pocket VFX -------------------------------------------------------------------------------------------------
# Thin wrappers over open_creative.pocket_vfx (the CPU worker runs the same code). IMAGE is [B,H,W,3] float 0..1
# sRGB; masks are MASK [B,H,W] float 0..1 at the proxy or the output size (upscaled with ``upscale_mask``).
# Coordinates in JSON are uv01 of the work clip unless stated otherwise.

PV_CATEGORY = "open_creative/pocket_vfx"
SAM2_CONFIG = "configs/sam2.1/sam2.1_hiera_s.yaml"
SAM2_CHECKPOINT = "sam2.1_hiera_small.pt"
SAM2_MODEL = "sam2.1_hiera_small"


def _gray_frames(frames) -> list[np.ndarray]:
    import cv2

    return [cv2.cvtColor(f, cv2.COLOR_RGB2GRAY) for f in _frames_u8(frames)]


def _mask_at(masks: np.ndarray | None, i: int, w: int, h: int) -> np.ndarray | None:
    if masks is None:
        return None
    m = masks[min(i, len(masks) - 1)]
    return m.astype(np.float32) if m.shape == (h, w) else pv_masks.upscale_mask(m, w, h)


class PVTrackAnchor:
    """LK point track of the anchor (+ corrections) on the proxy frames -> track JSON (``track_to_json``)."""

    CATEGORY = PV_CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("track",)

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "frames": ("IMAGE",),
            "anchor": ("STRING", {"default": '{"frame":0,"x":0.5,"y":0.7,"follow":true,"track_window_frames":null}'}),
            "corrections": ("STRING", {"default": "[]", "multiline": True}),
            "out_width": ("INT", {"default": 1280, "min": 64, "max": 4096}),
            "out_height": ("INT", {"default": 720, "min": 64, "max": 4096}),
        }}

    def run(self, frames, anchor, corrections, out_width, out_height):
        gray = _gray_frames(frames)
        a, corr = json.loads(anchor), json.loads(corrections or "[]")
        h, w = gray[0].shape[:2]
        if not a.get("follow", True):
            points = static_track((a["x"] * w, a["y"] * h), len(gray))
        else:
            points = smooth_track(track_with_corrections(gray, a, corr, a.get("track_window_frames")),
                                  SMOOTH_SIGMA_FRAMES, SMOOTH_MAX_SHIFT_PX)
        return (json.dumps(track_to_json(points, (w, h), (out_width, out_height))),)


class PVSubjectMasks:
    """SAM 2.1 hiera small VIDEO predictor -> mask sequence (MASK [N,H,W]) + manifest; writes the ``masks`` ZIP.

    ``prompts`` is ``pocket_vfx.masks.build_prompts`` JSON (proxy px). The video state is created per call
    (``init_state`` on a per-call temporary frame directory) and never shared. SAM 2 / torch import lazily."""

    CATEGORY = PV_CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("MASK", "STRING")
    RETURN_NAMES = ("masks", "manifest")
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "frames": ("IMAGE",),
            "prompts": ("STRING", {"default": "{}", "multiline": True}),
            "width": ("INT", {"default": 640, "min": 16, "max": 4096}),
            "height": ("INT", {"default": 360, "min": 16, "max": 4096}),
        }, "optional": {"filename_prefix": ("STRING", {"default": "pocket_vfx_masks"})}}

    def run(self, frames, prompts, width, height, filename_prefix="pocket_vfx_masks"):
        try:
            from sam2.build_sam import build_sam2_video_predictor
        except ImportError as e:
            raise RuntimeError("SAM 2 is not installed in this Comfy build (vfx_masks_v1 needs the video "
                               "predictor)") from e
        import contextlib
        import tempfile
        import uuid as _uuid
        from pathlib import Path

        import cv2
        import folder_paths  # Comfy runtime module
        from PIL import Image

        p = json.loads(prompts or "{}")
        rgb = _frames_u8(frames)
        n = len(rgb)
        predictor = build_sam2_video_predictor(SAM2_CONFIG, _sam2_checkpoint(SAM2_CHECKPOINT))
        try:
            import torch

            no_grad = torch.inference_mode()
        except ImportError:
            no_grad = contextlib.nullcontext()
        out = np.zeros((n, height, width), np.float32)
        with tempfile.TemporaryDirectory(prefix="pv_sam2_") as tmp, no_grad:
            for i, f in enumerate(rgb):
                if f.shape[:2] != (height, width):
                    f = cv2.resize(f, (width, height), interpolation=cv2.INTER_AREA)
                Image.fromarray(f).save(Path(tmp) / f"{i:05d}.jpg", quality=95)
            state = predictor.init_state(video_path=tmp, offload_video_to_cpu=True)
            try:
                obj = int(p.get("obj_id", 1))
                for pf in p.get("point_frames") or []:
                    pts = np.array([[q["x"], q["y"]] for q in pf["points"]], np.float32)
                    labels = np.array([q["label"] for q in pf["points"]], np.int32)
                    predictor.add_new_points_or_box(inference_state=state, frame_idx=int(pf["frame"]), obj_id=obj,
                                                    points=pts, labels=labels)
                for mf in p.get("mask_frames") or []:
                    m = pv_masks.decode_prompt_mask(mf["png_b64"], width, height) > 0
                    predictor.add_new_mask(inference_state=state, frame_idx=int(mf["frame"]), obj_id=obj, mask=m)
                for reverse in (False, True):
                    for idx, _ids, logits in predictor.propagate_in_video(state, reverse=reverse):
                        out[int(idx)] = (_np(logits)[0, 0] > 0.0).astype(np.float32)
            finally:
                predictor.reset_state(state)
        seq = pv_masks.MaskSequence(masks=[(m * 255).astype(np.uint8) for m in out],
                                    prompts_hash=pv_masks.prompts_hash(p), model=SAM2_MODEL)
        out_dir = Path(folder_paths.get_output_directory())
        name = f"{filename_prefix}_{_uuid.uuid4().hex[:12]}.zip"
        doc = pv_masks.write_masks_zip(seq, out_dir / name)
        return {"ui": {"masks": [{"filename": name, "subfolder": "", "type": "output"}]},
                "result": (_out(out), json.dumps(doc))}


class PVProceduralEffect:
    """One frame of a procedural effect layer: linear premultiplied RGB (IMAGE) + alpha (MASK)."""

    CATEGORY = PV_CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("rgb_premult_linear", "alpha")

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "preset_id": (sorted(load_pv_presets()),),
            "params": ("STRING", {"default": '{"scale":1.0,"intensity":0.8,"palette":"amber"}', "multiline": True}),
            "window": ("STRING", {"default": '{"start":34,"peak":43,"end":82}'}),
            "frame": ("INT", {"default": 43, "min": 0, "max": 10_000}),
            "anchor_x": ("FLOAT", {"default": 640.0, "min": -1.0, "max": 8192.0}),  # output px; < 0 = none
            "anchor_y": ("FLOAT", {"default": 500.0, "min": -1.0, "max": 8192.0}),
            "width": ("INT", {"default": 1280, "min": 16, "max": 4096}),
            "height": ("INT", {"default": 720, "min": 16, "max": 4096}),
            "seed": ("INT", {"default": 12345, "min": 0, "max": 2**31 - 1}),
        }, "optional": {"mask": ("MASK",)}}

    def run(self, preset_id, params, window, frame, anchor_x, anchor_y, width, height, seed, mask=None):
        win = json.loads(window)
        anchor = (anchor_x, anchor_y) if anchor_x >= 0 and anchor_y >= 0 else None
        m = _mask_at(_np(mask), 0, width, height) if mask is not None else None
        layer = render_effect(preset_id, json.loads(params), EffectWindow(win["start"], win["peak"], win["end"]),
                              frame, anchor, width, height, seed, m)
        if layer is None:
            return _out(np.zeros((1, height, width, 3), np.float32)), _out(np.zeros((1, height, width), np.float32))
        return _out(layer.rgb_premult[None]), _out(layer.alpha[None])


class PVTrackedComposite:
    """Source frames + track + (optional) subject masks + effect config -> composited frames, ROI and stats.

    ``config``: ``{preset_id, params, window{start,peak,end}, layer_order, seed}`` (+ optional ``invalid_policy``,
    default the preset's). The effect follows the track (uv01); invalid frames follow the invalid policy."""

    CATEGORY = PV_CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("frames", "roi", "manifest")

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "source": ("IMAGE",), "track": ("STRING", {"forceInput": True}),
            "config": ("STRING", {"default": "{}", "multiline": True}),
        }, "optional": {"masks": ("MASK",)}}

    def run(self, source, track, config, masks=None):
        src = _frames_u8(source)
        n, h, w = src.shape[:3]
        cfg = json.loads(config)
        preset = load_pv_presets()[cfg["preset_id"]]
        win = EffectWindow(**{k: int(cfg["window"][k]) for k in ("start", "peak", "end")})
        anchors = anchor_positions(json.loads(track), None, w, h, n,
                                   cfg.get("invalid_policy", preset.get("invalid_policy", "hide")))
        ms = _np(masks) if masks is not None else None
        out = np.empty((n, h, w, 3), np.float32)
        roi_out = np.empty((n, h, w), np.float32)
        roi_px = []
        for i in range(n):
            m = _mask_at(ms, i, w, h)
            effect = None
            if anchors[i] is not None or (m is not None and cfg["preset_id"] == "energy_aura_v1"):
                effect = render_effect(cfg["preset_id"], cfg.get("params", {}), win, i,
                                       tuple(anchors[i]) if anchors[i] is not None else None, w, h,
                                       int(cfg.get("seed", 0)), m)
            frame, roi = pv_composite_frame(FrameInputs(source=src[i], effect=effect, plate=None, subject_mask=m,
                                                        front_glow=None, roi_limit=None), cfg["layer_order"])
            out[i] = frame.astype(np.float32) / 255.0
            roi_out[i] = roi
            roi_px.append(int(roi.sum()))
        manifest = {"frames": n, "width": w, "height": h, "layer_order": cfg["layer_order"], "roi_px": roi_px,
                    "occlusion": ms is not None and cfg["layer_order"] != "in_front"}
        return _out(out), _out(roi_out), json.dumps(manifest)


class PVOutsideROIIntegrity:
    """Outside-ROI integrity (max channel error <= 1/255 outside the ROI) and, with a subject mask, Occlusion QA
    (max fraction of subject-core pixels changed by > 2/255; ``masks.subject_core``)."""

    CATEGORY = PV_CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("qa",)
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"source": ("IMAGE",), "frames": ("IMAGE",), "roi": ("MASK",)},
                "optional": {"subject_mask": ("MASK",)}}

    def run(self, source, frames, roi, subject_mask=None):
        src, out, r = _frames_u8(source), _frames_u8(frames), _np(roi) >= 0.5
        h, w = src.shape[1:3]
        ms = _np(subject_mask) if subject_mask is not None else None
        acc, cover = IntegrityAccumulator(), []
        for i in range(len(src)):
            acc.add(src[i], out[i], r[i])
            if ms is not None:
                cover.append(subject_core_cover(out[i], src[i], pv_masks.subject_core(_mask_at(ms, i, w, h))))
        return (json.dumps({"integrity": acc.result(), "occlusion_qa": max(cover) if cover else None}),)


class PVPlateMatte:
    """Raw emissive plate frames (sRGB) -> linear premultiplied RGB (IMAGE) + alpha (MASK) + matte QA (JSON STRING
    of ``matte_qa``: halo risk / flicker / empty plate)."""

    CATEGORY = PV_CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("rgb_premult_linear", "alpha", "qa")

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "frames": ("IMAGE",),
            "threshold": ("FLOAT", {"default": 0.06, "min": 0.0, "max": 1.0, "step": 0.005}),
            "knee": ("FLOAT", {"default": 0.04, "min": 0.0, "max": 0.5, "step": 0.005}),
        }}

    def run(self, frames, threshold, knee):
        stats = MatteStats()
        pairs = list(matte_sequence(iter(_frames_u8(frames)), MatteConfig(float(threshold), float(knee)), stats))
        return (_out(np.stack([p[0] for p in pairs])), _out(np.stack([p[1] for p in pairs])),
                json.dumps(matte_qa(stats.summary())))


NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in
                       (PLProductMask, PLLockedProduct, PLBackgroundPrompt, PLCompositeSequence, PLCoreIntegrityCheck,
                        CSDepthToScene, CSCompileCameraPath, CSRenderGuides, CSPathDiagnostics,
                        CSGeneratedMotionReport, PVTrackAnchor, PVSubjectMasks, PVProceduralEffect,
                        PVTrackedComposite, PVOutsideROIIntegrity, PVPlateMatte)}
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
    "PVTrackAnchor": "Pocket VFX · Track Anchor",
    "PVSubjectMasks": "Pocket VFX · Subject Masks (SAM 2.1 video)",
    "PVProceduralEffect": "Pocket VFX · Procedural Effect",
    "PVTrackedComposite": "Pocket VFX · Tracked Composite",
    "PVOutsideROIIntegrity": "Pocket VFX · Outside-ROI Integrity",
    "PVPlateMatte": "Pocket VFX · Plate Matte",
}
