/**
 * Pure helpers for the preview stage and the Top/Side gizmo (no DOM, no three).
 * Poses are OpenCV world-to-camera `T_cw`; world = source camera at t = 0.
 */
import type { CompiledPath } from "@oc/contracts";
import { cameraCenter, interpolate, type Mat4, type Vec2, type Vec3 } from "./camera";

export const IDENTITY_POSE: Mat4 = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];

/**
 * Camera pose at `tMs` from the dense compiled samples (`samples[k].t_ms` increasing): clamped to
 * the first/last sample, exact sample poses at their own times, otherwise a linear center lerp +
 * orientation slerp between the two neighbouring samples (`camera.interpolate`). No samples (an
 * invalid path) -> the source pose.
 */
export function poseAtTime(samples: CompiledPath["samples"], tMs: number): Mat4 {
  const n = samples.length;
  if (!n) return IDENTITY_POSE;
  if (!(tMs > samples[0].t_ms)) return samples[0].T_cw;
  if (tMs >= samples[n - 1].t_ms) return samples[n - 1].T_cw;
  let lo = 0, hi = n - 1; // invariant: t[lo] < tMs < t[hi]
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (samples[mid].t_ms === tMs) return samples[mid].T_cw;
    if (samples[mid].t_ms < tMs) lo = mid;
    else hi = mid;
  }
  const a = samples[lo], b = samples[hi];
  return interpolate([[a.t_ms, a.T_cw], [b.t_ms, b.T_cw]], [tMs], "linear")[0];
}

export type GizmoView = "top" | "side";

/**
 * World point -> 2D gizmo plane (screen x right, screen y down).
 * Top (x–z): x right, forward z up. Side (z–y): forward z right, OpenCV y (down) down.
 */
export function gizmoPlane(p: readonly number[], view: GizmoView): Vec2 {
  return view === "top" ? [p[0], -p[2]] : [p[2], p[1]];
}

export interface GizmoFit {
  /** Plane coords -> SVG pixels. */
  map: (q: Vec2) => Vec2;
  scale: number;
}

/**
 * Uniform-scale fit of all plane points into `w x h` with `pad` px margin (same scale on both
 * axes), centred on the points' bounding box. `scale` overrides the fitted scale (to share one
 * scale between the top and side views).
 */
export function fitGizmo(points: Vec2[], w: number, h: number, pad = 18, minSpan = 0.2, scaleOverride?: number): GizmoFit {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const [x, y] of points) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  if (x0 > x1) { x0 = -minSpan / 2; x1 = minSpan / 2; y0 = -minSpan / 2; y1 = minSpan / 2; }
  const sx = Math.max(x1 - x0, minSpan), sy = Math.max(y1 - y0, minSpan);
  const scale = scaleOverride ?? Math.min((w - 2 * pad) / sx, (h - 2 * pad) / sy);
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  return { scale, map: ([x, y]) => [w / 2 + (x - cx) * scale, h / 2 + (y - cy) * scale] };
}

/** Camera glyph in a gizmo plane: center and the 3 points of a small view wedge (apex = center). */
export function cameraGlyph(Tcw: Mat4, view: GizmoView, size: number, halfFovTan = 0.4): { c: Vec2; wedge: [Vec2, Vec2, Vec2] } {
  const c = cameraCenter(Tcw);
  const fwd: Vec3 = [Tcw[2][0], Tcw[2][1], Tcw[2][2]]; // camera z axis in world
  const side: Vec3 = view === "top" ? [Tcw[0][0], Tcw[0][1], Tcw[0][2]] : [Tcw[1][0], Tcw[1][1], Tcw[1][2]];
  const at = (s: number): Vec3 => [0, 1, 2].map((k) => c[k] + size * (fwd[k] + s * halfFovTan * side[k])) as Vec3;
  return { c: gizmoPlane(c, view), wedge: [gizmoPlane(c, view), gizmoPlane(at(-1), view), gizmoPlane(at(1), view)] };
}

/** Output size for an aspect (constraints: 1280x720 or 720x1280). */
export const outputSize = (aspect: "16:9" | "9:16"): [number, number] => (aspect === "9:16" ? [720, 1280] : [1280, 720]);

/** Stage view zoom (CSS only — never the camera / focal length): `scale` 1..4 and a pixel translate. */
export interface StageZoom {
  scale: number;
  tx: number;
  ty: number;
}
export const STAGE_ZOOM_MIN = 1;
export const STAGE_ZOOM_MAX = 4;
export const NO_ZOOM: StageZoom = { scale: 1, tx: 0, ty: 0 };

/**
 * Zoom by `factor` about the frame point `(cx, cy)` (CSS px in the `w x h` frame): the content point
 * under the cursor stays put, the scale is clamped to 1..4 and the translate keeps the frame covered.
 * Transform = `translate(tx, ty) scale(scale)` with origin 0 0.
 */
export function zoomAt(z: StageZoom, cx: number, cy: number, factor: number, w: number, h: number): StageZoom {
  const scale = Math.min(STAGE_ZOOM_MAX, Math.max(STAGE_ZOOM_MIN, z.scale * (Number.isFinite(factor) && factor > 0 ? factor : 1)));
  const k = scale / z.scale;
  const clampT = (t: number, size: number) => Math.min(0, Math.max(size * (1 - scale), t));
  return { scale, tx: clampT(cx - (cx - z.tx) * k, w), ty: clampT(cy - (cy - z.ty) * k, h) };
}
