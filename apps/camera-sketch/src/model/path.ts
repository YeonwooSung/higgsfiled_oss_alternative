/**
 * Sketch -> camera path compiler — TS port of `open_creative/camera_sketch/path.py`.
 *
 * Used for the instant in-browser preview (`compilePathProvisional`); the server's
 * `compile_path` result has the same `CompiledPath` shape and replaces it when it arrives.
 * Same algorithms and constants as Python (parity: fixtures/camera-sketch/parity.v1.json).
 */
import type { CameraPreset, CompiledPath } from "@oc/contracts";
import {
  coverFit,
  ease,
  framingRotation,
  fromUv01,
  interpolate,
  intrinsics,
  outputIntrinsics,
  pose,
  project,
  rotationOf,
  rotY,
  type Mat3,
  type Mat4,
  type Vec2,
  type Vec3,
} from "./camera";

export const SOLVER_VERSION = "cs_framing_ls_v1";
export const SCHEMA_VERSION = 1;
export const PARAMS = ["lateral_ratio", "push_ratio", "arc_deg"] as const;
export type ParamName = (typeof PARAMS)[number];
export const NEAR = 0.05;
export const FPS = 24;
export const MAX_RAW_POINTS = 128;
export const MAX_CONTROL_POINTS = 5;
export const MIN_CONTROL_POINTS = 2;
export const RDP_EPS0 = 0.005;
export const RDP_MAX_DOUBLINGS = 32;
export const CLOSED_DIST = 0.02;
export const CLOSED_MIN_LEN = 0.1;
export const BACKTRACK_COS = -0.5; // turn > 120 degrees
export const FRAMING_LO = 0.05;
export const FRAMING_HI = 0.95;
const NON_BLOCKING = new Set(["unsupported_sketch", "framing_clamped"]);
const MOTION_REASONS = new Set([
  "param_out_of_bounds", "framing_unsolvable", "camera_upside_down", "camera_turned_around", "target_behind_camera", "target_out_of_frame",
]);

/** Mirror of presets/camera-sketch/presets.v1.json `presets` (a test keeps them identical). */
export const PRESETS_V1: CameraPreset[] = [
  {
    id: "push_in", label_en: "Push in", label_ko: "푸시 인",
    bounds: { lateral_ratio: [0, 0], push_ratio: [0, 0.1], arc_deg: [0, 0] },
    default: { lateral_ratio: 0, push_ratio: 0.06, arc_deg: 0 },
  },
  {
    id: "slide", label_en: "Slide", label_ko: "슬라이드",
    bounds: { lateral_ratio: [-0.05, 0.05], push_ratio: [0, 0.1], arc_deg: [0, 0] },
    default: { lateral_ratio: 0.04, push_ratio: 0, arc_deg: 0 },
  },
  {
    id: "small_arc", label_en: "Small arc", label_ko: "작은 아크",
    bounds: { lateral_ratio: [0, 0], push_ratio: [0, 0.1], arc_deg: [-6, 6] },
    default: { lateral_ratio: 0, push_ratio: 0, arc_deg: 4 },
  },
];

// ---- sketch simplification -------------------------------------------------------------

type Pt = [number, number];
export type ControlPoint = [number, number, number];

/** Distance from p to segment ab (to `a` when `a == b`). */
function segDist(p: Pt, a: Pt, b: Pt): number {
  const ab0 = b[0] - a[0], ab1 = b[1] - a[1];
  const den = ab0 * ab0 + ab1 * ab1;
  const s = den === 0 ? 0 : Math.min(Math.max(((p[0] - a[0]) * ab0 + (p[1] - a[1]) * ab1) / den, 0), 1);
  return Math.hypot(p[0] - (a[0] + s * ab0), p[1] - (a[1] + s * ab1));
}

