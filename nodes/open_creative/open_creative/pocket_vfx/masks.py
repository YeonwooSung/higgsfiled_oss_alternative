"""Subject mask sequences (spec §6.3): manifest + ZIP storage, proxy -> output resize, contour band, temporal
smoothing, diagnostics and user mask edits. Pure NumPy/OpenCV (no torch).

Conventions:
- A sequence holds one uint8 HxW mask per work-clip frame at the PROXY size (640 px long side), 255 = subject.
  Masks are binarized at > 127. They are stored as a ZIP of 8-bit gray PNGs ``mask_%04d.png`` plus
  ``manifest.json`` (``manifest``).
- Proxy -> output only through ``upscale_mask`` (soft: bilinear on float, then clip; binary: nearest). The RGB
  resize path is never used for masks.
- Prompts (the ``prompts`` JSON of ``vfx_masks_v1``) are in proxy px: ``build_prompts``. A mask edit is applied to
  its frame (``apply_mask_edits``) and also sent as a mask prompt frame (``edits_as_prompts``) so the propagation
  around it follows the correction.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

SCHEMA_VERSION = 1
AREA_JUMP = 0.35  # relative frame-to-frame area change that is flagged
MAX_HOLES = 3  # more interior holes than this is flagged
HOLE_MIN_PX = 4  # background islands smaller than this (proxy px) are noise, not holes
MAX_FRAMES = 240  # sanity bound when reading a provider ZIP (5 s @ 24 fps = 120)
CORE_ERODE_PX = 4  # output px: the subject core of Occlusion QA lies this far inside the (soft >= 0.5) silhouette
_FRAME_NAME = re.compile(r"^mask_(\d{4})\.png$")
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class MaskFormatError(ValueError):
    """A mask ZIP that does not match the contract (frames, size, names)."""


@dataclass
class MaskSequence:
    masks: list[np.ndarray]  # uint8 HxW at proxy size, 255 = subject
    prompts_hash: str = ""
    model: str = ""

    def __len__(self) -> int:
        return len(self.masks)

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.masks[0].shape[:2]
        return w, h


# --------------------------------------------------------------------------- geometry helpers (shared)


def binary(mask: np.ndarray) -> np.ndarray:
    """uint8 0/1 of a bool, uint8 (> 127) or float (> 0.5) mask."""
    a = np.asarray(mask)
    if a.dtype == np.bool_:
        b = a
    elif a.dtype == np.uint8:
        b = a > 127
    else:
        b = a > 0.5
    return b.astype(np.uint8)


def mask_box(m: np.ndarray) -> tuple[int, int, int, int] | None:
    """Tight ``(x0, y0, x1, y1)`` (exclusive ends) of the nonzero pixels, or None."""
    rows, cols = np.flatnonzero(m.any(axis=1)), np.flatnonzero(m.any(axis=0))
    if rows.size == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def pad_box(box, pad: int, w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return max(x0 - pad, 0), max(y0 - pad, 0), min(x1 + pad, w), min(y1 + pad, h)


def disk_kernel(r: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


# --------------------------------------------------------------------------- stats, manifest, ZIP


def mask_stats(mask: np.ndarray) -> dict:
    """``area`` (subject px), ``holes`` (background islands of >= HOLE_MIN_PX px not touching the frame edge)."""
    b = binary(mask)
    area = int(b.sum())
    if area == 0:
        return {"area": 0, "holes": 0, "empty": True}
    n, _, stats, _ = cv2.connectedComponentsWithStats(1 - b, connectivity=4)
    h, w = b.shape
    holes = 0
    for i in range(1, n):
        x, y, bw, bh, px = stats[i]
        touches = x == 0 or y == 0 or x + bw == w or y + bh == h
        if not touches and px >= HOLE_MIN_PX:
            holes += 1
    return {"area": area, "holes": holes, "empty": False}


def frame_file(i: int) -> str:
    return f"mask_{i:04d}.png"


def manifest(seq: MaskSequence) -> dict:
    w, h = seq.size
    return {"schema_version": SCHEMA_VERSION, "size": [w, h],
            "frames": [{"frame": i, "file": frame_file(i), **mask_stats(m)} for i, m in enumerate(seq.masks)],
            "prompts_hash": seq.prompts_hash, "model": seq.model}


def _png(mask: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(mask, np.uint8), "L").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def write_masks_zip(seq: MaskSequence, path: str | Path) -> dict:
    """Write ``mask_%04d.png`` (8-bit gray) + ``manifest.json``; returns the manifest."""
    doc = manifest(seq)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for i, m in enumerate(seq.masks):
            z.writestr(zipfile.ZipInfo(frame_file(i), date_time=_ZIP_TIME), _png(m))
        z.writestr(zipfile.ZipInfo("manifest.json", date_time=_ZIP_TIME), json.dumps(doc, separators=(",", ":")))
    return doc


def read_masks_zip(path: str | Path, expect_frames: int | None = None,
                   expect_size: tuple[int, int] | None = None) -> MaskSequence:
    """Read a mask ZIP (a manifest is optional). Only ``mask_NNNN.png`` members are read; nothing is extracted."""
    with zipfile.ZipFile(path) as z:
        names = {int(m.group(1)): n for n in z.namelist() if (m := _FRAME_NAME.match(n))}
        if not names or sorted(names) != list(range(len(names))) or len(names) > MAX_FRAMES:
            raise MaskFormatError("mask frames must be mask_0000.png .. contiguous")
        if expect_frames is not None and len(names) != expect_frames:
            raise MaskFormatError(f"{len(names)} mask frames, expected {expect_frames}")
        doc = json.loads(z.read("manifest.json")) if "manifest.json" in z.namelist() else {}
        masks = []
        for i in range(len(names)):
            with Image.open(io.BytesIO(z.read(names[i]))) as im:
                if expect_size is not None and im.size != tuple(expect_size):
                    raise MaskFormatError(f"mask {i} size {im.size}, expected {tuple(expect_size)}")
                a = np.asarray(im.convert("L"), np.uint8)
            if masks and a.shape != masks[0].shape:
                raise MaskFormatError(f"mask {i} size differs from mask 0")
            masks.append(np.where(a > 127, 255, 0).astype(np.uint8))
    return MaskSequence(masks=masks, prompts_hash=str(doc.get("prompts_hash", "")), model=str(doc.get("model", "")))


# --------------------------------------------------------------------------- resize


def _unit(mask: np.ndarray) -> np.ndarray:
    a = np.asarray(mask)
    if a.dtype == np.bool_:
        return a.astype(np.float32)
    if a.dtype == np.uint8:
        return a.astype(np.float32) / 255.0
    return np.clip(a.astype(np.float32), 0.0, 1.0)


def upscale_mask(m_proxy: np.ndarray, out_w: int, out_h: int, soft: bool = True) -> np.ndarray:
    """Proxy mask -> float32 HxW in [0, 1] at the output size. Soft: bilinear on float, then clip (edges get
    fractional coverage). Binary: nearest on the binarized mask (exactly 0/1)."""
    if soft:
        up = cv2.resize(_unit(m_proxy), (int(out_w), int(out_h)), interpolation=cv2.INTER_LINEAR)
        return np.clip(up, 0.0, 1.0).astype(np.float32)
    up = cv2.resize(binary(m_proxy), (int(out_w), int(out_h)), interpolation=cv2.INTER_NEAREST)
    return up.astype(np.float32)


def subject_core(mask_out: np.ndarray, erode_px: int = CORE_ERODE_PX) -> np.ndarray:
    """Bool core of an output-size soft mask: ``>= 0.5`` eroded by ``erode_px`` (the soft edge and the ROI
    dilation stay outside it). Occlusion QA (``integrity.subject_core_cover``) checks these pixels."""
    b = (np.asarray(mask_out, np.float32) >= 0.5).astype(np.uint8)
    return cv2.erode(b, disk_kernel(erode_px)).astype(bool) if erode_px > 0 else b.astype(bool)


def edit_to_proxy(mask: np.ndarray, w: int, h: int) -> np.ndarray:
    """A user mask edit (gray, any size) -> uint8 0/255 at the proxy size (binary, nearest)."""
    a = np.asarray(mask)
    if a.ndim != 2:
        raise ValueError("a mask edit must be a single-channel image")
    return (cv2.resize(binary(a), (int(w), int(h)), interpolation=cv2.INTER_NEAREST) * 255).astype(np.uint8)


# --------------------------------------------------------------------------- contour band, smoothing


def contour_band_patch(mask: np.ndarray, inner_px: int, outer_px: int,
                       margin: int | None = None) -> tuple[tuple[int, int, int, int], np.ndarray] | None:
    """``dilate(M, outer) - erode(M, inner)`` with a Gaussian feather, as ``(box, crop)``. ``mask`` is bool, uint8
    0/255 or float in [0, 1] (``binary``): the float32 band inside
    ``box = (x0, y0, x1, y1)`` (the mask bbox padded by ``margin``, default just enough for the band; the band is 0
    outside it). None for an empty mask. A frame edge does not erode (the subject continues past the frame)."""
    m = binary(mask)
    box = mask_box(m)
    if box is None:
        return None
    feather = max(1.0, (inner_px + outer_px) / 4.0)
    need = outer_px + math.ceil(4 * feather) + 1
    x0, y0, x1, y1 = pad_box(box, max(need, margin or 0), m.shape[1], m.shape[0])
    crop = m[y0:y1, x0:x1]
    dil = cv2.dilate(crop, disk_kernel(outer_px))
    ero = cv2.erode(crop, disk_kernel(inner_px)) if inner_px > 0 else crop
    band = cv2.GaussianBlur((dil - ero).astype(np.float32), (0, 0), feather)
    return (x0, y0, x1, y1), band


def contour_band(mask: np.ndarray, inner_px: int, outer_px: int) -> np.ndarray:
    """Full-frame float32 contour band (``contour_band_patch`` placed on the frame)."""
    out = np.zeros(np.shape(mask)[:2], np.float32)
    patch = contour_band_patch(mask, inner_px, outer_px)
    if patch is not None:
        (x0, y0, x1, y1), band = patch
        out[y0:y1, x0:x1] = band
    return out


def smooth_masks(seq: MaskSequence | Sequence[np.ndarray], radius: int = 1) -> list[np.ndarray]:
    """Temporal box average over ``[i - radius, i + radius]`` (edge frames repeated), uint8 soft masks.
    Binarized at > 127 it is a per-pixel majority vote, which removes one-frame dropouts and flicker."""
    masks = list(seq.masks if isinstance(seq, MaskSequence) else seq)
    n = len(masks)
    if radius <= 0 or n == 0:
        return [np.asarray(m, np.uint8).copy() for m in masks]
    width = 2 * radius + 1
    out = []
    for i in range(n):
        acc = np.zeros(masks[0].shape, np.uint16)
        for j in range(i - radius, i + radius + 1):
            acc += masks[min(max(j, 0), n - 1)]
        out.append(np.rint(acc / width).astype(np.uint8))
    return out


# --------------------------------------------------------------------------- diagnostics


def mask_diagnostics(seq: MaskSequence | Sequence[np.ndarray], expects_subject: Iterable[int] | None = None,
                     stats: Sequence[dict] | None = None) -> list[dict]:
    """Warnings ``[{frame, code}]``: ``area_jump`` (area changes > 35 % from the previous non-empty frame),
    ``mask_empty`` (no subject on a frame where the prompts expect it; ``expects_subject=None`` = every frame) and
    ``holes`` (> 3 interior holes). ``stats`` may pass precomputed ``mask_stats`` per frame."""
    masks = seq.masks if isinstance(seq, MaskSequence) else seq
    st = list(stats) if stats is not None else [mask_stats(m) for m in masks]
    expected = None if expects_subject is None else set(expects_subject)
    out: list[dict] = []
    for i, s in enumerate(st):
        if i > 0 and st[i - 1]["area"] > 0 and abs(s["area"] - st[i - 1]["area"]) / st[i - 1]["area"] > AREA_JUMP:
            out.append({"frame": i, "code": "area_jump"})
        if s["empty"] and (expected is None or i in expected):
            out.append({"frame": i, "code": "mask_empty"})
        if s["holes"] > MAX_HOLES:
            out.append({"frame": i, "code": "holes"})
    return out


# --------------------------------------------------------------------------- mask edits and prompts


def apply_mask_edits(seq: MaskSequence, edits: dict[int, np.ndarray]) -> MaskSequence:
    """A new sequence where each edited frame is replaced by its edit (proxy-size uint8 0/255)."""
    masks = list(seq.masks)
    for frame, m in edits.items():
        if not 0 <= frame < len(masks):
            raise ValueError(f"mask edit frame {frame} outside 0..{len(masks) - 1}")
        if np.shape(m) != masks[frame].shape:
            raise ValueError(f"mask edit {frame} has shape {np.shape(m)}, expected {masks[frame].shape}")
        masks[frame] = (binary(m) * 255).astype(np.uint8)
    return MaskSequence(masks=masks, prompts_hash=seq.prompts_hash, model=seq.model)


def edits_as_prompts(edits: dict[int, np.ndarray]) -> list[dict]:
    """Mask prompt frames for re-propagation: ``[{frame, png_b64}]`` (8-bit gray PNG at proxy size)."""
    return [{"frame": int(f), "png_b64": base64.b64encode(_png((binary(m) * 255).astype(np.uint8))).decode()}
            for f, m in sorted(edits.items())]


def decode_prompt_mask(png_b64: str, w: int, h: int) -> np.ndarray:
    with Image.open(io.BytesIO(base64.b64decode(png_b64, validate=True))) as im:
        if im.size != (w, h):
            raise ValueError(f"mask prompt size {im.size}, expected {(w, h)}")
        return (binary(np.asarray(im.convert("L"), np.uint8)) * 255).astype(np.uint8)


def build_prompts(subject_prompts: Sequence[dict], edits: dict[int, np.ndarray], w: int, h: int, n_frames: int,
                  mock_context: dict | None = None) -> dict:
    """The ``prompts`` binding of ``vfx_masks_v1``: point prompts (config uv01 -> proxy px) and mask prompts.
    ``mock_context`` carries what only the mock provider uses (the real graph ignores it)."""
    point_frames = [{"frame": int(p["frame"]),
                     "points": [{"x": round(pt["x"] * w, 3), "y": round(pt["y"] * h, 3), "label": int(pt["label"])}
                                for pt in p["points"]]}
                    for p in sorted(subject_prompts, key=lambda p: p["frame"]) if p["points"]]
    doc = {"schema_version": SCHEMA_VERSION, "width": int(w), "height": int(h), "frame_count": int(n_frames),
           "obj_id": 1, "point_frames": point_frames, "mask_frames": edits_as_prompts(edits)}
    if mock_context is not None:
        doc["mock_context"] = mock_context
    return doc


def prompts_hash(prompts: dict) -> str:
    canon = json.dumps({k: v for k, v in prompts.items() if k != "mock_context"}, sort_keys=True,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()
