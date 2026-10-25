/**
 * Browser scene prep + preview mesh: TS port of `open_creative.camera_sketch.scene`
 * (`depth_to_z`, `prepare_z`, `build_mesh`) and of the edge-face stretch rule in `render.py`.
 *
 * The server renderer is the reference (spec §5). Everything here keeps Python's conventions:
 * - Depth PNG16 is linear `min..max -> 0..65535` (`decode_depth_png16`), decoded with fast-png
 *   (a canvas would quantize to 8 bits).
 * - `prepareZ`: positive finite pixels only; numpy-linear percentile clip; optional edge-aware
 *   smoothing of log z (a port of OpenCV's float `bilateralFilter`, d=5, sigmaColor=0.1,
 *   sigmaSpace=3, BORDER_REFLECT_101, 4096-bin exp LUT); target patch median -> 1; cap 50.
 *   Arrays are Float32Array, row-major, HxW; non-finite pixels stay NaN.
 * - `buildPreviewMesh`: edge-aligned grid `x_j = min(j*stride, W)`, z sampled at the clamped pixel
 *   `z[clamp(floor(y)), clamp(floor(x))]`, `uv = (x/W, y/H)`, cells split `(a,c,b)`,`(b,c,d)`;
 *   faces with a non-finite vertex are dropped, faces with `max/min - 1 > threshold` go to the
 *   edge layer. Positions are OpenCV camera/world coordinates (convert with `three.ts`).
 */
import { decode } from "fast-png";
import type { Mat3, Mat4 } from "./camera";

export const Z_FAR_CAP = 50;
export const STRETCH_VALID = 1.5;
/** Browser preview budget: regular triangles per mesh (spec §13: low mesh resolution, 30 fps). */
export const MAX_PREVIEW_TRIANGLES = 60_000;

export interface DepthConvention {
  kind: "inverse_relative" | "depth_relative";
  scale: number;
  offset: number;
  eps: number;
}
export const DAV2_SMALL: DepthConvention = { kind: "inverse_relative", scale: 1, offset: 0, eps: 1e-6 };

const f32 = Math.fround;

/** `depth` asset meta `convention` -> DepthConvention (missing fields take Python's defaults). */
export function conventionFromMeta(c: unknown): DepthConvention {
  const o = (c ?? {}) as Record<string, unknown>;
  if (o.kind !== "inverse_relative" && o.kind !== "depth_relative") throw new Error("depth convention is missing or unknown");
  const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);
  return { kind: o.kind, scale: num(o.scale, 1), offset: num(o.offset, 0), eps: num(o.eps, 1e-6) };
}

/** 16-bit grayscale PNG -> float32 `min + q / 65535 * (max - min)` (Python `decode_depth_png16`). */
export function decodeDepthPng16(bytes: ArrayBuffer | Uint8Array, meta: { min: number; max: number }): { data: Float32Array; width: number; height: number } {
  const img = decode(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes));
  if (img.depth !== 16 || img.channels !== 1 || !(img.data instanceof Uint16Array)) throw new Error("not a 16-bit grayscale png");
  const { min: lo, max: hi } = meta;
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) throw new Error("depth meta min/max must be finite");
  const q = img.data;
  const out = new Float32Array(q.length);
  for (let i = 0; i < q.length; i++) out[i] = lo + (q[i] / 65535) * (hi - lo);
  return { data: out, width: img.width, height: img.height };
}

/** `scene.depth_to_z` (float32 arithmetic like numpy). */
export function depthToZ(d: Float32Array, conv: DepthConvention): Float32Array {
  const z = new Float32Array(d.length);
  const eps = f32(conv.eps);
  for (let i = 0; i < d.length; i++) {
    const a = Math.max(f32(d[i] + conv.offset), eps); // NaN stays NaN
    z[i] = conv.kind === "inverse_relative" ? conv.scale / a : conv.scale * a;
  }
  return z;
}

