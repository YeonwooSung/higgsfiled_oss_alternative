/**
 * Camera convention core — TS port of `open_creative/camera_sketch/camera.py` (the reference).
 *
 * Conventions (identical to Python):
 * - OpenCV camera axes: x right, y down, z forward.
 * - Poses are world-to-camera `T_cw` (4x4 row-major): `X_c = R X_w + t`. World frame = source
 *   camera at t=0. `T_wc` only ever as an explicit inverse.
 * - Pixels: pixel i covers [i, i+1); the image spans [0,w]x[0,h]; principal point (w/2, h/2).
 *   `uv01` = pixels / (w, h).
 * - Quaternions are `[w, x, y, z]`, unit length, canonical `w >= 0` from `rotToQuat`.
 * - Plain float64 numbers, deterministic. The OpenCV -> Three.js conversion is NOT here; it lives
 *   only in `three.ts`.
 */

export type Vec2 = [number, number];
export type Vec3 = [number, number, number];
export type Quat = [number, number, number, number];
export type Mat3 = number[][];
export type Mat4 = number[][];
export type Easing = "linear" | "ease_in_out";

/** Pinhole K with `fx = fy = focalMm / sensorWMm * width`, `c = (w/2, h/2)`. */
export function intrinsics(focalMm: number, width: number, height: number, sensorWMm = 36.0): Mat3 {
  const f = (focalMm / sensorWMm) * width;
  return [
    [f, 0, width / 2],
    [0, f, height / 2],
    [0, 0, 1],
  ];
}

/** Source pixel `p` maps to output pixel `p * scale + (offset_x, offset_y)`. */
export interface CoverFit {
  scale: number;
  offset_x: number;
  offset_y: number;
  out_w: number;
  out_h: number;
}

/** Scale the source uniformly to cover the output, then center-crop (offsets <= 0). */
export function coverFit(srcW: number, srcH: number, outW: number, outH: number): CoverFit {
  if (Math.min(srcW, srcH, outW, outH) <= 0) throw new Error("sizes must be positive");
  const scale = Math.max(outW / srcW, outH / srcH);
  return { scale, offset_x: (outW - srcW * scale) / 2, offset_y: (outH - srcH * scale) / 2, out_w: outW, out_h: outH };
}

/** K of the output frame: `A K_src` with `A = [[s,0,ox],[0,s,oy],[0,0,1]]`. */
export function outputIntrinsics(Ksrc: Mat3, fit: CoverFit): Mat3 {
  const A = [
    [fit.scale, 0, fit.offset_x],
    [0, fit.scale, fit.offset_y],
    [0, 0, 1],
  ];
  return matMul(A, Ksrc);
}

export function matMul(A: number[][], B: number[][]): number[][] {
  const n = A.length, m = B[0].length, k = B.length;
  const out: number[][] = [];
  for (let i = 0; i < n; i++) {
    const row: number[] = [];
    for (let j = 0; j < m; j++) {
      let s = 0;
      for (let l = 0; l < k; l++) s += A[i][l] * B[l][j];
      row.push(s);
    }
    out.push(row);
  }
  return out;
}

export function transpose3(M: Mat3): Mat3 {
  return [0, 1, 2].map((i) => [M[0][i], M[1][i], M[2][i]]);
}

function mulVec3(M: Mat3, v: readonly number[]): Vec3 {
  return [
    M[0][0] * v[0] + M[0][1] * v[1] + M[0][2] * v[2],
    M[1][0] * v[0] + M[1][1] * v[1] + M[1][2] * v[2],
    M[2][0] * v[0] + M[2][1] * v[1] + M[2][2] * v[2],
  ];
}

const norm3 = (v: readonly number[]) => Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);

/**
 * Project one world point to pixels. Returns `{uv, z}`; `z` is camera-space depth and
 * `uv = [NaN, NaN]` when `z <= 0`.
 */
export function project(K: Mat3, Tcw: Mat4, X: readonly number[]): { uv: Vec2; z: number } {
  const Xc: Vec3 = [0, 1, 2].map((r) => Tcw[r][0] * X[0] + Tcw[r][1] * X[1] + Tcw[r][2] * X[2] + Tcw[r][3]) as Vec3;
  const z = Xc[2];
  if (!(z > 0)) return { uv: [NaN, NaN], z };
  const h = mulVec3(K, Xc);
  return { uv: [h[0] / h[2], h[1] / h[2]], z };
}

