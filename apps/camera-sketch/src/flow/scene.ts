import { TERMINAL_JOB_STATES, type Asset, type CameraAnalyzeResult, type CameraSketchConfig, type Job } from "@oc/contracts";

/** The three bundled example scenes (public/samples). They go through the normal upload path. */
export const SAMPLES = [
  { id: "room", url: "/samples/room.png", titleKey: "sampleIndoor", ratio: "16 / 9" },
  { id: "product", url: "/samples/product.png", titleKey: "sampleProduct", ratio: "16 / 9" },
  { id: "facade", url: "/samples/facade.png", titleKey: "sampleArchitecture", ratio: "9 / 16" },
] as const;
export type SampleId = (typeof SAMPLES)[number]["id"];

/**
 * Newest ready `scene` asset to auto-link into the config (upload -> config link), or null.
 * Only a scene the editor has not handled yet is linked (`handled`: scenes already linked once, or present when
 * the project loaded with a scene set) and only when it is newer than the configured scene. A config that
 * points elsewhere (an undo, a deliberate scene choice) is never re-linked.
 */
export function sceneToLink(assets: Asset[], config: Pick<CameraSketchConfig, "scene_asset_id">,
  handled: readonly string[] = []): string | null {
  const ready = assets.filter((a) => a.role === "scene" && a.state === "ready");
  if (!ready.length) return null;
  const newest = ready.reduce((a, b) => (b.created_at > a.created_at ? b : a));
  if (newest.id === config.scene_asset_id || handled.includes(newest.id)) return null;
  const current = assets.find((a) => a.id === config.scene_asset_id);
  return current && current.created_at >= newest.created_at ? null : newest.id;
}

/** Scene ids the auto-link treats as already handled when a project (re)loads. */
export function handledScenesOnLoad(assets: Asset[], config: Pick<CameraSketchConfig, "scene_asset_id">): string[] {
  return config.scene_asset_id ? assets.filter((a) => a.role === "scene").map((a) => a.id) : [];
}

/**
 * Depth asset to PATCH into `config.depth_asset_id` after a successful `camera.analyze`.
 * The executor never writes the config; the server rejects depth whose lineage is not the config's
 * scene, so a stale result (scene replaced meanwhile) is filtered out here as well.
 */
export function depthToLink(assets: Asset[], config: Pick<CameraSketchConfig, "scene_asset_id" | "depth_asset_id">, analyze: Job | undefined): string | null {
  if (!analyze || analyze.state !== "succeeded") return null;
  const id = (analyze.result as Partial<CameraAnalyzeResult> | null)?.depth_asset_id;
  if (!id || id === config.depth_asset_id || !config.scene_asset_id) return null;
  const depth = assets.find((a) => a.id === id);
  return depth && depth.role === "depth" && depth.source_asset_id === config.scene_asset_id ? id : null;
}

export type AnalysisState = "no_scene" | "ready" | "running" | "done" | "failed";

export function analysisState(config: Pick<CameraSketchConfig, "scene_asset_id" | "depth_asset_id">, latest: Job | undefined): AnalysisState {
  if (!config.scene_asset_id) return "no_scene";
  if (latest && !TERMINAL_JOB_STATES.includes(latest.state)) return "running";
  if (config.depth_asset_id) return "done";
  if (latest && (latest.state === "failed" || latest.state === "submission_unknown")) return "failed";
  return "ready";
}

export function latestJob(jobs: Record<string, Job>, kind: Job["kind"]): Job | undefined {
  return Object.values(jobs).filter((j) => j.kind === kind).sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0];
}

/** i18n key for a job's state; falls back to the generic "working" label when no `stage_*` key exists. */
export function jobLabelKey(state: Job["state"], has: (key: string) => boolean): string {
  if (state === "cancel_requested") return "cancelRequested";
  if (state === "queued") return "jobQueued";
  const terminal = `state_${state}`;
  if (has(terminal)) return terminal;
  const stage = `stage_${state}`;
  return has(stage) ? stage : "jobRunning";
}