/** `scene_load.edge_rel_threshold`: edge protection 0..1 -> relative depth-jump threshold. */
export const edgeRelThreshold = (edgeProtection: number) => 0.12 - 0.08 * edgeProtection;

/** Python `round` (half to even) for non-negative x. */
function roundHalfEven(x: number): number {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

/**
 * k-th smallest (0-based) of `a[0..n)`, partially reordering `a` in place (Hoare quickselect with a
 * median-of-3 pivot). Afterwards every element in `a[k+1..n)` is >= the result. O(n), no sort.
 */
function selectK(a: Float64Array, n: number, k: number): number {
  let lo = 0, hi = n - 1;
  while (hi > lo) {
    const x = a[lo], y = a[(lo + hi) >> 1], z = a[hi];
    const pivot = x < y ? (y < z ? y : x < z ? z : x) : x < z ? x : y < z ? z : y;
    let i = lo, j = hi;
    while (i <= j) {
      while (a[i] < pivot) i++;
      while (a[j] > pivot) j--;
      if (i <= j) {
        const t = a[i]; a[i] = a[j]; a[j] = t;
        i++; j--;
      }
    }
    if (k <= j) hi = j;
    else if (k >= i) lo = i;
    else return a[k];
  }
  return a[k];
}

/** The k-th and (k+1)-th smallest of `a[0..n)` (the second equals the first when k = n-1). */
function selectPair(a: Float64Array, n: number, k: number): [number, number] {
  const v0 = selectK(a, n, k);
  let v1 = Infinity;
  for (let i = k + 1; i < n; i++) if (a[i] < v1) v1 = a[i];
  return [v0, k + 1 < n ? v1 : v0];
}

/** numpy "linear" percentile (`_lerp` with the t >= 0.5 branch) of `a[0..n)`; reorders `a`. */
function percentileOf(a: Float64Array, n: number, p: number): number {
  const idx = (p / 100) * (n - 1);
  const lo = Math.floor(idx);
  const [v0, v1] = selectPair(a, n, lo);
  const t = idx - lo, d = v1 - v0;
  return t >= 0.5 ? v1 - d * (1 - t) : v0 + d * t;
}

/** numpy median of float32 data (even n: float32 mean of the middle pair); reorders `a`. */
function medianOf(a: Float64Array, n = a.length): number {
  if (n % 2) return selectK(a, n, (n - 1) / 2);
  const [v0, v1] = selectPair(a, n, n / 2 - 1);
  return f32((v0 + v1) / 2);
}

/**
 * Port of OpenCV `bilateralFilter` for single-channel CV_32F (BORDER_REFLECT_101): circular
 * window of radius `d/2`, Gaussian space weights, colour weights from a 4096-bin exp LUT over the
 * image's value range with linear interpolation. Accumulates in float64 (OpenCV: float32), so
 * results differ in the last bits.
 */
export function bilateralFilter32f(src: Float32Array, w: number, h: number, d: number, sigmaColor: number, sigmaSpace: number): Float32Array {
  let lo = Infinity, hi = -Infinity;
  for (const v of src) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (Math.abs(lo - hi) < 1.1920929e-7) return Float32Array.from(src);
  const radius = Math.max(d > 0 ? Math.floor(d / 2) : Math.round(sigmaSpace * 1.5), 1);
  const gc = -0.5 / (sigmaColor * sigmaColor), gs = -0.5 / (sigmaSpace * sigmaSpace);
  const nBins = 1 << 12;
  const len = f32(hi - lo);
  const scaleIndex = f32(nBins / len);
  const lut = new Float32Array(nBins + 2);
  let last = 1;
  for (let i = 0; i < nBins + 2; i++) {
    if (last > 0) {
      const v = i / scaleIndex;
      lut[i] = Math.exp(v * v * gc);
      last = lut[i];
    } else lut[i] = 0;
  }
  const ox: number[] = [], oy: number[] = [], sw: number[] = [];
  for (let i = -radius; i <= radius; i++) {
    for (let j = -radius; j <= radius; j++) {
      const r = Math.sqrt(i * i + j * j);
      if (r > radius) continue;
      oy.push(i); ox.push(j); sw.push(f32(Math.exp(r * r * gs)));
    }
  }
  const refl = (k: number, n: number) => (n === 1 ? 0 : k < 0 ? -k : k >= n ? 2 * n - 2 - k : k);
  const out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const c = src[y * w + x];
      let sum = 0, wsum = 0;
      for (let k = 0; k < sw.length; k++) {
        const v = src[refl(y + oy[k], h) * w + refl(x + ox[k], w)];
        if (Number.isNaN(v)) continue;
        let a = f32(Math.abs(v - c) * scaleIndex);
        const idx = Math.floor(a);
        a -= idx;
        const wk = sw[k] * (lut[idx] + a * (lut[idx + 1] - lut[idx]));
        wsum += wk;
        sum += v * wk;
      }
      out[y * w + x] = sum / wsum;
    }
  }
  return out;
}