/** Iterative RDP; kept iff max point-to-segment distance `> eps` (first index on ties). */
function rdp(P: Pt[], eps: number): number[] {
  const keep = new Set([0, P.length - 1]);
  const stack: [number, number][] = [[0, P.length - 1]];
  while (stack.length) {
    const [i, j] = stack.pop()!;
    let best = -1, bestD = -1;
    for (let k = i + 1; k < j; k++) {
      const d = segDist(P[k], P[i], P[j]);
      if (d > bestD) {
        best = k;
        bestD = d;
      }
    }
    if (best >= 0 && bestD > eps) {
      keep.add(best);
      stack.push([i, best], [best, j]);
    }
  }
  return [...keep].sort((a, b) => a - b);
}

/**
 * Raw `(t, u, v)` sketch -> 2..5 control points `(t, u, v)` and an optional warning
 * (`"unsupported_sketch"` for a closed loop or a > 120 degree turn between ANY two control
 * segments). Stable sort by t, uniform downsample to <= 128, RDP with eps doubling from 0.005,
 * arc-length time renormalization; zero length -> static pair. See path.py for the full contract.
 */
export function simplifySketch(points: readonly (readonly number[])[]): { controlPoints: ControlPoint[]; warning: "unsupported_sketch" | null } {
  if (!points.length) throw new Error("sketch has no points");
  let pts = points.map((p) => [p[0], p[1], p[2]] as ControlPoint).sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  const n = pts.length;
  if (n > MAX_RAW_POINTS) pts = Array.from({ length: MAX_RAW_POINTS }, (_, k) => pts[Math.floor((k * (n - 1)) / (MAX_RAW_POINTS - 1))]);
  const P: Pt[] = pts.map((p) => [p[1], p[2]]);
  const cum = [0];
  for (let i = 1; i < P.length; i++) cum.push(cum[i - 1] + Math.hypot(P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1]));
  const total = cum[cum.length - 1];
  if (total <= 0) {
    const [, u0, v0] = pts[0];
    return { controlPoints: [[0, u0, v0], [1, u0, v0]], warning: null };
  }
  let eps = RDP_EPS0;
  let idx = rdp(P, eps);
  for (let r = 0; r < RDP_MAX_DOUBLINGS; r++) {
    if (idx.length <= MAX_CONTROL_POINTS) break;
    eps *= 2;
    idx = rdp(P, eps);
  }
  if (idx.length > MAX_CONTROL_POINTS) idx = [0, P.length - 1];
  const ctrl: ControlPoint[] = idx.map((i) => [cum[i] / total, pts[i][1], pts[i][2]]);
  ctrl[0] = [0, ctrl[0][1], ctrl[0][2]];
  ctrl[ctrl.length - 1] = [1, ctrl[ctrl.length - 1][1], ctrl[ctrl.length - 1][2]];
  return { controlPoints: ctrl, warning: shapeWarning(P, total, idx.map((i) => P[i])) };
}

/** `"unsupported_sketch"` when polyline `P` closes or control polyline `C` turns back > 120 degrees. */
function shapeWarning(P: Pt[], total: number, C: Pt[]): "unsupported_sketch" | null {
  let warning: "unsupported_sketch" | null = null;
  const last = P[P.length - 1];
  if (Math.hypot(last[0] - P[0][0], last[1] - P[0][1]) <= CLOSED_DIST && total > CLOSED_MIN_LEN) warning = "unsupported_sketch";
  const dirs: Pt[] = [];
  for (let k = 1; k < C.length; k++) dirs.push([C[k][0] - C[k - 1][0], C[k][1] - C[k - 1][1]]);
  for (let i = 0; i < dirs.length; i++) {
    for (let j = i + 1; j < dirs.length; j++) {
      const a = dirs[i], b = dirs[j];
      if (a[0] * b[0] + a[1] * b[1] < BACKTRACK_COS * (Math.hypot(a[0], a[1]) * Math.hypot(b[0], b[1]))) warning = "unsupported_sketch";
    }
  }
  return warning;
}

