"""Guide renderer for Camera Sketch: z-buffer rasterizer, small-hole fill, frame sequence.

Conventions (shared with ``camera.py`` / ``scene.py``):
- Output pixel ``(row, col)`` is sampled at its center ``(col + 0.5, row + 0.5)``.
- A pixel is covered by a triangle when all three screen-space barycentrics are
  ``>= -COVER_EPS`` (inclusive edges, so shared edges never leave cracks).
- Nearest camera-space z wins. Color and z are interpolated perspective-correctly
  (``1/z`` is linear in screen space). Triangles with any vertex at ``z <= near`` are
  dropped (no clipping); covered samples with ``z > far`` are rejected.
- Two layers. Regular faces (``mesh.faces``) are z-buffered as above. Edge faces
  (``mesh.edge_faces``, triangles across a depth jump) are z-buffered among themselves and
  only fill pixels that NO regular face covers, so they never occlude a regular face.
- Edge-face validity ("stretch" rule): for an edge face, ``A_ref`` is its signed screen area
  under ``K_out`` at the identity pose (``T_cw = I``: the source view mapped to the output)
  and ``A`` its signed screen area at the current pose (screen area = shoelace area of the
  three projected vertices ``(x/z, y/z)`` after ``K_out``). ``stretch = A / A_ref``. A pixel
  won by an edge face is valid iff ``0 < stretch <= STRETCH_VALID`` (1.5); a flipped
  (``stretch <= 0``) or stretched face gives ``valid=False, z=inf`` but its texture/vertex
  colour is still written to ``rgb``. At the identity pose every edge face has stretch 1, so
  the source view renders without cracks; a disocclusion strip (edge face stretched across
  it) stays invalid.
- "Hole" = pixel no regular face covers and no valid edge face covers: ``valid=False``,
  ``z=inf``; ``rgb=0`` unless a stretched edge face wrote its colour. Holes are never
  counted as valid; only components that fit a ``max_px`` box may be inpainted.

Pure NumPy (+OpenCV for inpainting / nearest-valid lookup). Triangles are processed in
buckets by bbox size: each bucket evaluates a fixed ``S x S`` pixel window per triangle
(vectorized, chunked); bboxes larger than ``MAX_WINDOW`` go through a per-triangle loop.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import cv2
import numpy as np

from .scene import Mesh

NEAR = 0.05
FAR = 100.0  # > scene.Z_FAR_CAP (50), so the sky backdrop renders
COVER_EPS = 1e-7
WINDOWS = (1, 2, 4, 8, 16, 32, 64)
MAX_WINDOW = WINDOWS[-1]
CHUNK_SAMPLES = 1 << 21  # candidate samples evaluated per vectorized step
STRETCH_VALID = 1.5  # edge-face pixels are valid while A/A_ref is in (0, STRETCH_VALID]


@dataclass
class Frame:
    rgb: np.ndarray  # HxWx3 uint8 (0 where invalid)
    z: np.ndarray  # HxW float32 camera-space depth, inf where invalid
    valid: np.ndarray  # HxW bool


def _bilinear(img: np.ndarray, u01: np.ndarray, v01: np.ndarray) -> np.ndarray:
    """Bilinear sample of ``img`` (HxWxC) at normalized continuous coords; the sample point
    in pixel-center index coords is ``(u*W - 0.5, v*H - 0.5)``, clamped to the image
    (border pixels replicate). Returns float64 NxC."""
    h, w = img.shape[:2]
    x = np.clip(np.asarray(u01, np.float64) * w - 0.5, 0.0, w - 1)
    y = np.clip(np.asarray(v01, np.float64) * h - 0.5, 0.0, h - 1)
    x0 = np.minimum(np.floor(x).astype(np.intp), max(w - 2, 0))
    y0 = np.minimum(np.floor(y).astype(np.intp), max(h - 2, 0))
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    top = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    bot = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    return top * (1 - fy) + bot * fy


def vertex_colors(mesh: Mesh, image: np.ndarray) -> np.ndarray:
    """Per-vertex colors (Nx3 uint8): the source image sampled bilinearly at ``mesh.uv``
    (normalized continuous coords of the edge-aligned grid, see ``scene.py``), clamped at
    the border, rounded to nearest."""
    img = np.asarray(image)[..., :3].astype(np.float64)
    out = _bilinear(img, mesh.uv[:, 0], mesh.uv[:, 1])
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _bary_coeffs(x: np.ndarray, y: np.ndarray, area: np.ndarray) -> np.ndarray:
    """Per triangle (Mx3 screen x/y), coefficients ``(a, b, c)`` with
    ``b_i(px, py) = a_i*px + b_i*py + c_i``; returns M x 3 (bary index) x 3 (a, b, c)."""
    out = np.empty((len(x), 3, 3))
    for i in range(3):
        j, k = (i + 1) % 3, (i + 2) % 3
        # Edge function of the edge opposite vertex i (j -> k), evaluated at p.
        out[:, i, 0] = (y[:, j] - y[:, k]) / area
        out[:, i, 1] = (x[:, k] - x[:, j]) / area
        out[:, i, 2] = (x[:, j] * y[:, k] - x[:, k] * y[:, j]) / area
    return out


def _project(V: np.ndarray, T: np.ndarray, K: np.ndarray):
    Xc = V @ T[:3, :3].T + T[:3, 3]
    hc = Xc @ K.T
    with np.errstate(divide="ignore", invalid="ignore"):
        return Xc[:, 2], hc[:, 0] / hc[:, 2], hc[:, 1] / hc[:, 2]


def _signed_area(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per triangle (Mx3 screen x/y): twice the signed area (same orientation as ``_bary_coeffs``)."""
    return (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])