/** `scene._target_patch_median`. */
function targetPatchMedian(z: Float32Array, w: number, h: number, uv: readonly number[]): number {
  const r = Math.max(1, roundHalfEven(0.02 * Math.min(h, w)));
  const clamp = (v: number, hiV: number) => Math.min(Math.max(v, 0), hiV);
  const buf = new Float64Array((2 * r + 1) ** 2);
  const patch = (cy: number, cx: number) => {
    let n = 0;
    for (let y = Math.max(0, cy - r); y <= Math.min(h - 1, cy + r); y++) {
      for (let x = Math.max(0, cx - r); x <= Math.min(w - 1, cx + r); x++) {
        const v = z[y * w + x];
        if (Number.isFinite(v)) buf[n++] = v;
      }
    }
    return n;
  };
  const cx = clamp(Math.floor(uv[0] * w), w - 1), cy = clamp(Math.floor(uv[1] * h), h - 1);
  let n = patch(cy, cx);
  if (!n) {
    // Nearest finite pixel (squared Euclidean; ties -> first in row-major order).
    let best = Infinity, by = -1, bx = -1;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (!Number.isFinite(z[y * w + x])) continue;
        const dd = (y - cy) ** 2 + (x - cx) ** 2;
        if (dd < best) { best = dd; by = y; bx = x; }
      }
    }
    if (by < 0) throw new Error("depth has no finite values");
    n = patch(by, bx);
  }
  return medianOf(buf, n);
}

export interface PrepareZOptions {
  clipPct?: [number, number];
  /** Edge-aware smoothing of log z (Python default true). */
  smooth?: boolean;
}

/**
 * First half of `scene.prepare_z` (independent of the target): positive finite pixels only,
 * percentile clip, optional bilateral smoothing of log z. Non-finite pixels -> NaN. Throws when
 * no pixel is finite and positive. Cache this per depth asset; target moves only re-normalize.
 */
export function clipSmoothZ(zIn: Float32Array, w: number, h: number, opts: PrepareZOptions = {}): Float32Array {
  const [pLo, pHi] = opts.clipPct ?? [1, 99];
  const smooth = opts.smooth ?? true;
  const n = w * h;
  if (zIn.length !== n) throw new Error("depth size mismatch");
  const finite = new Uint8Array(n);
  const vals = new Float64Array(n);
  let k = 0;
  for (let i = 0; i < n; i++) {
    const v = zIn[i];
    if (Number.isFinite(v) && v > 0) { finite[i] = 1; vals[k++] = v; }
  }
  if (!k) throw new Error("depth has no finite values");
  const lo = f32(percentileOf(vals, k, pLo)), hi = f32(percentileOf(vals, k, pHi));
  const z = new Float32Array(n);
  for (let i = 0; i < n; i++) z[i] = finite[i] ? Math.min(Math.max(zIn[i], lo), hi) : NaN;
  if (!smooth) return z;
  const lz = new Float32Array(n);
  let m = 0;
  for (let i = 0; i < n; i++) {
    lz[i] = Math.log(z[i]);
    if (finite[i]) vals[m++] = lz[i]; // reuse the value buffer
  }
  const fill = f32(medianOf(vals, m));
  for (let i = 0; i < n; i++) if (!finite[i]) lz[i] = fill;
  const sm = bilateralFilter32f(lz, w, h, 5, 0.1, 3.0);
  for (let i = 0; i < n; i++) z[i] = finite[i] ? Math.exp(sm[i]) : NaN;
  return z;
}