/** 2..5 finite points with `t` strictly increasing from exactly 0 to exactly 1. */
export function isControlPoints(points: readonly (readonly number[])[]): boolean {
  if (points.length < MIN_CONTROL_POINTS || points.length > MAX_CONTROL_POINTS) return false;
  if (!points.every((p) => p.every(Number.isFinite))) return false;
  if (points[0][0] !== 0 || points[points.length - 1][0] !== 1) return false;
  for (let i = 1; i < points.length; i++) if (!(points[i][0] > points[i - 1][0])) return false;
  return true;
}

/**
 * Sketch as given to the compiler -> control points + warning (path.py `sketch_control_points`):
 * 2..5 points are control points with USER times (kept as given; must pass `isControlPoints`,
 * else throws); 1 or > 5 points are a raw capture and go through `simplifySketch`.
 */
export function sketchControlPoints(points: readonly (readonly number[])[]): { controlPoints: ControlPoint[]; warning: "unsupported_sketch" | null } {
  if (points.length >= MIN_CONTROL_POINTS && points.length <= MAX_CONTROL_POINTS) {
    if (!isControlPoints(points)) throw new Error("control points need t strictly increasing from 0 to 1");
    const ctrl = points.map((p) => [p[0], p[1], p[2]] as ControlPoint);
    const C: Pt[] = ctrl.map((c) => [c[1], c[2]]);
    let total = 0;
    for (let i = 1; i < C.length; i++) total += Math.hypot(C[i][0] - C[i - 1][0], C[i][1] - C[i - 1][1]);
    return { controlPoints: ctrl, warning: shapeWarning(C, total, C) };
  }
  return simplifySketch(points);
}

/** Scale the sketch displacement about the first control point (times unchanged). */
export function scaleSketch(ctrl: readonly (readonly number[])[], s: number): ControlPoint[] {
  const u0 = ctrl[0][1], v0 = ctrl[0][2];
  return ctrl.map((c) => [c[0], u0 + s * (c[1] - u0), v0 + s * (c[2] - v0)] as ControlPoint);
}

// ---- framing least squares -------------------------------------------------------------

/** `A = I + lamVel D1'D1 + lamAcc D2'D2` (n x n). */
function system(n: number, lamVel: number, lamAcc: number): number[][] {
  const A = Array.from({ length: n }, (_, i) => Array.from({ length: n }, (_, j) => (i === j ? 1 : 0)));
  const addStencil = (w: number[], lam: number) => {
    for (let r = 0; r + w.length <= n; r++) {
      for (let a = 0; a < w.length; a++) for (let b = 0; b < w.length; b++) A[r + a][r + b] += lam * w[a] * w[b];
    }
  };
  if (n >= 2) addStencil([-1, 1], lamVel);
  if (n >= 3) addStencil([1, -2, 1], lamAcc);
  return A;
}

