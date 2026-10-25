/**
 * Pure logic of the result screen (spec §3 화면 C): which renders exist, the "Previous version" rule, the render
 * badge (Quick vs Generated plate, Mock), the export formats and the wording inputs of the QA / warning lines.
 */
import type { Job, PocketVfxConfig, VfxExportFormat, VfxLayerOrder, VfxRenderResult } from "@oc/contracts";
import type { Dict } from "../i18n/en";

// ---- renders

const byNewest = (a: Job, b: Job) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0);

/** Succeeded vfx.render jobs, newest first. */
export function renderResults(jobs: Record<string, Job>): Job[] {
  return Object.values(jobs).filter((j) => j.kind === "vfx.render" && j.state === "succeeded" && !!j.result).sort(byNewest);
}

export const resultOf = (job: Job | undefined): VfxRenderResult | null => (job?.result as VfxRenderResult | null | undefined) ?? null;

// ---- previous version

function canonical(v: unknown): string {
  if (v === null || v === undefined) return "null";
  if (Array.isArray(v)) return `[${v.map(canonical).join(",")}]`;
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    return `{${Object.keys(o).sort().map((k) => `${JSON.stringify(k)}:${canonical(o[k])}`).join(",")}}`;
  }
  return JSON.stringify(v);
}

/**
 * Everything a render reads from the config, as a canonical string. The config holds no UI-only state (frame,
 * tool, view live in the editor store), so this is the whole config minus `schema_version` and the plate field the
 * render ignores in the selected mode (bundled: `plate.asset_id`; generated: `plate.bundled_id`).
 */
export function renderInputs(c: PocketVfxConfig): string {
  const { schema_version: _v, ...rest } = c;
  const generated = c.plate.mode === "generated";
  return canonical({ ...rest, plate: { ...c.plate, asset_id: generated ? c.plate.asset_id : null, bundled_id: generated ? null : c.plate.bundled_id } });
}

/**
 * "Previous version" (shared §9): the current settings came after the render's revision and differ from it in
 * something the render reads. `null` while the render's revision config is still loading (and the revisions differ).
 * Downloads stay available either way.
 */
export function isPreviousVersion(i: {
  renderRevision: number;
  renderConfig: PocketVfxConfig | undefined;
  revision: number;
  config: PocketVfxConfig;
}): boolean | null {
  if (i.revision <= i.renderRevision) return false;
  if (!i.renderConfig) return null;
  return renderInputs(i.renderConfig) !== renderInputs(i.config);
}

// ---- badges

export type ProfileBadgeKind = "quick" | "generated";

/** "Generated plate" only for a generated render whose plate really was generated; anything else is Quick. */
export function profileBadgeOf(result: Pick<VfxRenderResult, "render_profile" | "plate_source"> | null | undefined): ProfileBadgeKind {
  return result?.render_profile === "generated" && result.plate_source === "generated" ? "generated" : "quick";
}

/** Mock when the result says so, or the job ran on a mock profile (its masks are stand-ins; Ruling R14). */
export function isMockResult(result: Pick<VfxRenderResult, "mock"> | null | undefined, modelProfile: string,
  isMockProfile: (id: string) => boolean): boolean {
  return !!result?.mock || isMockProfile(modelProfile);
}

// ---- exports

/** Mirror of `PocketVfxApp.export_formats["vfx.render"]` (services/api/oc_api/apps/pocket_vfx.py). */
export const EXPORT_FORMATS: readonly VfxExportFormat[] = ["mp4", "cover_png", "recipe_zip", "layers_zip"];

// ---- QA lines

export type RoiQaView = { kind: "pass"; maxErr: number } | { kind: "fail"; maxErr: number | null } | { kind: "unknown" };

/** Outside-ROI integrity: only a well-formed `pass` with a measured error is a pass. */
export function roiQaView(qa: Partial<VfxRenderResult["roi_qa"]> | null | undefined): RoiQaView {
  if (qa?.status === "pass" && typeof qa.max_err === "number") return { kind: "pass", maxErr: qa.max_err };
  if (qa?.status === "fail") return { kind: "fail", maxErr: typeof qa.max_err === "number" ? qa.max_err : null };
  return { kind: "unknown" };
}

export type OcclusionView =
  | { kind: "in_front" | "clear" | "unknown" }
  | { kind: "touches" | "split_glow"; pct: number };

/**
 * Subject-core occlusion check: `occlusion_qa` is the largest fraction of subject-core pixels the effect changed on
 * any frame. In front = no occlusion; Split glow crosses the subject by design.
 */
export function occlusionView(result: Pick<VfxRenderResult, "occlusion" | "occlusion_qa">, layerOrder: VfxLayerOrder | undefined): OcclusionView {
  if (!result.occlusion || layerOrder === "in_front") return { kind: "in_front" };
  const q = result.occlusion_qa;
  if (typeof q !== "number" || !Number.isFinite(q)) return { kind: "unknown" };
  const pct = Math.round(q * 1000) / 10;
  if (layerOrder === "split_glow") return { kind: "split_glow", pct };
  return q > 0 ? { kind: "touches", pct } : { kind: "clear" };
}

export interface WarningItem {
  key: keyof Dict & `res_${string}`;
  vars: Record<string, string | number>;
}

const runs = (frames: [number, number][]) => frames.map(([a, b]) => (a === b ? `${a + 1}` : `${a + 1}–${b + 1}`)).join(", ");

/** Tracking and mask warnings of a render, as i18n keys with 1-based frame numbers. */
export function trackingWarningItems(result: Pick<VfxRenderResult, "tracking_warnings" | "mask_warnings">): WarningItem[] {
  const out: WarningItem[] = [];
  for (const w of result.tracking_warnings ?? []) {
    if (w.code === "track_lost") out.push({ key: w.policy === "freeze" ? "res_track_lost_freeze" : "res_track_lost_hide", vars: { frames: runs(w.frames) } });
    else if (w.code === "track_invalid_at_peak") out.push({ key: "res_track_invalid_at_peak", vars: { frame: w.frame + 1 } });
    else if (w.code === "camera_not_fixed") out.push({ key: "res_camera_not_fixed", vars: {} });
    else if (w.code === "camera_motion_unknown") out.push({ key: "res_camera_motion_unknown", vars: {} });
  }
  for (const [code, frames] of maskWarningRuns(result.mask_warnings ?? [])) out.push({ key: `res_mask_${code}`, vars: { frames: runs(frames) } });
  return out;
}

/** Mask diagnostics (one per frame) grouped per code, in order of first appearance, into runs of consecutive frames. */
function maskWarningRuns<C extends string>(diags: readonly { code: C; frame: number }[]): Map<C, [number, number][]> {
  const byCode = new Map<C, number[]>();
  for (const d of diags) byCode.set(d.code, [...(byCode.get(d.code) ?? []), d.frame]);
  const out = new Map<C, [number, number][]>();
  for (const [code, frames] of byCode) {
    const spans: [number, number][] = [];
    for (const f of [...new Set(frames)].sort((a, b) => a - b)) {
      const last = spans[spans.length - 1];
      if (last && f === last[1] + 1) last[1] = f;
      else spans.push([f, f]);
    }
    out.set(code, spans);
  }
  return out;
}

export type AudioSummary = "none" | "original" | "sfx" | "original_sfx";

export function audioSummary(result: Pick<VfxRenderResult, "has_audio" | "audio">): AudioSummary {
  if (!result.has_audio || !result.audio) return "none";
  const { original_kept: o, sfx: s } = result.audio;
  return o && s ? "original_sfx" : o ? "original" : s ? "sfx" : "none";
}
