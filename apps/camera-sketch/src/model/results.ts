/**
 * Pure result-screen logic (Task 16): guide staleness via the Python `geometry_hash`, QA status
 * mapping (unknown is never a pass), previous-version detection, export formats per kind,
 * progress display rules and the path overlay's target trajectory.
 */
import type { CameraExportFormat, CameraSketchConfig, CompiledPath, Job, JobKind, MotionReason, MotionReport } from "@oc/contracts";
import { sha256Hex } from "@oc/web-core";
import { project } from "./camera";
import { HOLE_WARN_RATIO } from "./sketch";

// ---- geometry hash (mirror of services/api/oc_api/camera_sketch/jobs.py::geometry_hash) ----

/** Python `repr(float)` (what `json.dumps` writes for a float): shortest round-trip digits, `1e-05`, `50.0`. */
export function pyFloatRepr(x: number): string {
  if (!Number.isFinite(x)) throw new Error("non-finite float");
  if (x === 0) return Object.is(x, -0) ? "-0.0" : "0.0";
  const [mant, expS] = x.toExponential().split("e");
  const exp = Number(expS);
  const neg = mant.startsWith("-");
  const digits = mant.replace("-", "").replace(".", "");
  let s: string;
  if (exp < -4 || exp >= 16) {
    const m = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
    s = `${m}e${exp < 0 ? "-" : "+"}${String(Math.abs(exp)).padStart(2, "0")}`;
  } else if (exp >= 0) {
    const int = digits.slice(0, exp + 1).padEnd(exp + 1, "0");
    s = `${int}.${digits.slice(exp + 1) || "0"}`;
  } else {
    s = `0.${"0".repeat(-exp - 1)}${digits}`;
  }
  return neg ? `-${s}` : s;
}

/** Config fields that are `int` in the pydantic model; every other number there is a `float`. */
const INT_PATHS = new Set(["camera.sensor_width_mm_assumed", "camera.roll_deg", "output.duration_ms", "output.fps"]);

function canon(v: unknown, path: string): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return INT_PATHS.has(path) ? String(Math.trunc(v)) : pyFloatRepr(v);
  if (typeof v === "string") return JSON.stringify(v).replace(/[\u007f-￿]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
  if (Array.isArray(v)) return `[${v.map((x) => canon(x, path)).join(",")}]`;
  const o = v as Record<string, unknown>;
  return `{${Object.keys(o).sort().map((k) => `${JSON.stringify(k)}:${canon(o[k], path ? `${path}.${k}` : k)}`).join(",")}}`;
}

/** The canonical JSON the API hashes: geometry fields only, sorted keys, compact, Python number formatting. */
export function canonicalGeometryJson(cfg: CameraSketchConfig): string {
  const geom = {
    scene_asset_id: cfg.scene_asset_id ?? null,
    depth_asset_id: cfg.depth_asset_id ?? null,
    target: cfg.target,
    camera: cfg.camera,
    path: { ...cfg.path, sketch_points: cfg.path.sketch_points.map((p) => ({ t: p.t, u: p.u, v: p.v })) },
    output: cfg.output,
    scene: { depth_contrast: cfg.scene.depth_contrast, edge_protection: cfg.scene.edge_protection },
  };
  return canon(geom, "");
}

/** sha256 of the canonical geometry JSON; equals the guide job's `result.geometry_hash` for the same geometry. */
export async function geometryHash(cfg: CameraSketchConfig): Promise<string> {
  return sha256Hex(new TextEncoder().encode(canonicalGeometryJson(cfg)).buffer as ArrayBuffer);
}

// ---- guide selection for Cinematic ----

export const OUTPUT_FRAMES = 120;

export type GuideReason = "guide_missing" | "guide_running" | "guide_stale" | "frame_count_mismatch" | "hash_pending";

const byNewest = (a: Job, b: Job) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0);
const ACTIVE = new Set<Job["state"]>(["queued", "preparing", "submitting", "running", "postprocessing", "cancel_requested"]);

/**
 * The guide a Cinematic job would use: the newest succeeded `camera.guide` whose `geometry_hash`
 * equals the current config's. Otherwise why not (no guide / one is running / out of date / bad length).
 */