/** Second half of `scene.prepare_z`: target patch median -> 1, cap at `Z_FAR_CAP` (NaN stays NaN). */
export function normalizeZ(z: Float32Array, w: number, h: number, targetUv01: readonly number[]): { z: Float32Array; targetRaw: number } {
  const targetRaw = targetPatchMedian(z, w, h, targetUv01);
  const t = f32(targetRaw);
  const out = new Float32Array(z.length);
  for (let i = 0; i < z.length; i++) out[i] = Math.min(f32(z[i] / t), Z_FAR_CAP);
  return { z: out, targetRaw };
}

/**
 * `scene.prepare_z`: percentile clip, (optional) bilateral smoothing of log z, normalize the
 * target patch median to 1, cap at `Z_FAR_CAP`. Returns `{z, targetRaw}`; throws when no pixel
 * is finite and positive.
 */
export function prepareZ(zIn: Float32Array, w: number, h: number, targetUv01: readonly number[], opts: PrepareZOptions = {}): { z: Float32Array; targetRaw: number } {
  return normalizeZ(clipSmoothZ(zIn, w, h, opts), w, h, targetUv01);
}

/** `scene_load`: `z_norm ** depth_contrast` (target stays 1); returns the input when contrast is 1. */
export function applyDepthContrast(z: Float32Array, contrast: number): Float32Array {
  if (contrast === 1) return z;
  const out = new Float32Array(z.length);
  for (let i = 0; i < z.length; i++) out[i] = Math.pow(z[i], contrast);
  return out;
}

export interface PreviewMesh {
  /** N x 3 OpenCV coordinates (x right, y down, z forward); non-finite-z vertices at the origin. */
  positions: Float32Array;
  /** N x 2 `(x / W, y / H)`, image y down. */
  uvs: Float32Array;
  /** Regular (connected) faces, M x 3. */
  index: Uint32Array;
  /** Depth-jump faces (fallback edge layer), E x 3. */
  edgeIndex: Uint32Array;
  gridW: number;
  gridH: number;
}