/** Pixels -> normalized [0,1] image coordinates. */
export const toUv01 = (px: readonly number[], w: number, h: number): Vec2 => [px[0] / w, px[1] / h];
/** Normalized [0,1] image coordinates -> pixels. */
export const fromUv01 = (uv: readonly number[], w: number, h: number): Vec2 => [uv[0] * w, uv[1] * h];

export function rotX(a: number): Mat3 {
  const c = Math.cos(a), s = Math.sin(a);
  return [
    [1, 0, 0],
    [0, c, -s],
    [0, s, c],
  ];
}

export function rotY(a: number): Mat3 {
  const c = Math.cos(a), s = Math.sin(a);
  return [
    [c, 0, s],
    [0, 1, 0],
    [-s, 0, c],
  ];
}

/** Python floor-mod (`a % m` takes the divisor's sign); JS `%` keeps the dividend's sign. */
export const floorMod = (a: number, m: number) => ((a % m) + m) % m;

/** Wrap an angle to [-pi, pi) exactly like Python's `(a + pi) % (2 pi) - pi`. */
export const wrapAngle = (a: number) => floorMod(a + Math.PI, 2 * Math.PI) - Math.PI;

/** Solve `K r = [u, v, 1]` for a zero-skew upper-triangular pinhole K (back substitution). */
function backProject(K: Mat3, u: number, v: number): Vec3 {
  const z = 1;
  const y = (v - K[1][2] * z) / K[1][1];
  const x = (u - K[0][1] * y - K[0][2] * z) / K[0][0];
  return [x, y, z];
}

/**
 * World-to-camera R (roll 0) whose ray through `desiredPx` points exactly at the target.
 * Camera-to-world `R_wc = Ry(yaw) Rx(pitch)`; returns `R_wc^T`. Pitch is solved first
 * (`rho cos(pitch + phi) = d_y`, root with the smaller |pitch|, `+` on ties), then yaw.
 * Throws when the target coincides with the camera or no roll-free solution exists.
 */
export function framingRotation(cWorld: readonly number[], targetWorld: readonly number[], desiredPx: readonly number[], K: Mat3): Mat3 {
  let d: Vec3 = [targetWorld[0] - cWorld[0], targetWorld[1] - cWorld[1], targetWorld[2] - cWorld[2]];
  const n = norm3(d);
  if (!Number.isFinite(n) || n < 1e-12) throw new Error("target coincides with the camera center");
  d = [d[0] / n, d[1] / n, d[2] / n];
  let r = backProject(K, desiredPx[0], desiredPx[1]);
  const rn = norm3(r);
  r = [r[0] / rn, r[1] / rn, r[2] / rn];
  const rho = Math.hypot(r[1], r[2]);
  const ratio = d[1] / rho;
  if (Math.abs(ratio) > 1 + 1e-12) throw new Error("no roll-free rotation frames the target at desired_px");
  const phi = Math.atan2(r[2], r[1]);
  const a = Math.acos(Math.min(Math.max(ratio, -1), 1));
  const c0 = wrapAngle(a - phi), c1 = wrapAngle(-a - phi);
  const pitch = Math.abs(c0) <= Math.abs(c1) ? c0 : c1;
  const p = mulVec3(rotX(pitch), r);
  const yaw = Math.atan2(d[0], d[2]) - Math.atan2(p[0], p[2]);
  return transpose3(matMul(rotY(yaw), rotX(pitch)));
}

/** `T_cw` from world-to-camera rotation R and camera center c: `t = -R c`. */
export function pose(R: Mat3, c: readonly number[]): Mat4 {
  const t = mulVec3(R, c);
  return [
    [R[0][0], R[0][1], R[0][2], -t[0]],
    [R[1][0], R[1][1], R[1][2], -t[1]],
    [R[2][0], R[2][1], R[2][2], -t[2]],
    [0, 0, 0, 1],
  ];
}

export const rotationOf = (T: Mat4): Mat3 => [0, 1, 2].map((i) => [T[i][0], T[i][1], T[i][2]]);

/** Camera center in world coordinates: `c = -R^T t`. */
export function cameraCenter(Tcw: Mat4): Vec3 {
  const t = [Tcw[0][3], Tcw[1][3], Tcw[2][3]];
  const c = mulVec3(transpose3(rotationOf(Tcw)), t);
  return [-c[0], -c[1], -c[2]];
}

/** Easing on `s` clamped to [0,1]: linear, or cubic smoothstep `3s^2 - 2s^3`. */
export function ease(s: number, kind: Easing | string): number {
  s = Math.min(Math.max(s, 0), 1);
  if (kind === "linear") return s;
  if (kind === "ease_in_out") return s * s * (3 - 2 * s);
  throw new Error(`unknown easing ${kind}`);
}