export function cinematicGuide(jobs: Record<string, Job>, currentHash: string | null): { guide: Job | null; reason: GuideReason | null } {
  const guides = Object.values(jobs).filter((j) => j.kind === "camera.guide").sort(byNewest);
  const done = guides.filter((j) => j.state === "succeeded");
  if (!done.length) return { guide: null, reason: guides.some((j) => ACTIVE.has(j.state)) ? "guide_running" : "guide_missing" };
  if (currentHash === null) return { guide: null, reason: "hash_pending" };
  const match = done.filter((j) => (j.result as { geometry_hash?: string } | null)?.geometry_hash === currentHash);
  if (!match.length) return { guide: null, reason: guides.some((j) => ACTIVE.has(j.state)) ? "guide_running" : "guide_stale" };
  const g = match[0];
  if ((g.result as { frame_count?: number } | null)?.frame_count !== OUTPUT_FRAMES) return { guide: null, reason: "frame_count_mismatch" };
  return { guide: g, reason: null };
}

/** i18n key for an API error code of a render request, or null to show the server message. */
export function renderError(code: string): `err_${string}` | null {
  return ["guide_stale", "frame_count_mismatch", "guide_not_finished", "guide_not_found", "profile_unavailable", "path_invalid", "render_busy", "queue_full", "gpu_busy"]
    .includes(code) ? `err_${code}` : null;
}

// ---- QA status ----

const QA_REASONS: readonly MotionReason[] = [
  "no_frames", "length_mismatch", "unsupported_frame", "shape_mismatch", "low_texture_generated", "low_texture_geometry",
  "tracking_lost_geometry", "tracking_lost_generated", "target_drift", "subject_change",
];

export interface QaView {
  kind: "pass" | "check" | "unknown";
  titleKey: "qaPassed" | "qaCheck" | "qaUnknown";
  reasonKeys: `qa_${MotionReason | "unknown"}`[];
}

/** Cinematic QA header state. Only a well-formed `status: "ok"` is a pass; anything else unexpected is unknown. */
export function qaStatus(report: MotionReport | null | undefined): QaView {
  const r = report as Partial<MotionReport> | null | undefined;
  const reasons = Array.isArray(r?.reasons) ? r!.reasons : null;
  const keys = (reasons ?? []).map((x) => (QA_REASONS.includes(x) ? (`qa_${x}` as const) : ("qa_unknown" as const)));
  const uniq = [...new Set(keys)];
  if (r && reasons && r.status === "ok") return { kind: "pass", titleKey: "qaPassed", reasonKeys: uniq };
  if (r && reasons && r.status === "check") return { kind: "check", titleKey: "qaCheck", reasonKeys: uniq };
  return { kind: "unknown", titleKey: "qaUnknown", reasonKeys: uniq.length ? uniq : ["qa_unknown"] };
}

// ---- versions, exports, progress ----

/** "Previous version": the result was rendered from a revision other than the current one. */
export function isPreviousVersion(job: Pick<Job, "input_revision"> | undefined | null, revision: number): boolean {
  return !!job && job.input_revision !== revision;
}

/** Mirror of `CameraSketchApp.export_formats` (services/api/oc_api/apps/camera_sketch.py). */
export const EXPORT_FORMATS: Record<"camera.guide" | "camera.cinematic", readonly CameraExportFormat[]> = {
  "camera.guide": ["mp4", "path_json", "guides_zip", "recipe_zip"],
  "camera.cinematic": ["mp4", "path_json", "recipe_zip"],
};

export function exportFormats(kind: JobKind): CameraExportFormat[] {
  return kind === "camera.guide" || kind === "camera.cinematic" ? [...EXPORT_FORMATS[kind]] : [];
}

/** Percent to show, or null for a spinner: only an API-provided progress, never 100 before success. */
export function progressPercent(job: Pick<Job, "state" | "progress">): number | null {
  if (job.progress === null || job.progress === undefined || !Number.isFinite(job.progress) || !ACTIVE.has(job.state)) return null;
  return Math.max(0, Math.min(99, Math.round(job.progress * 100)));
}

const STAGES = ["compile", "render", "encode", "prepare", "generate", "postprocess", "qa", "depth", "path_json", "guides_zip", "recipe"] as const;
export type StageKey = `stageName_${(typeof STAGES)[number]}` | "jobRunning";

