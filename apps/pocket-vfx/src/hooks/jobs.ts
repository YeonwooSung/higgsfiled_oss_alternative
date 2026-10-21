"use client";

/**
 * Jobs of the editor and the gates of their buttons.
 * - `startJob`: saves pending edits first (the job pins the revision on screen), creates the job with an
 *   Idempotency-Key and follows it over SSE with the polling fallback of `watchJob`. Nothing here runs on its own.
 * - Gates (`trackGate`, `plateGate`, `renderGate`): whether a button may run and, when not, every reason as a code
 *   (`Reason.message` carries the server's text for codes the UI has no words for).
 * - Plate choice: selecting a generated plate or going back to the bundled one is an explicit, undoable edit; a
 *   finished `vfx.generate_plate` never changes the config (a matte `check` included).
 */
import {
  TERMINAL_JOB_STATES, type Job, type JobKind, type PocketVfxConfig, type VfxPlateResult, type VfxPreset, type VfxPresetId,
  type VfxRenderProfile, type VfxValidateResponse,
} from "@oc/contracts";
import { watchJob, type SourceLike } from "@oc/web-core";
import { api, ApiError } from "../api/client";
import { useEditor, type SaveState, type TrackStatus } from "../state/editorStore";

// ---- running jobs

export function followJob(job: Job): Promise<Job> {
  useEditor.getState().trackJob(job);
  if (TERMINAL_JOB_STATES.includes(job.state)) return Promise.resolve(job);
  return new Promise((resolve) => {
    watchJob(job.id, {
      eventSource: (url) => new EventSource(url, { withCredentials: true }) as unknown as SourceLike,
      fetchJob: (id) => api.getJob(id),
      onUpdate: (j) => useEditor.getState().trackJob(j),
      onDone: (j) => {
        useEditor.getState().trackJob(j);
        void useEditor.getState().refresh().catch(() => {});
        resolve(j);
      },
    });
  });
}

/** Saves pending edits, then creates the job on that revision (Idempotency-Key per click). */
export async function createJob(kind: JobKind, options: Record<string, unknown> = {}): Promise<Job> {
  const revision = await useEditor.getState().flush();
  const s = useEditor.getState();
  if (s.saveState === "error" || s.saveState === "conflict") throw new ApiError(0, "save_failed", "Not saved");
  const job = await api.createJob(s.project!.id, kind, revision, options, crypto.randomUUID());
  s.trackJob(job);
  return job;
}

/** `createJob` + follow until the job ends; resolves with the final job. */
export async function startJob(kind: JobKind, options: Record<string, unknown> = {}): Promise<Job> {
  return followJob(await createJob(kind, options));
}

export const isActive = (j: Job) => !TERMINAL_JOB_STATES.includes(j.state);
const byNewest = (a: Job, b: Job) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0);

export function activeJob(jobs: Record<string, Job>, kind: JobKind): Job | undefined {
  return Object.values(jobs).filter((j) => j.kind === kind && isActive(j)).sort(byNewest)[0];
}

export function latestSucceeded(jobs: Record<string, Job>, kind: JobKind): Job | undefined {
  return Object.values(jobs).filter((j) => j.kind === kind && j.state === "succeeded").sort(byNewest)[0];
}

// ---- gates

export interface Reason {
  code: string;
  /** server text (validate / API errors) */
  message?: string;
}

export interface Gate {
  ok: boolean;
  reasons: Reason[];
}

/** Validation of the current revision: null while it is being checked. */
export type ValidationState = { result: VfxValidateResponse | null; error: boolean } | null;

const gate = (reasons: Reason[]): Gate => ({ ok: reasons.length === 0, reasons });

function blocking(validation: ValidationState, keep: (code: string) => boolean): Reason[] {
  if (!validation) return [{ code: "checking" }];
  if (validation.error || !validation.result) return [{ code: "check_failed" }];
  return validation.result.blocking_errors.filter((e) => keep(e.code)).map((e) => ({ code: e.code, message: e.message }));
}

/** `validate` codes that block vfx.track (the API's `_TRACK_PREREQUISITES`). */
export const TRACK_PREREQUISITES = new Set(["video_required", "asset_not_ready", "trim_range", "analysis_required", "analysis_stale",
  "anchor_range", "frame_range"]);
/** Codes vfx.generate_plate ignores: it makes the plate, and it does not depend on the subject. */
const PLATE_IGNORES = new Set(["mask_required", "plate_required", "plate_mismatch", "plate_length"]);

/** Track: blocked while one runs or by its prerequisites in the last known check (it does not wait for a newer one). */
export function trackGate({ validation, jobs }: { validation: ValidationState; jobs: Record<string, Job> }): Gate {
  if (activeJob(jobs, "vfx.track")) return gate([{ code: "track_running" }]);
  return gate((validation?.result?.blocking_errors ?? []).filter((e) => TRACK_PREREQUISITES.has(e.code))
    .map((e) => ({ code: e.code, message: e.message })));
}

