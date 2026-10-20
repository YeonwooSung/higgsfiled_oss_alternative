/**
 * Pure editor helpers around the path compiler: pointer stroke capture, control point edits,
 * output<->source uv mapping, preset switching, the mobile strength slider and reason text keys.
 */
import type { CameraPreset, CameraPresetId, CameraSketchConfig, CompiledPath, PathReason, SketchPoint } from "@oc/contracts";
import { coverFit, project, type Vec2 } from "./camera";
import { MAX_RAW_POINTS, PARAMS, simplifySketch, type ControlPoint, type ParamName } from "./path";

type PathCfg = CameraSketchConfig["path"];
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));

/**
 * Pointer stroke (OUTPUT-frame uv01, drawing order) -> raw `(t, u, v)` sketch of at most 128 points.
 * Positions are clamped into the frame, non-finite ones dropped; more than 128 points are uniformly
 * downsampled with the compiler's index rule (`floor(k (n-1) / 127)`). `t` is the drawing order in
 * [0, 1] (the compiler re-derives time from arc length; only the order matters).
 */
export function captureSketch(stroke: readonly (readonly number[])[]): ControlPoint[] {
  let pts = stroke.filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1])).map((p): Vec2 => [clamp01(p[0]), clamp01(p[1])]);
  const n = pts.length;
  if (n > MAX_RAW_POINTS) pts = Array.from({ length: MAX_RAW_POINTS }, (_, k) => pts[Math.floor((k * (n - 1)) / (MAX_RAW_POINTS - 1))]);
  const m = pts.length;
  return pts.map(([u, v], i) => [m > 1 ? i / (m - 1) : 0, u, v]);
}

/** Stroke -> 2..5 config control points (via `simplifySketch`) and the compiler warning, if any. */
export function sketchFromStroke(stroke: readonly (readonly number[])[]): { points: SketchPoint[]; warning: "unsupported_sketch" | null } {
  const raw = captureSketch(stroke);
  if (!raw.length) return { points: [], warning: null };
  const { controlPoints, warning } = simplifySketch(raw);
  return { points: controlPoints.map(([t, u, v]) => ({ t, u, v })), warning };
}

/** Minimum horizontal sketch travel (uv01) before a lateral sign is proposed. */
export const LATERAL_PROPOSAL_MIN_DU = 0.01;
/** Proposed magnitude when the current lateral is 0. */
export const LATERAL_PROPOSAL_DEFAULT = 0.03;

/**
 * Slide only: the target moving right on screen means the camera moves left (negative lateral).
 * Returns the proposed `lateral_ratio` when the current sign disagrees with the sketch, else null.
 * Never applied automatically; the UI asks the user.
 */
export function lateralProposal(preset: string, lateral: number, points: readonly SketchPoint[]): number | null {
  if (preset !== "slide" || points.length < 2) return null;
  const du = points[points.length - 1].u - points[0].u;
  if (Math.abs(du) < LATERAL_PROPOSAL_MIN_DU) return null;
  const want = du > 0 ? -1 : 1;
  if (Math.sign(lateral) === want) return null;
  return want * (lateral !== 0 ? Math.abs(lateral) : LATERAL_PROPOSAL_DEFAULT);
}

/** `t` = cumulative arc length fraction, exactly how the compiler times control points. */
export function retime(points: readonly SketchPoint[]): SketchPoint[] {
  const cum = [0];
  for (let i = 1; i < points.length; i++) cum.push(cum[i - 1] + Math.hypot(points[i].u - points[i - 1].u, points[i].v - points[i - 1].v));
  const total = cum[cum.length - 1];
  return points.map((p, i) => ({ t: total > 0 ? cum[i] / total : i === 0 ? 0 : 1, u: p.u, v: p.v }));
}