/** i18n key of a job's current stage name (`job.stage`), generic "Working" when unknown. */
export function stageKey(stage: string | null | undefined): StageKey {
  return stage && (STAGES as readonly string[]).includes(stage) ? (`stageName_${stage}` as StageKey) : "jobRunning";
}

// ---- path overlay ----

export interface TrajectoryPoint {
  tMs: number;
  /** Target position in OUTPUT uv01, null when it is behind the camera. */
  uv: [number, number] | null;
}

/**
 * Target screen trajectory of a stored guide path artifact (`{user_config, compiled}`), the same
 * construction as the worker's `target_px_out` applied to every sample. Preview overlay only.
 */
export function targetTrajectory(artifact: unknown): TrajectoryPoint[] | null {
  try {
    const a = artifact as { user_config: { target: { u: number; v: number; depth_scale: number } }; compiled: {
      K: number[][]; samples: { t_ms: number; T_cw: number[][] }[];
      source_to_output: { scale: number; offset_x: number; offset_y: number; out_w: number; out_h: number; src_w: number; src_h: number };
    } };
    const c = a?.compiled, tgt = a?.user_config?.target;
    if (!c?.K || !Array.isArray(c.samples) || !c.source_to_output || !tgt) return null;
    const f = c.source_to_output;
    const px = [tgt.u * f.src_w * f.scale + f.offset_x, tgt.v * f.src_h * f.scale + f.offset_y];
    const [[fx, , cx], [, fy, cy]] = c.K;
    const X = [((px[0] - cx) / fx) * tgt.depth_scale, ((px[1] - cy) / fy) * tgt.depth_scale, tgt.depth_scale];
    if (!X.every(Number.isFinite)) return null;
    return c.samples.map((s) => {
      const { uv, z } = project(c.K, s.T_cw, X);
      return { tMs: s.t_ms, uv: z > 0 && Number.isFinite(uv[0]) && Number.isFinite(uv[1]) ? [uv[0] / f.out_w, uv[1] / f.out_h] : null };
    });
  } catch {
    return null;
  }
}

// ---- Geometry warnings (confirm before rendering; spec §12) ----

/** Target-visibility guardrail (product policy): visible in less than 90 % of the shot -> warning. */
export const VISIBILITY_WARN = 0.9;

/**
 * Warning codes of the approved server path that require an explicit confirmation before Render
 * Geometry: compiler warning reasons, holes > 8 % and target visibility < 90 %. Empty when none.
 */
export function geometryWarnings(path: CompiledPath | null, holeRatio: number | null): string[] {
  if (!path || path.status === "invalid") return [];
  const out = path.status === "warning" ? [...path.reasons] : [];
  if (holeRatio !== null && holeRatio > HOLE_WARN_RATIO) out.push("holes_high");
  const vis = path.diagnostics?.target_in_frame_fraction;
  if (typeof vis === "number" && vis < VISIBILITY_WARN) out.push("visibility_low");
  return [...new Set(out)];
}

/** Guide result warnings (`hole_ratio_high`, `target_visibility_low`, compiler reasons) as reason codes. */
export function guideWarningCodes(warnings: unknown): string[] {
  if (!Array.isArray(warnings)) return [];
  const map: Record<string, string> = { hole_ratio_high: "holes_high", target_visibility_low: "visibility_low" };
  return [...new Set(warnings.filter((w): w is string => typeof w === "string").map((w) => map[w] ?? w))];
}

/**
 * Warnings of the guide a Cinematic job would use (spec §12: warn before Generate). The guide's own
 * `warnings` plus `holes_high` / `visibility_low` re-derived from its measured stats. Generate stays
 * allowed (the user decides); these are shown in the estimate dialog.
 */
export function cinematicWarnings(result: unknown): string[] {
  const r = (result ?? {}) as { warnings?: unknown; hole_ratio_max?: unknown; target_visibility_min?: unknown };
  const out = guideWarningCodes(r.warnings);
  if (typeof r.hole_ratio_max === "number" && r.hole_ratio_max > HOLE_WARN_RATIO) out.push("holes_high");
  if (typeof r.target_visibility_min === "number" && r.target_visibility_min < VISIBILITY_WARN) out.push("visibility_low");
  return [...new Set(out)];
}
