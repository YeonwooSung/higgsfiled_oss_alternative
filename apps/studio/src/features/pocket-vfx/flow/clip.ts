/**
 * Clip -> config links of the upload + trim screen. The worker never writes the config: the client links the inspected
 * clip (`video_asset_id`, default trim and aspect) and, after `vfx.analyze`, the work clip (`work_asset_id`). The API
 * accepts a work clip only when its `meta` was made from the config's clip, trim and aspect (`analysis_matches`);
 * `matchingWork` applies the same rule so the client never sends a link the server would reject.
 */
import type { Aspect, Asset, AssetRole, Job, JobKind, PocketVfxConfig, VfxAnalyzeResult, VfxClipSource, VfxTrim } from "@oc/contracts";
import { fitTiming, frameCount } from "../model/timeline";
import { defaultAspect, defaultTrim, normalizeTrim } from "../model/trim";

const newest = <T extends { created_at: string }>(xs: T[]): T | undefined =>
  xs.reduce<T | undefined>((a, b) => (!a || b.created_at > a.created_at ? b : a), undefined);

export function latestJob(jobs: Job[], kind: JobKind): Job | undefined {
  return newest(jobs.filter((j) => j.kind === kind));
}

/** Newest asset of `role` made from `sourceId` (e.g. the clip's `clip_preview`). */
export function derivedAsset(assets: Asset[], role: AssetRole, sourceId: string | null | undefined): Asset | undefined {
  return sourceId ? newest(assets.filter((a) => a.role === role && a.source_asset_id === sourceId)) : undefined;
}

export type ClipPhase = "none" | "uploading" | "inspecting" | "rejected" | "ready";

/** State of the newest uploaded clip (`asset.inspect` turns `uploaded` into `ready` or `rejected`). */
export function clipPhase(assets: Asset[]): ClipPhase {
  const clip = newest(assets.filter((a) => a.role === "clip"));
  if (!clip) return "none";
  return ({ pending_upload: "uploading", uploaded: "inspecting", rejected: "rejected", ready: "ready" } as const)[clip.state];
}

/**
 * The newest clip is ready but its `clip_preview` / `clip_filmstrip` are not in the project yet (an older worker
 * stored them after flipping the clip to ready): keep polling a little longer.
 */
export function derivativesPending(assets: Asset[]): boolean {
  const clip = newest(assets.filter((a) => a.role === "clip"));
  return !!clip && clip.state === "ready"
    && (!derivedAsset(assets, "clip_preview", clip.id) || !derivedAsset(assets, "clip_filmstrip", clip.id));
}

/** Newest ready clip to link into the config, or null (already linked, or older than the linked one). */
export function clipToLink(assets: Asset[], config: Pick<PocketVfxConfig, "video_asset_id">): string | null {
  const clip = newest(assets.filter((a) => a.role === "clip" && a.state === "ready"));
  if (!clip || clip.id === config.video_asset_id) return null;
  const current = assets.find((a) => a.id === config.video_asset_id);
  return current && current.created_at >= clip.created_at ? null : clip.id;
}

/** `meta.source` of an inspected clip (probe result: display size, duration, audio). */
export function clipSource(clip: Asset | undefined): VfxClipSource | null {
  return ((clip?.meta as { source?: VfxClipSource } | undefined)?.source) ?? null;
}

/** Source duration in ms of an inspected clip. */
export function clipDurationMs(clip: Asset | undefined): number | null {
  return clip?.duration_ms ?? clipSource(clip)?.duration_ms ?? null;
}

/**
 * Fit effect timing and the anchor into the trimmed length, and drop corrections, subject prompts and mask edits on
 * frames the shorter clip no longer has (the server would report `frame_range`).
 */
function withTrim(config: PocketVfxConfig, trim: VfxTrim): PocketVfxConfig {
  const { timing, anchorFrame } = fitTiming(config.effect, config.anchor.frame, trim.duration_ms);
  const n = frameCount(trim.duration_ms);
  const inside = <E extends { frame: number }>(xs: E[]) => xs.filter((x) => x.frame < n);
  return {
    ...config, trim, effect: { ...config.effect, ...timing }, anchor: { ...config.anchor, frame: anchorFrame },
    corrections: inside(config.corrections), subject_prompts: inside(config.subject_prompts), mask_edits: inside(config.mask_edits),
  };
}

/** Config after linking a newly inspected clip: default trim, aspect of the displayed orientation, no work clip. */
export function linkClip(config: PocketVfxConfig, clip: Asset): PocketVfxConfig {
  const src = clipSource(clip);
  const duration = clipDurationMs(clip) ?? 0;
  const trim = defaultTrim(duration) ?? config.trim;
  const w = src?.display_w ?? clip.width ?? 16;
  const h = src?.display_h ?? clip.height ?? 9;
  return withTrim({ ...config, video_asset_id: clip.id, work_asset_id: null, output: { ...config.output, aspect: defaultAspect(w, h) } }, trim);
}

const sameTrim = (a: unknown, b: VfxTrim) => {
  const t = a as Partial<VfxTrim> | null | undefined;
  return !!t && Object.keys(t).length === 2 && t.source_start_ms === b.source_start_ms && t.duration_ms === b.duration_ms;
};

/** Newest ready work clip analyzed from the config's clip, trim and aspect (the server's `analysis_matches`). */
export function matchingWork(assets: Asset[], config: Pick<PocketVfxConfig, "video_asset_id" | "trim" | "output">): Asset | undefined {
  if (!config.video_asset_id) return undefined;
  return newest(assets.filter((a) => {
    const m = a.meta as { source_asset_id?: string; trim?: unknown; aspect?: string };
    return a.role === "work_clip" && a.state === "ready" && String(m.source_asset_id) === config.video_asset_id
      && sameTrim(m.trim, config.trim) && m.aspect === config.output.aspect;
  }));
}

/**
 * Config after a trim / aspect choice. Changing either invalidates the prepared work clip (it is unlinked); settings
 * that were analyzed before link that work clip again without a new job.
 */
export function applyClipSettings(config: PocketVfxConfig, settings: { trim: VfxTrim; aspect: Aspect }, assets: Asset[]): PocketVfxConfig {
  const clip = assets.find((a) => a.id === config.video_asset_id);
  const duration = clipDurationMs(clip);
  const trim = duration && duration >= 1000 ? normalizeTrim(settings.trim, duration) : settings.trim;
  const next = withTrim({ ...config, output: { ...config.output, aspect: settings.aspect } }, trim);
  return { ...next, work_asset_id: matchingWork(assets, next)?.id ?? null };
}

/** Work clip of a succeeded `vfx.analyze` to PATCH into the config, or null (failed, already linked, or stale). */
export function workToLink(assets: Asset[], config: PocketVfxConfig, analyze: Job | undefined): string | null {
  if (!analyze || analyze.state !== "succeeded") return null;
  const id = (analyze.result as Partial<VfxAnalyzeResult> | null)?.work_asset_id;
  if (!id || id === config.work_asset_id) return null;
  return matchingWork(assets.filter((a) => a.id === id), config) ? id : null;
}