@dataclass
class _Layer:
    zbuf: np.ndarray  # h*w, inf where uncovered
    owner: np.ndarray  # h*w, index into ``faces`` (-1 = uncovered)
    faces: np.ndarray  # Fx3 vertex indices of the rasterized candidate faces
    coef: np.ndarray  # Fx3x3 barycentric coefficients
    invz: np.ndarray  # Fx3 per-vertex 1/z
    area: np.ndarray  # F signed screen area (2x)
    src: np.ndarray  # F row index into the layer's input face array


def _raster_layer(F: np.ndarray, zv: np.ndarray, sx: np.ndarray, sy: np.ndarray, w: int, h: int,
                  near: float, far: float) -> _Layer:
    """Z-buffer one face layer: nearest z wins; exact ties -> lowest candidate index."""
    zbuf = np.full(h * w, np.inf)
    owner = np.full(h * w, -1, dtype=np.int64)
    src = np.arange(len(F))
    if len(F):
        zt = zv[F]
        keep = (zt > near).all(1)
        F, zt, src = F[keep], zt[keep], src[keep]
    empty = _Layer(zbuf, owner, np.zeros((0, 3), np.intp), np.zeros((0, 3, 3)), np.zeros((0, 3)),
                   np.zeros(0), np.zeros(0, np.intp))
    if not len(F):
        return empty
    x, y = sx[F], sy[F]
    area = _signed_area(x, y)
    # Pixel index ranges whose centers fall inside the bbox, clipped to the screen.
    x0 = np.maximum(np.ceil(x.min(1) - 0.5), 0)
    x1 = np.minimum(np.floor(x.max(1) - 0.5), w - 1)
    y0 = np.maximum(np.ceil(y.min(1) - 0.5), 0)
    y1 = np.minimum(np.floor(y.max(1) - 0.5), h - 1)
    ok = (np.abs(area) > 1e-12) & (x1 >= x0) & (y1 >= y0) & np.isfinite(area)
    ok &= (zt.min(1) <= far)
    idx = np.nonzero(ok)[0]
    coef = _bary_coeffs(x[idx], y[idx], area[idx])
    invz = 1.0 / zt[idx]
    x0, y0 = x0[idx].astype(np.int64), y0[idx].astype(np.int64)
    x1, y1 = x1[idx].astype(np.int64), y1[idx].astype(np.int64)
    size = np.maximum(x1 - x0, y1 - y0) + 1
    bucket = np.searchsorted(np.array(WINDOWS), size)  # smallest window >= size
    pix_parts, z_parts, own_parts = [], [], []
    for b, S in enumerate(WINDOWS):
        sel = np.nonzero(bucket == b)[0]
        if not len(sel):
            continue
        oy, ox = np.divmod(np.arange(S * S), S)
        step = max(1, CHUNK_SAMPLES // (S * S))
        for s0 in range(0, len(sel), step):
            t = sel[s0 : s0 + step]
            px = x0[t, None] + ox[None, :]
            py = y0[t, None] + oy[None, :]
            inb = (px <= x1[t, None]) & (py <= y1[t, None])
            _accumulate(t, px, py, inb, coef, invz, far, w, pix_parts, z_parts, own_parts)
    for t in np.nonzero(bucket == len(WINDOWS))[0]:
        # Large triangle: its full (screen-clipped) bbox, a row band at a time.
        xs = np.arange(x0[t], x1[t] + 1)
        band = max(1, CHUNK_SAMPLES // len(xs))
        for r0 in range(int(y0[t]), int(y1[t]) + 1, band):
            ys = np.arange(r0, min(r0 + band, int(y1[t]) + 1))
            py, px = np.meshgrid(ys, xs, indexing="ij")
            tt = np.array([t])
            _accumulate(tt, px.reshape(1, -1), py.reshape(1, -1), None, coef, invz, far, w,
                        pix_parts, z_parts, own_parts)
    if pix_parts:
        pix = np.concatenate(pix_parts)
        zc = np.concatenate(z_parts)
        own = np.concatenate(own_parts)
        np.minimum.at(zbuf, pix, zc)
        win = np.nonzero(zc == zbuf[pix])[0]
        # Exact z ties (shared edges): the lowest candidate index wins (candidates are
        # in a fixed order: bucket, chunk, triangle, window offset). ``ufunc.at`` is
        # defined for repeated indices, unlike repeated-index assignment.
        first = np.full(h * w, np.iinfo(np.int64).max, dtype=np.int64)
        np.minimum.at(first, pix[win], win)
        hit = first < np.iinfo(np.int64).max
        owner[hit] = own[first[hit]]
    return _Layer(zbuf, owner, F[idx], coef, invz, area[idx], src[idx])


def _shade(layer: _Layer, p: np.ndarray, mesh: Mesh, colors, texture, w: int, h: int) -> np.ndarray:
    """Perspective-correct colours (Nx3 uint8) of pixels ``p`` (flat indices) owned in ``layer``."""
    t = layer.owner[p]
    py, px = np.divmod(p, w)
    pcx, pcy = px + 0.5, py + 0.5
    c = layer.coef[t]
    bw = c[:, :, 0] * pcx[:, None] + c[:, :, 1] * pcy[:, None] + c[:, :, 2]
    bw = np.clip(bw, 0.0, None) * layer.invz[t]
    bw /= bw.sum(1, keepdims=True)
    fv = layer.faces[t]
    if texture is not None:
        # Same clamped bilinear as ``_bilinear`` via cv2.remap (INTER_LINEAR +
        # BORDER_REPLICATE; OpenCV uses 5-bit fixed-point weights -> <= ~1/255 diff).
        tex = np.ascontiguousarray(np.asarray(texture)[..., :3])
        th, tw = tex.shape[:2]
        uv = np.einsum("nv,nvc->nc", bw, mesh.uv[fv].astype(np.float64))
        mx = np.full(h * w, -1.0, np.float32)
        my = np.full(h * w, -1.0, np.float32)
        mx[p] = uv[:, 0] * tw - 0.5
        my[p] = uv[:, 1] * th - 0.5
        samp = cv2.remap(tex, mx.reshape(h, w), my.reshape(h, w), cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REPLICATE)
        return samp.reshape(-1, 3)[p]
    val = np.einsum("nv,nvc->nc", bw, colors[fv].astype(np.float64))
    return np.clip(np.rint(val), 0, 255).astype(np.uint8)


def rasterize(
    mesh: Mesh,
    colors: np.ndarray | None,
    K_out: np.ndarray,
    T_cw: np.ndarray,
    w: int,
    h: int,
    near: float = NEAR,
    far: float = FAR,
    texture: np.ndarray | None = None,
) -> Frame:
    """Render ``mesh`` from ``T_cw`` with intrinsics ``K_out``.

    Color source: with ``texture`` (the source image, HxWx3 uint8) each covered pixel gets
    the perspective-correct interpolated ``mesh.uv`` and samples the texture bilinearly
    (full source resolution at any mesh stride; identity pose == exact cover-fit warp).
    Otherwise per-vertex ``colors`` (Nx3 uint8) are interpolated perspective-correctly.
    Regular faces render first; edge faces fill only pixels no regular face covers and are
    valid only under the stretch rule (module docstring).
    """
    if texture is None and colors is None:
        raise ValueError("colors or texture is required")
    T = np.asarray(T_cw, dtype=np.float64)
    K = np.asarray(K_out, dtype=np.float64)
    V = mesh.vertices.astype(np.float64)
    zv, sx, sy = _project(V, T, K)
    F = np.asarray(mesh.faces, dtype=np.intp).reshape(-1, 3)
    reg = _raster_layer(F, zv, sx, sy, w, h, near, far)
    valid = reg.owner >= 0
    zbuf = np.where(valid, reg.zbuf, np.inf)
    rgb = np.zeros((h * w, 3), np.uint8)
    if valid.any():
        p = np.nonzero(valid)[0]
        rgb[p] = _shade(reg, p, mesh, colors, texture, w, h)
    E = np.asarray(getattr(mesh, "edge_faces", np.zeros((0, 3))), dtype=np.intp).reshape(-1, 3)
    if len(E) and not valid.all():
        edge = _raster_layer(E, zv, sx, sy, w, h, near, far)
        take = (~valid) & (edge.owner >= 0)
        if take.any():
            _, rx, ry = _project(V, np.eye(4), K)
            ref = _signed_area(rx[E], ry[E])[edge.src]
            with np.errstate(divide="ignore", invalid="ignore"):
                stretch = edge.area / ref
            face_ok = np.isfinite(stretch) & (stretch > 0) & (stretch <= STRETCH_VALID)
            p = np.nonzero(take)[0]
            rgb[p] = _shade(edge, p, mesh, colors, texture, w, h)  # colour kept even when invalid
            good = p[face_ok[edge.owner[p]]]
            valid[good] = True
            zbuf[good] = edge.zbuf[good]
    z = np.where(valid, zbuf, np.inf).astype(np.float32)
    return Frame(rgb.reshape(h, w, 3), z.reshape(h, w), valid.reshape(h, w))


def _accumulate(t, px, py, inb, coef, invz, far, w, pix_parts, z_parts, own_parts) -> None:
    """Coverage + perspective-correct z for candidate pixels of triangles ``t``."""
    cx = px + 0.5
    cy = py + 0.5
    c = coef[t]
    b0 = c[:, 0, 0, None] * cx + c[:, 0, 1, None] * cy + c[:, 0, 2, None]
    b1 = c[:, 1, 0, None] * cx + c[:, 1, 1, None] * cy + c[:, 1, 2, None]
    b2 = 1.0 - b0 - b1
    m = (b0 >= -COVER_EPS) & (b1 >= -COVER_EPS) & (b2 >= -COVER_EPS)
    if inb is not None:
        m &= inb
    iz = invz[t]
    with np.errstate(divide="ignore"):
        zc = 1.0 / (b0 * iz[:, 0, None] + b1 * iz[:, 1, None] + b2 * iz[:, 2, None])
    m &= (zc > 0) & (zc <= far)
    rr, cc = np.nonzero(m)
    pix_parts.append(py[rr, cc] * w + px[rr, cc])
    z_parts.append(zc[rr, cc])
    own_parts.append(t[rr])


def nearest_valid_index(valid: np.ndarray) -> np.ndarray:
    """For every pixel, the flat index of the nearest valid pixel (exact L2 distance
    transform labels, OpenCV ``DIST_LABEL_PIXEL``; deterministic). Requires a valid pixel."""
    v = np.asarray(valid, bool)
    if not v.any():
        raise ValueError("no valid pixel")
    src = np.where(v, 0, 255).astype(np.uint8)
    _, labels = cv2.distanceTransformWithLabels(src, cv2.DIST_L2, cv2.DIST_MASK_PRECISE,
                                                labelType=cv2.DIST_LABEL_PIXEL)
    lut = np.zeros(int(labels.max()) + 1, np.int64)
    flat = np.nonzero(v.ravel())[0]
    lut[labels.ravel()[flat]] = flat
    return lut[labels.ravel()].reshape(v.shape)


def fill_small_holes(frame: Frame, max_px: int = 4) -> tuple[Frame, np.ndarray]:
    """Inpaint small holes: 8-connected invalid components whose bounding box is at most
    ``max_px`` pixels in BOTH width and height. RGB: ``cv2.inpaint`` TELEA (radius 3) with
    all holes masked so big holes never feed colors; z: nearest valid value. Larger holes
    stay invalid. Returns ``(new_frame, filled_mask)``; the input is unchanged."""
    holes = ~frame.valid
    filled = np.zeros_like(holes)
    rgb, z, valid = frame.rgb.copy(), frame.z.copy(), frame.valid.copy()
    if not holes.any() or holes.all():
        return Frame(rgb, z, valid), filled
    _, labels, stats, _ = cv2.connectedComponentsWithStats(holes.astype(np.uint8), connectivity=8)
    small = (stats[:, cv2.CC_STAT_WIDTH] <= max_px) & (stats[:, cv2.CC_STAT_HEIGHT] <= max_px)
    small[0] = False  # background label
    filled = small[labels] & holes
    if filled.any():
        inp = cv2.inpaint(np.ascontiguousarray(frame.rgb), holes.astype(np.uint8), 3,
                          cv2.INPAINT_TELEA)
        rgb[filled] = inp[filled]
        near = nearest_valid_index(frame.valid)
        z[filled] = frame.z.ravel()[near[filled]]
        valid |= filled
    return Frame(rgb, z, valid), filled


def render_sequence(
    mesh: Mesh, colors: np.ndarray | None, compiled, w: int, h: int,
    texture: np.ndarray | None = None,
) -> Iterator[Frame]:
    """Yield one ``Frame`` per ``compiled.samples`` pose (streams; never holds all frames).
    ``(w, h)`` must equal the compiled path's output size (its ``K`` is for that size)."""
    if compiled.status == "invalid" or not compiled.samples:
        raise ValueError("compiled path is invalid or has no samples")
    s2o = compiled.source_to_output or {}
    if (s2o.get("out_w"), s2o.get("out_h")) != (w, h):
        raise ValueError("render size does not match the compiled path's output size")
    K = np.asarray(compiled.K, dtype=np.float64)
    for s in compiled.samples:
        yield rasterize(mesh, colors, K, np.asarray(s["T_cw"], dtype=np.float64), w, h,
                        texture=texture)


def hole_ratio(frame: Frame) -> float:
    """Fraction of pixels with ``valid=False``."""
    return float(1.0 - np.count_nonzero(frame.valid) / frame.valid.size)


def target_visibility(
    frame: Frame,
    target_world: np.ndarray,
    K_out: np.ndarray,
    T_cw: np.ndarray,
    patch_points: np.ndarray | None = None,
    tol: float = 0.02,
) -> float:
    """Fraction of target patch points (world Nx3; ``None`` -> just ``target_world``) that
    are in front (``z > NEAR``), inside the frame, and pass the z-test
    ``z_point <= frame.z * (1 + tol)`` at their pixel."""
    P = np.asarray(target_world if patch_points is None else patch_points, float).reshape(-1, 3)
    if not len(P):
        return 0.0
    T = np.asarray(T_cw, float)
    Xc = P @ T[:3, :3].T + T[:3, 3]
    z = Xc[:, 2]
    h, w = frame.valid.shape
    vis = np.zeros(len(P), bool)
    front = z > NEAR
    if front.any():
        hc = Xc[front] @ np.asarray(K_out, float).T
        u, v = hc[:, 0] / hc[:, 2], hc[:, 1] / hc[:, 2]
        inside = (u >= 0) & (u < w) & (v >= 0) & (v < h)
        ok = np.zeros(len(u), bool)
        col = np.floor(u[inside]).astype(np.intp)
        row = np.floor(v[inside]).astype(np.intp)
        zf = frame.z[row, col].astype(np.float64)
        ok[inside] = frame.valid[row, col] & (z[front][inside] <= zf * (1.0 + tol))
        vis[front] = ok
    return float(vis.mean())