/** Move control point `i` (clamped into the frame) and retime the sketch. */
export function moveControlPoint(points: readonly SketchPoint[], i: number, u: number, v: number): SketchPoint[] {
  const next = points.map((p, k) => (k === i ? { t: p.t, u: clamp01(u), v: clamp01(v) } : { ...p }));
  return retime(next);
}

/** OUTPUT-frame uv01 -> SOURCE image uv01 (inverse cover-fit; valid for the source pose, t = 0). */
export function outputToSourceUv(uv: readonly number[], srcW: number, srcH: number, outW: number, outH: number): Vec2 {
  const f = coverFit(srcW, srcH, outW, outH);
  return [clamp01((uv[0] * outW - f.offset_x) / f.scale / srcW), clamp01((uv[1] * outH - f.offset_y) / f.scale / srcH)];
}

/** SOURCE image uv01 -> OUTPUT-frame uv01 (cover-fit, may fall outside [0,1] for cropped regions). */
export function sourceToOutputUv(uv: readonly number[], srcW: number, srcH: number, outW: number, outH: number): Vec2 {
  const f = coverFit(srcW, srcH, outW, outH);
  return [(uv[0] * srcW * f.scale + f.offset_x) / outW, (uv[1] * srcH * f.scale + f.offset_y) / outH];
}

/** The param the mobile strength slider drives for each preset. */
export const STRENGTH_PARAM: Record<CameraPresetId, ParamName> = { push_in: "push_ratio", slide: "lateral_ratio", small_arc: "arc_deg" };

const presetOf = (presets: readonly CameraPreset[], id: string) => presets.find((p) => p.id === id);
const maxAbs = ([lo, hi]: [number, number]) => Math.max(Math.abs(lo), Math.abs(hi));

/**
 * New `path` params for a preset switch: params the preset does not allow become 0, others are
 * clamped into its bounds, and its main motion gets the preset default when it would be 0.
 */
export function switchPreset(path: PathCfg, id: CameraPresetId, presets: readonly CameraPreset[]): Pick<PathCfg, "preset" | ParamName> {
  const p = presetOf(presets, id);
  if (!p) return { preset: id, lateral_ratio: path.lateral_ratio, push_ratio: path.push_ratio, arc_deg: path.arc_deg };
  const out = { preset: id } as Pick<PathCfg, "preset" | ParamName>;
  for (const k of PARAMS) {
    const [lo, hi] = p.bounds[k];
    out[k] = Math.min(hi, Math.max(lo, path[k]));
  }
  const main = STRENGTH_PARAM[id];
  if (out[main] === 0) out[main] = p.default[main];
  return out;
}

/** Mobile strength in [0,1] = |main param| / max |bound|. */
export function strengthOf(path: PathCfg, presets: readonly CameraPreset[]): number {
  const p = presetOf(presets, path.preset);
  if (!p) return 0;
  const k = STRENGTH_PARAM[path.preset];
  const m = maxAbs(p.bounds[k]);
  return m > 0 ? Math.min(1, Math.abs(path[k]) / m) : 0;
}

/** Param patch for a mobile strength value, keeping the main param's sign (+ when it is 0). */
export function withStrength(path: PathCfg, strength: number, presets: readonly CameraPreset[]): Partial<Record<ParamName, number>> {
  const p = presetOf(presets, path.preset);
  if (!p) return {};
  const k = STRENGTH_PARAM[path.preset];
  const [lo, hi] = p.bounds[k];
  const sign = path[k] < 0 && lo < 0 ? -1 : hi > 0 ? 1 : -1;
  const v = sign * clamp01(strength) * maxAbs(p.bounds[k]);
  return { [k]: Math.min(hi, Math.max(lo, v)) };
}

/** Every `CompiledPath.reasons` code (a test checks it against `PathReason` and the dictionaries). */
export const PATH_REASONS = [
  "unsupported_sketch", "framing_clamped", "param_out_of_bounds", "framing_unsolvable", "camera_upside_down", "camera_turned_around",
  "target_behind_camera", "target_out_of_frame", "invalid_input", "non_finite", "unknown_easing", "unknown_preset",
] as const satisfies readonly PathReason[];