function inv3(K: Mat3): number[][] {
  const [[a, b, c], [d, e, f], [g, h, i]] = K;
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  return [
    [A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
    [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
    [C / det, -(a * h - b * g) / det, (a * e - b * d) / det],
  ];
}

const gridCoords = (n: number, stride: number) =>
  Array.from({ length: Math.ceil(n / stride) + 1 }, (_, j) => Math.min(j * stride, n));

/** `build_mesh`'s vertex grid: coordinates and the z samples at the clamped pixels (row-major). */
export interface DepthGrid {
  xs: number[];
  ys: number[];
  /** z at `(min(ys[i], H-1), min(xs[j], W-1))`, length `ys.length * xs.length`. */
  zg: Float32Array;
  w: number;
  h: number;
  stride: number;
}

/** Sample `z` (HxW) at the edge-aligned grid of `stride` (once per depth map and stride). */
export function sampleGrid(z: Float32Array, w: number, h: number, stride: number): DepthGrid {
  if (!(stride >= 1) || !Number.isInteger(stride)) throw new Error("stride must be an integer >= 1");
  if (z.length !== w * h) throw new Error("depth size mismatch");
  const ys = gridCoords(h, stride), xs = gridCoords(w, stride);
  const zg = new Float32Array(ys.length * xs.length);
  for (let i = 0; i < ys.length; i++) {
    const row = Math.min(ys[i], h - 1) * w;
    for (let j = 0; j < xs.length; j++) zg[i * xs.length + j] = z[row + Math.min(xs[j], w - 1)];
  }
  return { xs, ys, zg, w, h, stride };
}

/**
 * Per-edit part of `scene_load` + `prepare_z` restricted to the grid samples (exact for what the
 * mesh reads, since normalization, cap and contrast are pointwise):
 * `z = min(f32(z / f32(targetRaw)), Z_FAR_CAP) ** contrast`.
 */
export function normalizeGrid(grid: DepthGrid, targetRaw: number, contrast = 1): DepthGrid {
  const t = f32(targetRaw);
  const zg = new Float32Array(grid.zg.length);
  for (let i = 0; i < zg.length; i++) {
    const v = f32(Math.min(f32(grid.zg[i] / t), Z_FAR_CAP));
    zg[i] = contrast === 1 ? v : Math.pow(v, contrast);
  }
  return { ...grid, zg };
}

/** `scene.build_mesh` from grid samples (same vertex order, face order and discontinuity rule). */
export function meshFromGrid(grid: DepthGrid, K: Mat3, edgeRelThreshold = 0.08): PreviewMesh {
  const { xs, ys, zg, w, h } = grid;
  const gh = ys.length, gw = xs.length;
  const Ki = inv3(K);
  const positions = new Float32Array(gh * gw * 3);
  const uvs = new Float32Array(gh * gw * 2);
  for (let i = 0; i < gh; i++) {
    const v = ys[i];
    for (let j = 0; j < gw; j++) {
      const u = xs[j], k = i * gw + j;
      const zv = zg[k];
      const s = Number.isFinite(zv) ? zv : 0;
      for (let r = 0; r < 3; r++) positions[k * 3 + r] = (Ki[r][0] * u + Ki[r][1] * v + Ki[r][2]) * s;
      uvs[k * 2] = u / w;
      uvs[k * 2 + 1] = v / h;
    }
  }
  // At most 2 faces per cell; trimmed copies below.
  const cells = (gh - 1) * (gw - 1);
  const reg = new Uint32Array(cells * 6), edge = new Uint32Array(cells * 6);
  let nr = 0, ne = 0;
  const push = (p: number, q: number, r: number) => {
    const za = zg[p], zb = zg[q], zc = zg[r];
    if (!Number.isFinite(za) || !Number.isFinite(zb) || !Number.isFinite(zc)) return;
    const rel = Math.max(za, zb, zc) / Math.min(za, zb, zc) - 1;
    // NaN rel (e.g. 0/0) -> not <= threshold -> edge layer, like numpy's `keep = rel <= thr`.
    if (rel <= edgeRelThreshold) { reg[nr++] = p; reg[nr++] = q; reg[nr++] = r; }
    else { edge[ne++] = p; edge[ne++] = q; edge[ne++] = r; }
  };
  for (let i = 0; i < gh - 1; i++) {
    for (let j = 0; j < gw - 1; j++) {
      const a = i * gw + j, b = a + 1, c = a + gw, d = c + 1;
      push(a, c, b);
      push(b, c, d);
    }
  }
  return { positions, uvs, index: reg.slice(0, nr), edgeIndex: edge.slice(0, ne), gridW: gw, gridH: gh };
}

/** Port of `scene.build_mesh` on a full z map (`sampleGrid` + `meshFromGrid`). */
export function buildPreviewMesh(z: Float32Array, w: number, h: number, K: Mat3, stride: number, edgeRelThreshold = 0.08): PreviewMesh {
  return meshFromGrid(sampleGrid(z, w, h, stride), K, edgeRelThreshold);
}

/** Prepared once per depth asset (decode, cap, clip, smooth) — see `prepareDepthBase`. */
export interface DepthBase {
  /** Clipped + smoothed z at the capped size (NaN = invalid), not yet target-normalized. */
  z: Float32Array;
  width: number;
  height: number;
  /** Size of the source depth before the cap (= scene image size). */
  srcWidth: number;
  srcHeight: number;
}

/**
 * Per editor change (target, contrast, edge protection, focal): target patch median from the base
 * map (a small patch), then normalization/cap/contrast at the grid samples only, then the mesh.
 * Never touches the full map beyond the target patch. `Ksrc` is for the SOURCE size; it is scaled
 * to the capped size like `scene_load.load_scene_info(max_side)`.
 */
export function previewMeshForEdit(base: DepthBase, grid: DepthGrid, targetUv01: readonly number[], contrast: number, Ksrc: Mat3,
  edgeRelThreshold: number): { mesh: PreviewMesh; targetRaw: number; K: Mat3 } {
  const targetRaw = targetPatchMedian(base.z, base.width, base.height, targetUv01);
  const sx = base.width / base.srcWidth, sy = base.height / base.srcHeight;
  const K: Mat3 = [Ksrc[0].map((v) => v * sx), Ksrc[1].map((v) => v * sy), [...Ksrc[2]]];
  return { mesh: meshFromGrid(normalizeGrid(grid, targetRaw, contrast), K, edgeRelThreshold), targetRaw, K };
}

/** Long-side cap for the browser depth map (server `load_scene_info(max_side=...)` analogue). */
export const MAX_DEPTH_SIDE = 2048;

/** Capped size like `scene_load`: `f = max_side / max(w, h)`, `round` half-to-even, >= 1; unchanged when within the cap. */
export function cappedSize(w: number, h: number, maxSide = MAX_DEPTH_SIDE): [number, number] {
  if (Math.max(w, h) <= maxSide) return [w, h];
  const f = maxSide / Math.max(w, h);
  return [Math.max(1, roundHalfEven(w * f)), Math.max(1, roundHalfEven(h * f))];
}

/** Per output index: source indices and area weights (OpenCV INTER_AREA, downscale). */
function areaWeights(n: number, m: number): { idx: Int32Array; wt: Float64Array; off: Int32Array } {
  const scale = n / m;
  const idx: number[] = [], wt: number[] = [], off = new Int32Array(m + 1);
  for (let o = 0; o < m; o++) {
    const a = o * scale, b = Math.min((o + 1) * scale, n);
    for (let s = Math.floor(a); s < Math.ceil(b); s++) {
      const cover = Math.min(b, s + 1) - Math.max(a, s);
      if (cover > 1e-12) { idx.push(s); wt.push(cover / (b - a)); }
    }
    off[o + 1] = idx.length;
  }
  return { idx: Int32Array.from(idx), wt: Float64Array.from(wt), off };
}

/** Area (box) downsample of a float map, separable; NaN propagates like OpenCV. */
export function resizeArea(src: Float32Array, w: number, h: number, nw: number, nh: number): Float32Array {
  if (nw === w && nh === h) return src;
  if (nw > w || nh > h) throw new Error("resizeArea only downsamples");
  const X = areaWeights(w, nw), Y = areaWeights(h, nh);
  const tmp = new Float64Array(h * nw);
  for (let y = 0; y < h; y++) {
    const row = y * w;
    for (let o = 0; o < nw; o++) {
      let acc = 0;
      for (let k = X.off[o]; k < X.off[o + 1]; k++) acc += src[row + X.idx[k]] * X.wt[k];
      tmp[y * nw + o] = acc;
    }
  }
  const out = new Float32Array(nw * nh);
  for (let o = 0; o < nh; o++) {
    for (let x = 0; x < nw; x++) {
      let acc = 0;
      for (let k = Y.off[o]; k < Y.off[o + 1]; k++) acc += tmp[Y.idx[k] * nw + x] * Y.wt[k];
      out[o * nw + x] = acc;
    }
  }
  return out;
}

/**
 * One-time prep of a depth asset (worker-friendly, pure): decode PNG16 -> z -> area-downsample to
 * long side <= `maxSide` -> clip + smooth. The cap is applied BEFORE clip/smoothing, as the
 * server's `max_side` path does.
 */
export function prepareDepthBase(png: ArrayBuffer | Uint8Array, meta: { min: number; max: number; convention: unknown }, maxSide = MAX_DEPTH_SIDE): DepthBase {
  const d = decodeDepthPng16(png, meta);
  let z = depthToZ(d.data, conventionFromMeta(meta.convention));
  const [nw, nh] = cappedSize(d.width, d.height, maxSide);
  z = resizeArea(z, d.width, d.height, nw, nh);
  return { z: clipSmoothZ(z, nw, nh), width: nw, height: nh, srcWidth: d.width, srcHeight: d.height };
}

/** Smallest stride whose regular triangle count `2 ceil(W/s) ceil(H/s)` is <= `maxTriangles`. */
export function chooseStride(w: number, h: number, maxTriangles = MAX_PREVIEW_TRIANGLES): number {
  let s = Math.max(1, Math.floor(Math.sqrt((2 * w * h) / maxTriangles)));
  while (2 * Math.ceil(w / s) * Math.ceil(h / s) > maxTriangles) s++;
  while (s > 1 && 2 * Math.ceil(w / (s - 1)) * Math.ceil(h / (s - 1)) <= maxTriangles) s--;
  return s;
}

/** Twice the signed screen area of each face projected with `K` from `T` (render `_signed_area`). */
function signedAreas(positions: Float32Array, faces: Uint32Array, T: Mat4, K: Mat3, out: Float64Array): void {
  const px = (vi: number, res: number[]) => {
    const x = positions[vi * 3], y = positions[vi * 3 + 1], z = positions[vi * 3 + 2];
    const cx = T[0][0] * x + T[0][1] * y + T[0][2] * z + T[0][3];
    const cy = T[1][0] * x + T[1][1] * y + T[1][2] * z + T[1][3];
    const cz = T[2][0] * x + T[2][1] * y + T[2][2] * z + T[2][3];
    const hx = K[0][0] * cx + K[0][1] * cy + K[0][2] * cz, hy = K[1][0] * cx + K[1][1] * cy + K[1][2] * cz;
    const hz = K[2][0] * cx + K[2][1] * cy + K[2][2] * cz;
    res[0] = hx / hz;
    res[1] = hy / hz;
  };
  const p0 = [0, 0], p1 = [0, 0], p2 = [0, 0];
  for (let f = 0; f < faces.length / 3; f++) {
    px(faces[f * 3], p0); px(faces[f * 3 + 1], p1); px(faces[f * 3 + 2], p2);
    out[f] = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p2[0] - p0[0]) * (p1[1] - p0[1]);
  }
}

const IDENTITY: Mat4 = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];