export function plateGate({ validation, jobs, saveState }: { validation: ValidationState; jobs: Record<string, Job>; saveState: SaveState }): Gate {
  if (activeJob(jobs, "vfx.generate_plate")) return gate([{ code: "plate_running" }]);
  if (saveState === "error") return gate([{ code: "save_failed" }]);
  return gate(blocking(validation, (code) => !PLATE_IGNORES.has(code)));
}

/** The render needs subject masks (API `masks_needed`): the effect hides behind the subject, or Blink Out. */
export function masksNeeded(config: PocketVfxConfig, preset: Pick<VfxPreset, "input_requirements"> | null | undefined): boolean {
  const cleanPlate = preset ? preset.input_requirements.clean_plate : config.effect.preset_id === "blink_out_v1";
  return config.effect.layer_order !== "in_front" || cleanPlate;
}

/** `generated` only when a generated plate is selected; Quick otherwise. */
export function renderProfileOf(config: PocketVfxConfig): VfxRenderProfile {
  return config.plate.mode === "generated" && config.plate.asset_id ? "generated" : "quick";
}

export interface RenderGate extends Gate {
  /** newest succeeded vfx.track when it matches the current config (the stage shows the same track), else null */
  trackJobId: string | null;
  renderProfile: VfxRenderProfile;
}

export function renderGate(i: {
  config: PocketVfxConfig;
  saveState: SaveState;
  validation: ValidationState;
  trackStatus: TrackStatus;
  jobs: Record<string, Job>;
  preset: Pick<VfxPreset, "input_requirements"> | null | undefined;
}): RenderGate {
  const reasons: Reason[] = [];
  if (activeJob(i.jobs, "vfx.render")) reasons.push({ code: "render_running" });
  if (activeJob(i.jobs, "vfx.track")) reasons.push({ code: "track_running" });
  if (i.saveState === "error") reasons.push({ code: "save_failed" });
  const needsTrack = i.config.anchor.follow || masksNeeded(i.config, i.preset);
  if (needsTrack && i.trackStatus === "none") reasons.push({ code: "track_required" });
  if (needsTrack && i.trackStatus === "stale") reasons.push({ code: "track_stale" });
  reasons.push(...blocking(i.validation, () => true));
  const trackJobId = i.trackStatus === "fresh" ? latestSucceeded(i.jobs, "vfx.track")?.id ?? null : null;
  return { ...gate(reasons), trackJobId, renderProfile: renderProfileOf(i.config) };
}

/** Reason of a refused job request (422 codes such as track_stale, validation_failed, plate_length). */
export function errorReason(e: unknown): Reason {
  return e instanceof ApiError ? { code: e.code, message: e.message } : { code: "generic" };
}

// ---- tracking actions

/**
 * "Re-track from here": the correction keyframe on the current frame at the shown position (the store's anchor /
 * correction rule), then a new vfx.track. Returns null when the correction could not be added (32 keyframes).
 */
export function retrackFrom(shown: [number, number]): Promise<Job> | null {
  const s = useEditor.getState();
  s.placeAnchor(shown[0], shown[1]);
  if (useEditor.getState().note?.code === "corrections_full") return null;
  return startJob("vfx.track");
}

/** Reset: removes every correction keyframe (one undo step); the anchor stays. */
export function resetCorrections(): void {
  useEditor.getState().update((c) => { c.corrections = []; });
}

// ---- plates

export interface GeneratedPlate {
  job: Job;
  result: VfxPlateResult;
}

/** Plates of succeeded vfx.generate_plate jobs for `presetId`, newest first. */
export function generatedPlates(jobs: Record<string, Job>, presetId: VfxPresetId): GeneratedPlate[] {
  return Object.values(jobs)
    .filter((j) => j.kind === "vfx.generate_plate" && j.state === "succeeded" && (j.result as VfxPlateResult | null)?.preset_id === presetId)
    .sort(byNewest)
    .map((job) => ({ job, result: job.result as unknown as VfxPlateResult }));
}

/** The plate the panel describes: the selected generated plate, else the newest one. */
export function focusedPlate(plates: GeneratedPlate[], config: PocketVfxConfig): GeneratedPlate | undefined {
  if (config.plate.mode === "generated" && config.plate.asset_id) return plates.find((p) => p.result.plate_asset_id === config.plate.asset_id);
  return plates[0];
}

/** Explicit choice of a generated plate (undoable): renders use the Generated plate profile. */
export function selectGeneratedPlate(assetId: string): void {
  useEditor.getState().update((c) => {
    c.plate.mode = "generated";
    c.plate.asset_id = assetId;
    c.render_profile = "generated";
  });
}

/** Explicit choice of a bundled plate (undoable): Quick renders. `bundledId` null = the preset's default plate. */
export function chooseBundledPlate(bundledId?: string | null): void {
  useEditor.getState().update((c) => {
    c.plate.mode = "bundled";
    c.plate.asset_id = null;
    if (bundledId !== undefined) c.plate.bundled_id = bundledId;
    c.render_profile = "quick";
  });
}