/** Dense solve with partial pivoting (small SPD systems). */
function solveLinear(M: number[][], rhs: number[]): number[] {
  const n = rhs.length;
  const A = M.map((row, i) => [...row, rhs[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    [A[c], A[p]] = [A[p], A[c]];
    for (let r = c + 1; r < n; r++) {
      const f = A[r][c] / A[c][c];
      for (let k = c; k <= n; k++) A[r][k] -= f * A[c][k];
    }
  }
  const x = new Array<number>(n).fill(0);
  for (let r = n - 1; r >= 0; r--) {
    let s = A[r][n];
    for (let k = r + 1; k < n; k++) s -= A[r][k] * x[k];
    x[r] = s / A[r][r];
  }
  return x;
}

function solveFixed(A: number[][], b: number[], fixed: Map<number, number>): number[] {
  const n = b.length;
  const x = Array.from({ length: n }, (_, i) => fixed.get(i) ?? 0);
  const free = x.map((_, i) => i).filter((i) => !fixed.has(i));
  if (free.length) {
    const F = [...fixed.keys()];
    const rhs = free.map((i) => b[i] - F.reduce((s, j) => s + A[i][j] * x[j], 0));
    const sol = solveLinear(free.map((i) => free.map((j) => A[i][j])), rhs);
    free.forEach((i, k) => (x[i] = sol[k]));
  }
  return x;
}

/**
 * Smoothed, box-bounded framing p (Nx2) for desired framing d (Nx2, uv01): per column
 * minimize `|p-d|^2 + lamVel |D1 p|^2 + lamAcc |D2 p|^2` with `p[0] = d[0]` fixed; up to 5
 * active-set rounds fixing violated entries at the bound (never released), then clip.
 */
export function solveFraming(desired: readonly (readonly number[])[], lamVel = 0.01, lamAcc = 0.1, lo = FRAMING_LO, hi = FRAMING_HI): number[][] {
  const n = desired.length;
  const A = system(n, lamVel, lamAcc);
  const out = desired.map((r) => [r[0], r[1]]);
  for (let col = 0; col < 2; col++) {
    const b = desired.map((r) => r[col]);
    const fixed = new Map<number, number>([[0, b[0]]]);
    let x = solveFixed(A, b, fixed);
    for (let round = 0; round < 5; round++) {
      const viol: number[] = [];
      for (let i = 1; i < n; i++) if (!fixed.has(i) && (x[i] < lo || x[i] > hi)) viol.push(i);
      if (!viol.length) break;
      for (const i of viol) fixed.set(i, x[i] < lo ? lo : hi);
      x = solveFixed(A, b, fixed);
    }
    for (let i = 1; i < n; i++) if (!fixed.has(i)) x[i] = Math.min(Math.max(x[i], lo), hi);
    x.forEach((v, i) => (out[i][col] = v));
  }
  return out;
}

// ---- preset curves ---------------------------------------------------------------------

/**
 * Camera center at eased progress s in [0,1]; every preset starts at the origin. slide:
 * `(lateral_ratio s, 0, 0)`; push_in: origin; small_arc: rotation about the vertical axis
 * through the target, positive arc -> +x. Push is additive on every preset: `z += push_ratio s`.
 */
export function presetCenter(preset: string, params: Partial<Record<ParamName, number>>, s: number, targetWorld: readonly number[]): Vec3 {
  let c: Vec3;
  if (preset === "slide") c = [(params.lateral_ratio ?? 0) * s, 0, 0];
  else if (preset === "push_in") c = [0, 0, 0];
  else if (preset === "small_arc") {
    const R = rotY(-(((params.arc_deg ?? 0) * s) * Math.PI) / 180);
    const T = targetWorld;
    c = [0, 1, 2].map((i) => T[i] + (R[i][0] * -T[0] + R[i][1] * -T[1] + R[i][2] * -T[2])) as Vec3;
  } else throw new Error(`unknown preset ${preset}`);
  c[2] += (params.push_ratio ?? 0) * s;
  return c;
}

// ---- compile ---------------------------------------------------------------------------

export interface SceneInfo {
  K_src: Mat3;
  src_w: number;
  src_h: number;
  /** Normalized scene units (target z ~ 1). */
  target_world: Vec3;
  /** In the SOURCE image. */
  target_uv01: Vec2;
  /** Recorded as-is in `CompiledPath.depth_mapping` (the server builds it from the depth asset). */
  depth_mapping?: CompiledPath["depth_mapping"];
}

/** Same construction as the API's `scene_info_from_meta`: `target_world = K^-1 [u w, v h, 1] * depthScale`. */
export function sceneInfoFromTarget(a: { focalMm: number; srcW: number; srcH: number; u: number; v: number; depthScale: number; sensorWMm?: number }): SceneInfo {
  const K = intrinsics(a.focalMm, a.srcW, a.srcH, a.sensorWMm ?? 36);
  const px = a.u * a.srcW, py = a.v * a.srcH;
  const y = (py - K[1][2]) / K[1][1];
  const x = (px - K[0][1] * y - K[0][2]) / K[0][0];
  return { K_src: K, src_w: a.srcW, src_h: a.srcH, target_world: [x * a.depthScale, y * a.depthScale, a.depthScale], target_uv01: [a.u, a.v] };
}

type SketchPointLike = { t: unknown; u: unknown; v: unknown } | readonly unknown[];

/** The §10 `path` config subset `compile_path` reads (values are validated at runtime). */
export interface PathConfig {
  preset: string;
  sketch_points?: readonly SketchPointLike[] | null;
  lateral_ratio?: number | null;
  push_ratio?: number | null;
  arc_deg?: number | null;
  easing?: string;
}

const isFiniteNumber = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);

/** numpy.interp for increasing `xp` (clamps outside the range). */
function interp1(x: number, xp: readonly number[], fp: readonly number[]): number {
  const n = xp.length;
  if (x < xp[0]) return fp[0];
  if (x > xp[n - 1]) return fp[n - 1];
  if (x === xp[n - 1]) return fp[n - 1];
  let j = 0;
  while (j + 1 < n && xp[j + 1] <= x) j++;
  const slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j]);
  let y = slope * (x - xp[j]) + fp[j];
  if (Number.isNaN(y)) {
    y = slope * (x - xp[j + 1]) + fp[j + 1];
    if (Number.isNaN(y) && fp[j] === fp[j + 1]) y = fp[j];
  }
  return y;
}