/** Rotation matrix -> unit quaternion `[w, x, y, z]` with `w >= 0` (Shepperd). */
export function rotToQuat(m: Mat3): Quat {
  const tr = m[0][0] + m[1][1] + m[2][2];
  let q: Quat;
  if (tr > 0) {
    const s = 2 * Math.sqrt(tr + 1);
    q = [0.25 * s, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s];
  } else if (m[0][0] >= m[1][1] && m[0][0] >= m[2][2]) {
    const s = 2 * Math.sqrt(1 + m[0][0] - m[1][1] - m[2][2]);
    q = [(m[2][1] - m[1][2]) / s, 0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s];
  } else if (m[1][1] >= m[2][2]) {
    const s = 2 * Math.sqrt(1 + m[1][1] - m[0][0] - m[2][2]);
    q = [(m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s];
  } else {
    const s = 2 * Math.sqrt(1 + m[2][2] - m[0][0] - m[1][1]);
    q = [(m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s];
  }
  if (q[0] < 0) q = q.map((x) => -x) as Quat;
  const n = Math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]);
  return q.map((x) => x / n) as Quat;
}

/** Unit quaternion `[w, x, y, z]` -> 3x3 rotation (input renormalized). */
export function quatToRot(q: readonly number[]): Mat3 {
  const n = Math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]);
  const [w, x, y, z] = q.map((v) => v / n);
  return [
    [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
    [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
    [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
  ];
}

/** Shortest-path slerp of `[w,x,y,z]` quaternions; nlerp when `dot > 0.9995`. */
export function slerp(qa: readonly number[], qb: readonly number[], s: number): Quat {
  let dot = qa[0] * qb[0] + qa[1] * qb[1] + qa[2] * qb[2] + qa[3] * qb[3];
  let b = [...qb];
  if (dot < 0) {
    b = b.map((x) => -x);
    dot = -dot;
  }
  let q: number[];
  if (dot > 0.9995) {
    q = qa.map((x, i) => x + s * (b[i] - x));
  } else {
    const theta = Math.acos(Math.min(dot, 1));
    const wa = Math.sin((1 - s) * theta), wb = Math.sin(s * theta), st = Math.sin(theta);
    q = qa.map((x, i) => (wa * x + wb * b[i]) / st);
  }
  const n = Math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]);
  return q.map((x) => x / n) as Quat;
}

const copy4 = (T: Mat4): Mat4 => T.map((r) => [...r]);

/**
 * Sample `T_cw` poses at `times` from keyframes `[t_i, T_cw_i]` (strictly increasing t).
 * Times are clamped to [t_0, t_last]; segment i = (number of keys with t_i <= t) - 1, capped to
 * [0, n-2] (numpy `searchsorted(side="right") - 1`); `e = ease(local)`; center lerp, orientation
 * slerp; `e == 0` / `e == 1` return the key pose exactly.
 */
export function interpolate(keys: [number, Mat4][], times: readonly number[], easing: Easing | string): Mat4[] {
  if (!keys.length) throw new Error("at least one key is required");
  const ts = keys.map(([t]) => t);
  for (let i = 1; i < ts.length; i++) if (!(ts[i] - ts[i - 1] > 0)) throw new Error("key times must be strictly increasing");
  const Ts = keys.map(([, T]) => T);
  const centers = Ts.map(cameraCenter);
  const quats = Ts.map((T) => rotToQuat(rotationOf(T)));
  const out: Mat4[] = [];
  for (const t0 of times) {
    const t = Math.min(Math.max(t0, ts[0]), ts[ts.length - 1]);
    if (ts.length === 1) {
      out.push(copy4(Ts[0]));
      continue;
    }
    let cnt = 0;
    while (cnt < ts.length && ts[cnt] <= t) cnt++;
    const i = Math.min(Math.max(cnt - 1, 0), ts.length - 2);
    const e = ease((t - ts[i]) / (ts[i + 1] - ts[i]), easing);
    if (e === 0) out.push(copy4(Ts[i]));
    else if (e === 1) out.push(copy4(Ts[i + 1]));
    else {
      const ca = centers[i], cb = centers[i + 1];
      const c = [0, 1, 2].map((k) => ca[k] + e * (cb[k] - ca[k]));
      out.push(pose(quatToRot(slerp(quats[i], quats[i + 1], e)), c));
    }
  }
  return out;
}