/**
 * Per edge face `stretch = A / A_ref` (render.py): `A` its signed screen area at `Tcw`, `A_ref`
 * at the identity pose, both under `Kout`. Pass `ref` (from a previous call's `refOut`) to skip
 * recomputing the identity areas every frame.
 */
export function edgeFaceStretch(positions: Float32Array, edgeIndex: Uint32Array, Tcw: Mat4, Kout: Mat3,
  ref?: Float64Array, out?: Float32Array): Float32Array {
  const n = edgeIndex.length / 3;
  const r = ref ?? edgeFaceRefAreas(positions, edgeIndex, Kout);
  const a = new Float64Array(n);
  signedAreas(positions, edgeIndex, Tcw, Kout, a);
  const res = out ?? new Float32Array(n);
  for (let f = 0; f < n; f++) res[f] = a[f] / r[f];
  return res;
}

/** Identity-pose signed areas for `edgeFaceStretch` (depends only on the mesh and `Kout`). */
export function edgeFaceRefAreas(positions: Float32Array, edgeIndex: Uint32Array, Kout: Mat3): Float64Array {
  const r = new Float64Array(edgeIndex.length / 3);
  signedAreas(positions, edgeIndex, IDENTITY, Kout, r);
  return r;
}

/** Edge-face pixel validity: `0 < stretch <= STRETCH_VALID` (flipped or stretched -> hole). */
export const isStretchValid = (s: number) => Number.isFinite(s) && s > 0 && s <= STRETCH_VALID;