/**
 * Provisional (in-browser) `compile_path`: keyframes per control point (no sketch -> t = 0, 1),
 * `nFrames` dense `T_cw` samples, status/reasons/diagnostics/suggested params exactly as Python.
 */
export function compilePathProvisional(
  cfg: PathConfig,
  scene: SceneInfo,
  outW: number,
  outH: number,
  opts: { nFrames?: number; presets?: CameraPreset[]; suggest?: boolean } = {},
): CompiledPath {
  const nFrames = opts.nFrames ?? 120;
  const presets = new Map((opts.presets ?? PRESETS_V1).map((p) => [p.id as string, p]));
  const name = String(cfg.preset ?? "");
  const easing: unknown = cfg.easing === undefined ? "ease_in_out" : cfg.easing;
  const fit = coverFit(scene.src_w, scene.src_h, outW, outH);
  const Kout = outputIntrinsics(scene.K_src, fit);
  const params: Record<string, number> = {};
  const out: CompiledPath = {
    schema_version: SCHEMA_VERSION, solver_version: SOLVER_VERSION, coordinate_convention: "opencv_T_cw",
    K: Kout, source_to_output: { ...fit, src_w: scene.src_w, src_h: scene.src_h },
    depth_mapping: scene.depth_mapping ?? null, preset: name, params, easing: String(easing), fps: FPS,
    control_points: [], keyframes: [], samples: [],
    diagnostics: { framing_rms_px: null, framing_rms_rel: null, target_in_front_min_z: null, target_in_frame_fraction: null, hole_ratio: null, framing_clamped: false, details: [] },
    status: "ok", reasons: [], suggested_params: null,
  };
  const dg = out.diagnostics;
  const fail = (code: string, detail: Record<string, unknown> = {}) => {
    if (!out.reasons.includes(code)) out.reasons.push(code);
    dg.details.push({ code, ...detail });
  };
  const blocking = () => out.reasons.some((r) => !NON_BLOCKING.has(r));
  let sketchCtrl: ControlPoint[] = []; // control points actually compiled (for the sketch_scale suggestion)
  const finish = (): CompiledPath => {
    if (blocking()) out.status = "invalid";
    else if (out.reasons.length) out.status = "warning";
    if ((opts.suggest ?? true) && out.reasons.some((r) => MOTION_REASONS.has(r))) {
      out.suggested_params = suggestParams(cfg, params, sketchCtrl, scene, outW, outH, nFrames, opts.presets);
    }
    return out;
  };

  if (!presets.has(name)) fail("unknown_preset", { preset: name });
  if (easing !== "linear" && easing !== "ease_in_out") fail("unknown_easing");
  if (!Number.isInteger(nFrames) || nFrames < 2) fail("invalid_input", { field: "n_frames" });
  let raw: ControlPoint[] = [];
  let parsed: { controlPoints: ControlPoint[]; warning: "unsupported_sketch" | null } = { controlPoints: [], warning: null };
  try {
    const sp: unknown = cfg.sketch_points;
    const list = sp || [];
    if (!Array.isArray(list)) throw new Error("not a list");
    for (const p of list as unknown[]) {
      let tri: unknown[];
      if (Array.isArray(p)) tri = p;
      else if (p !== null && typeof p === "object") tri = [(p as Record<string, unknown>).t, (p as Record<string, unknown>).u, (p as Record<string, unknown>).v];
      else throw new Error("bad point");
      if (tri.length !== 3 || !tri.every(isFiniteNumber)) throw new Error("bad point");
      raw.push(tri as ControlPoint);
    }
    if (raw.length) parsed = sketchControlPoints(raw);
  } catch {
    fail("invalid_input", { field: "sketch_points" });
    raw = [];
  }
  if (out.reasons.length) return finish();
  const preset = presets.get(name)!;
  const vals = Object.fromEntries(PARAMS.map((p) => [p, cfg[p] === undefined ? preset.default[p] : cfg[p]])) as Record<ParamName, unknown>;
  const bad = PARAMS.filter((p) => !isFiniteNumber(vals[p]));
  for (const p of bad) fail("invalid_input", { field: p });
  if (bad.length) return finish();
  for (const p of PARAMS) params[p] = vals[p] as number;
  for (const [p, [lo, hi]] of Object.entries(preset.bounds)) {
    if (!(lo - 1e-12 <= params[p] && params[p] <= hi + 1e-12)) fail("param_out_of_bounds", { param: p, value: params[p], bounds: [lo, hi] });
  }
  const T = scene.target_world;
  if (!T.every(Number.isFinite) || T[2] <= NEAR) fail("target_behind_camera", { key: 0 });
  if (out.reasons.length) return finish();

  // Target framing in OUTPUT uv01: source pixel -> cover-fit -> output pixel.
  const [tu, tv] = scene.target_uv01;
  const tOut: Vec2 = [(tu * scene.src_w * fit.scale + fit.offset_x) / outW, (tv * scene.src_h * fit.scale + fit.offset_y) / outH];
  let ctrl: ControlPoint[];
  if (raw.length) {
    ctrl = parsed.controlPoints;
    sketchCtrl = ctrl;
    if (parsed.warning) fail(parsed.warning);
  } else {
    ctrl = [[0, 0.5, 0.5], [1, 0.5, 0.5]];
  }
  out.control_points = raw.length ? ctrl.map((c) => [...c]) : [];
  const ts = ctrl.map((c) => c[0]);
  const requested = ctrl.map((c): Vec2 => [tOut[0] + (c[1] - ctrl[0][1]), tOut[1] + (c[2] - ctrl[0][2])]);
  const framing = solveFraming(requested);
  const unclamped = solveFraming(requested, 0.01, 0.1, -Infinity, Infinity);
  if (unclamped.slice(1).some((r) => r.some((x) => x < FRAMING_LO || x > FRAMING_HI))) {
    dg.framing_clamped = true;
    fail("framing_clamped");
  }

  const ez = easing as string;
  const eKeys = ts.map((t) => ease(t, ez));
  const keys: [number, Mat4][] = [];
  ts.forEach((t, k) => {
    const c = presetCenter(name, params, eKeys[k], T);
    let R: Mat3;
    try {
      R = framingRotation(c, T, fromUv01(framing[k], outW, outH), Kout);
    } catch {
      fail("framing_unsolvable", { key: k });
      return;
    }
    if (R[1][1] <= 0) fail("camera_upside_down", { key: k });
    if (R[2][2] <= 0) fail("camera_turned_around", { key: k });
    const Tk = pose(R, c);
    keys.push([eKeys[k], Tk]);
    out.keyframes.push({ t, T_cw: Tk, desired_uv: [framing[k][0], framing[k][1]], requested_uv: [requested[k][0], requested[k][1]] });
  });
  if (blocking()) {
    out.keyframes = [];
    return finish();
  }

  const eS = Array.from({ length: nFrames }, (_, i) => ease(i / (nFrames - 1), ez));
  const Rs = interpolate(keys, eS, "linear");
  const Ts = Rs.map((Ti, i) => pose(rotationOf(Ti), presetCenter(name, params, eS[i], T)));
  const want = eS.map((e) => [interp1(e, eKeys, framing.map((f) => f[0])), interp1(e, eKeys, framing.map((f) => f[1]))]);
  const proj = Ts.map((Ti) => project(Kout, Ti, T));
  if (!Ts.every((Ti) => Ti.every((row) => row.every(Number.isFinite)))) {
    fail("non_finite");
    out.keyframes = [];
    out.control_points = [];
    return finish();
  }
  const front = proj.map((p) => p.z > NEAR);
  const inside = proj.map((p, i) => front[i] && p.uv[0] >= 0 && p.uv[0] <= outW && p.uv[1] >= 0 && p.uv[1] <= outH);
  dg.target_in_front_min_z = Math.min(...proj.map((p) => p.z));
  dg.target_in_frame_fraction = inside.filter(Boolean).length / nFrames;
  const frontIdx = front.flatMap((f, i) => (f ? [i] : []));
  if (frontIdx.length) {
    let sum = 0;
    for (const i of frontIdx) {
      const w = fromUv01(want[i], outW, outH);
      const du = proj[i].uv[0] - w[0], dv = proj[i].uv[1] - w[1];
      sum += du * du + dv * dv;
    }
    const rms = Math.sqrt(sum / frontIdx.length);
    dg.framing_rms_px = rms;
    dg.framing_rms_rel = rms / Math.min(outW, outH);
  }
  if (!front.every(Boolean)) fail("target_behind_camera", { first_frame: front.indexOf(false) });
  else if (!inside.every(Boolean)) fail("target_out_of_frame", { first_frame: inside.indexOf(false) });
  out.samples = Ts.map((Ti, i) => ({ t_ms: (i * 1000) / FPS, T_cw: Ti }));
  return finish();
}