/** Editor-side block reasons (not compiler codes). */
export const EDITOR_REASONS = ["compile_pending", "depth_missing", "holes_high", "compile_failed"] as const;

const KNOWN = new Set<string>([...PATH_REASONS, ...EDITOR_REASONS]);
export type ReasonKey = `reason_${(typeof PATH_REASONS)[number] | (typeof EDITOR_REASONS)[number]}` | "reason_unknown";

/** i18n key for a reason code; unknown codes get a generic text (raw codes are never shown). */
export function reasonKey(code: string): ReasonKey {
  return (KNOWN.has(code) ? `reason_${code}` : "reason_unknown") as ReasonKey;
}

/** Hole guardrail (product policy): max frame hole ratio above this -> warning + reduced-motion proposal. */
export const HOLE_WARN_RATIO = 0.08;
/** Time of the last of the 120 output frames (frame 119 at 24 fps). */
export const LAST_FRAME_MS = (119 * 1000) / 24;

/**
 * Shot time of a compiled keyframe: keyframes sit at eased progress `ease(t)` and frame `i` at
 * `ease(i / 119)`, so keyframe `t` is reached at frame `t * 119`.
 */
export const keyframeTimeMs = (t: number) => Math.min(1, Math.max(0, t)) * LAST_FRAME_MS;

/**
 * Reduced-motion proposal for the "Apply reduced motion" button: the compiler's `suggested_params`
 * when it has them, else (hole ratio above the guardrail) the current motion halved. Never applied
 * automatically.
 */
export function motionSuggestion(path: CompiledPath | null, holeRatio: number | null): { params: Record<ParamName, number>; source: "compiler" | "holes" } | null {
  if (!path) return null;
  const sp = path.suggested_params;
  if (sp && PARAMS.every((k) => typeof sp[k] === "number" && Number.isFinite(sp[k]))) {
    return { params: Object.fromEntries(PARAMS.map((k) => [k, sp[k]])) as Record<ParamName, number>, source: "compiler" };
  }
  if (holeRatio !== null && holeRatio > HOLE_WARN_RATIO && PARAMS.some((k) => (path.params[k] ?? 0) !== 0)) {
    return { params: Object.fromEntries(PARAMS.map((k) => [k, (path.params[k] ?? 0) * 0.5])) as Record<ParamName, number>, source: "holes" };
  }
  return null;
}

/** Target position in the OUTPUT frame (uv01) for a pose: where the amber reticle is drawn. */
export function targetOutputUv(K: number[][], Tcw: number[][], targetWorld: readonly number[], outW: number, outH: number): Vec2 | null {
  const { uv, z } = project(K, Tcw, targetWorld);
  return z > 0 ? [uv[0] / outW, uv[1] / outH] : null;
}

/** Sketch to start numeric editing from: both points at the target's output position (a static path). */
export function sketchAtTarget(targetOut: readonly number[]): SketchPoint[] {
  const u = clamp01(targetOut[0]), v = clamp01(targetOut[1]);
  return [{ t: 0, u, v }, { t: 1, u, v }];
}

/** Insert a control point halfway between the last two (max 5), keeping arc-length times. */
export function addControlPoint(points: readonly SketchPoint[]): SketchPoint[] {
  if (points.length >= 5 || points.length < 2) return points.map((p) => ({ ...p }));
  const a = points[points.length - 2], b = points[points.length - 1];
  return retime([...points.slice(0, -1), { t: 0, u: (a.u + b.u) / 2, v: (a.v + b.v) / 2 }, b]);
}

/** Remove control point `i`; fewer than 2 points means no sketch (the preset path). */
export function removeControlPoint(points: readonly SketchPoint[], i: number): SketchPoint[] {
  const next = points.filter((_, k) => k !== i);
  return next.length < 2 ? [] : retime(next);
}
