"""Depth -> scene for Camera Sketch: convention adapter, clip/smooth/normalize, mesh.

Conventions (shared with ``camera.py``; the TS port of ``build_mesh`` mirrors this file):
- Depth Anything V2 Small outputs relative *inverse* depth (larger = nearer), not meters.
  ``z = scale / max(d + offset, eps)``; non-finite inputs stay non-finite (invalid pixels).
- Pixel ``(row, col)`` has its center at continuous coords ``(col + 0.5, row + 0.5)``.
- ``prepare_z`` normalizes so the median z of the target patch is exactly 1 scene unit.

``build_mesh`` layout (deterministic, edge-aligned so the mesh covers the whole image):
- Grid vertices sit at continuous image positions ``x_j = min(j*stride, W)`` for
  ``j = 0..ceil(W/stride)`` (so both ``0`` and ``W`` are included; the last column may be a
  partial stride), and ``y_i`` likewise with ``H``. Vertex index is ``i * grid_w + j``
  (row-major). Vertex = ``z * K^-1 @ [x, y, 1]`` where ``z`` is sampled at the clamped
  pixel ``z[clamp(floor(y), 0, H-1), clamp(floor(x), 0, W-1)]``; ``uv = (x / W, y / H)``.
  Colors for ``uv`` are sampled bilinearly from pixel centers with clamped borders
  (``render.vertex_colors``).
- Each cell with corners ``a=(r,c) b=(r,c+1) c=(r+1,c) d=(r+1,c+1)`` is split along the
  b-c diagonal into triangles ``(a, c, b)`` and ``(b, c, d)`` (emitted cell by cell,
  row-major, ``(a,c,b)`` first).
- A triangle with any non-finite vertex z is dropped. A finite triangle with
  ``max(z)/min(z) - 1 > threshold`` (a depth jump) is NOT connected as a regular face: it goes
  to ``Mesh.edge_faces`` (same order rules), the fallback "edge layer". ``Mesh.faces`` holds only
  the regular (connected) faces, so code that ignores ``edge_faces`` sees the cut mesh.
  The renderer uses edge faces only where no regular face covers a pixel, and marks such a
  pixel valid only while the face is not stretched (see ``render.rasterize``).
  Vertices are never removed; non-finite z vertices are emitted at the origin.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

import cv2
import numpy as np


@dataclass(frozen=True)
class DepthConvention:
    kind: Literal["inverse_relative", "depth_relative"]
    scale: float = 1.0
    offset: float = 0.0
    eps: float = 1e-6


DAV2_SMALL = DepthConvention("inverse_relative")

# Far cap in normalized scene units (target z = 1), applied at the end of ``prepare_z``.
# Kept below the renderer far plane (100). Inverse depth <= eps means "at infinity" (sky);
# it is represented as this cap, not NaN, so the sky stays renderable as a backdrop.
Z_FAR_CAP = 50.0


def depth_to_z(d: np.ndarray, conv: DepthConvention) -> np.ndarray:
    d = np.asarray(d, dtype=np.float32)
    if conv.kind == "inverse_relative":
        z = conv.scale / np.maximum(d + conv.offset, conv.eps)
    else:
        z = conv.scale * np.maximum(d + conv.offset, conv.eps)
    return z.astype(np.float32)


def _target_patch_median(z: np.ndarray, target_uv01: tuple[float, float]) -> float:
    h, w = z.shape
    r = max(1, int(round(0.02 * min(h, w))))
    finite = np.isfinite(z)
    if not finite.any():
        raise ValueError("depth has no finite values")
    cx = int(np.clip(np.floor(target_uv01[0] * w), 0, w - 1))
    cy = int(np.clip(np.floor(target_uv01[1] * h), 0, h - 1))

    def patch(cy_: int, cx_: int) -> np.ndarray:
        p = z[max(0, cy_ - r) : cy_ + r + 1, max(0, cx_ - r) : cx_ + r + 1]
        return p[np.isfinite(p)]

    vals = patch(cy, cx)
    if vals.size == 0:
        # Nearest finite pixel (squared Euclidean; ties -> first in row-major order).
        ys, xs = np.nonzero(finite)
        k = int(np.argmin((ys - cy) ** 2 + (xs - cx) ** 2))
        vals = patch(int(ys[k]), int(xs[k]))
    return float(np.median(vals))


def prepare_z(
    z: np.ndarray,
    target_uv01: tuple[float, float],
    clip_pct: tuple[float, float] = (1, 99),
    smooth: bool = True,
) -> tuple[np.ndarray, float]:
    """Percentile clip, edge-aware smoothing of log z, normalize target patch median to 1.

    Returns ``(z_norm, target_z_raw)`` where ``target_z_raw`` is the target patch median
    after clip/smooth and before normalization. Non-finite pixels stay NaN. Raises
    ``ValueError`` when no pixel is finite.
    """
    z = np.asarray(z, dtype=np.float32).copy()
    finite = np.isfinite(z) & (z > 0)
    if not finite.any():
        raise ValueError("depth has no finite values")
    z[~finite] = np.nan
    lo, hi = np.nanpercentile(z, clip_pct)
    z = np.clip(z, lo, hi)
    if smooth:
        lz = np.log(z)
        lz[~finite] = np.float32(np.nanmedian(lz))
        lz = cv2.bilateralFilter(lz.astype(np.float32), d=5, sigmaColor=0.1, sigmaSpace=3.0)
        z = np.exp(lz).astype(np.float32)
        z[~finite] = np.nan
    target_raw = _target_patch_median(z, target_uv01)
    z_norm = np.minimum(z / np.float32(target_raw), np.float32(Z_FAR_CAP))  # NaN stays NaN
    return z_norm.astype(np.float32), target_raw


@dataclass
class Mesh:
    vertices: np.ndarray  # Nx3 float32
    uv: np.ndarray  # Nx2 float32
    faces: np.ndarray  # Mx3 int32, regular (connected) faces
    grid_shape: tuple[int, int]  # (grid_h, grid_w)
    edge_faces: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), np.int32))  # Ex3 int32, depth-jump faces


def build_mesh(
    z_norm: np.ndarray, K: np.ndarray, stride: int, edge_rel_threshold: float = 0.08
) -> Mesh:
    if stride < 1:
        raise ValueError("stride must be >= 1")
    h, w = z_norm.shape
    ys = np.minimum(np.arange(-(-h // stride) + 1) * stride, h)
    xs = np.minimum(np.arange(-(-w // stride) + 1) * stride, w)
    gh, gw = len(ys), len(xs)
    rows, cols = np.minimum(ys, h - 1), np.minimum(xs, w - 1)
    zg = np.asarray(z_norm, dtype=np.float64)[np.ix_(rows, cols)]
    u, v = np.meshgrid(xs.astype(np.float64), ys.astype(np.float64))
    ok = np.isfinite(zg)
    zs = np.where(ok, zg, 0.0)
    rays = np.stack([u, v, np.ones_like(u)], -1) @ np.linalg.inv(K).T
    vertices = (rays * zs[..., None]).reshape(-1, 3).astype(np.float32)
    uv = np.stack([u / w, v / h], -1).reshape(-1, 2).astype(np.float32)

    idx = np.arange(gh * gw, dtype=np.int32).reshape(gh, gw)
    a, b = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()
    c, d = idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    # Interleave per cell: (a, c, b) then (b, c, d).
    tris = np.stack([np.stack([a, c, b], 1), np.stack([b, c, d], 1)], 1).reshape(-1, 3)
    zf = zg.ravel()
    zt = zf[tris]
    finite = np.isfinite(zt).all(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        rel = zt.max(1) / zt.min(1) - 1.0
    keep = finite & (rel <= edge_rel_threshold)
    edge = finite & ~keep
    return Mesh(vertices, uv, tris[keep].astype(np.int32), (gh, gw), tris[edge].astype(np.int32))


def rgb_pixel_hash(rgb: np.ndarray) -> str:
    """sha256 over the HxWx3 uint8 pixel bytes plus the shape: identifies an image regardless of PNG encoding."""
    a = np.ascontiguousarray(rgb, dtype=np.uint8)
    h = hashlib.sha256()
    h.update(repr(a.shape).encode())
    h.update(a.tobytes())
    return h.hexdigest()


def encode_depth_png16(d: np.ndarray) -> tuple[bytes, dict]:
    """16-bit PNG, linear ``min..max -> 0..65535``. Non-finite values encode as 0 (= min)."""
    d = np.asarray(d, dtype=np.float64)
    fin = np.isfinite(d)
    lo = float(d[fin].min()) if fin.any() else 0.0
    hi = float(d[fin].max()) if fin.any() else 0.0
    span = hi - lo
    q = np.zeros(d.shape, np.uint16)
    if span > 0:
        q = np.rint(np.where(fin, (d - lo) / span, 0.0) * 65535.0).astype(np.uint16)
    ok, buf = cv2.imencode(".png", q)
    if not ok:
        raise ValueError("png encode failed")
    return buf.tobytes(), {"min": lo, "max": hi}


def decode_depth_png16(data: bytes, meta: dict) -> np.ndarray:
    q = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if q is None or q.dtype != np.uint16 or q.ndim != 2:
        raise ValueError("not a 16-bit grayscale png")
    lo, hi = meta["min"], meta["max"]
    return (lo + q.astype(np.float64) / 65535.0 * (hi - lo)).astype(np.float32)