/**
 * Smallest reduction whose compile has no motion reason (path.py `_suggest_params`): for k = 1..4
 * (f = 0.5**k) try (1) params * f (zero params stay 0) with the sketch unchanged -> sketch_scale 1,
 * then (2, only with a moving sketch) params * f AND the sketch displacement * f -> sketch_scale f.
 */
function suggestParams(
  cfg: PathConfig, params: Record<string, number>, sketch: ControlPoint[], scene: SceneInfo, outW: number, outH: number, nFrames: number,
  presets?: CameraPreset[],
): Record<string, number> | null {
  if (!Object.keys(params).length) return null;
  const moving = sketch.some((c) => c[1] !== sketch[0][1] || c[2] !== sketch[0][2]);
  for (let k = 1; k < 5; k++) {
    const f = 0.5 ** k;
    const trial = Object.fromEntries(PARAMS.map((p) => [p, params[p] !== 0 ? params[p] * f : 0])) as Record<ParamName, number>;
    for (const ss of moving ? [1, f] : [1]) {
      const c: PathConfig = { ...cfg, ...trial };
      if (ss !== 1) c.sketch_points = scaleSketch(sketch, ss).map(([t, u, v]) => ({ t, u, v }));
      const res = compilePathProvisional(c, scene, outW, outH, { nFrames, presets, suggest: false });
      if (!res.reasons.some((r) => MOTION_REASONS.has(r))) return { ...trial, sketch_scale: ss };
    }
  }
  return null;
}
