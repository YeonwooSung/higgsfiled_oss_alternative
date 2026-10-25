"use client";

import type { CameraSketchConfig, CameraSketchProject, CompileResponse, Job } from "@oc/contracts";
import { Autosaver, History } from "@oc/web-core";
import { create } from "zustand";
import { api } from "../api/client";
import { handledScenesOnLoad } from "../flow/scene";
import { PARAMS, type ParamName } from "../model/path";
import { lateralProposal, scaleSketchPoints, sketchFromStroke } from "../model/sketch";

export type SaveState = "saved" | "saving" | "error" | "conflict";
export type Tool = "none" | "target" | "sketch" | "keyframe";

/** Debounce between a saved revision and its `POST camera-path/compile` (after the 600 ms autosave). */
export const COMPILE_DEBOUNCE_MS = 300;
/** Delay of the single automatic retry after a failed compile request. */
export const COMPILE_RETRY_MS = 2000;
/** Compiler reasons that never block rendering (they are warnings). */
const NON_BLOCKING = new Set(["unsupported_sketch", "framing_clamped"]);

interface EditorState {
  project: CameraSketchProject | null;
  config: CameraSketchConfig | null;
  revision: number;
  saveState: SaveState;
  /** API error code of the last failed save (e.g. `depth_stale`), else null. */
  saveCode: string | null;
  clearSaveError: () => void;
  canUndo: boolean;
  canRedo: boolean;
  jobs: Record<string, Job>;
  load: (project: CameraSketchProject) => void;
  refresh: () => Promise<void>;
  /**
   * User edit: one undo step. Edits with the same `coalesce` key within 1 s (a slider drag, typing
   * in a number field) replace the last step instead of adding one.
   */
  update: (mutate: (draft: CameraSketchConfig) => void, coalesce?: string) => void;
  /** Automatic link (not a user edit): saved, but never an undo step and never undone. */
  link: (mutate: (draft: CameraSketchConfig) => void) => void;
  /**
   * Scene assets the upload -> config auto-link has handled (linked once, or already present when the project
   * loaded with a scene set). `sceneToLink` never links them again, so undo / a chosen scene is not overridden.
   */
  handledSceneIds: readonly string[];
  /** Auto-link a newly uploaded scene (clears the depth, records the scene as handled). */
  linkScene: (sceneId: string) => void;
  undo: () => void;
  redo: () => void;
  flush: () => Promise<number>;
  trackJob: (job: Job) => void;
  /** Latest server compile response and the revision it was computed for (the compile loop sets it). */
  serverCompile: { revision: number; response: CompileResponse } | null;
  setServerCompile: (c: { revision: number; response: CompileResponse } | null) => void;
  /** API error code of the last failed compile for the current revision (cleared by the next save). */
  compileError: string | null;
  /** Timeline playhead, 0..duration ms (the timeline UI drives it). */
  playheadMs: number;
  setPlayheadMs: (t: number) => void;
  playing: boolean;
  setPlaying: (on: boolean) => void;
  tool: Tool;
  setTool: (tool: Tool) => void;
  /** Proposed `lateral_ratio` after a slide sketch whose direction disagrees with its sign (never auto-applied). */
  lateralProposal: number | null;
  applyLateralProposal: () => void;
  dismissLateralProposal: () => void;
  /** Pointer stroke (OUTPUT uv01, drawing order) -> sketch control points (one undo step). */
  commitSketch: (stroke: readonly (readonly number[])[]) => void;
  /** Retry the compile of the current saved revision after a failure (the "Retry" action). */
  retryCompile: () => void;
  /** Clear the sketch, keep preset and params ("Restore preset"). */
  restorePreset: () => void;
  /**
   * "Apply reduced motion": write the compiler's `suggested_params` as a normal (undoable) edit;
   * `sketch_scale` < 1 scales the stored sketch displacement about its first point.
   */
  applySuggestedParams: (params: Record<string, number>) => void;
}

let history: History<CameraSketchConfig> | null = null;
let saver: Autosaver<CameraSketchConfig> | null = null;
let compileTimer: ReturnType<typeof setTimeout> | null = null;
let lastSaved: { projectId: string; revision: number; cfg: CameraSketchConfig } | null = null;

/** Stop a scheduled compile (or its retry); called on project switch and when the editor unmounts. */
export function stopCompileLoop() {
  if (compileTimer) clearTimeout(compileTimer);
  compileTimer = null;
}
let lastEdit: { key: string; at: number } | null = null;
const COALESCE_MS = 1000;

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

export const useEditor = create<EditorState>((set, get) => {
  const applyLocal = (cfg: CameraSketchConfig) => {
    set({ config: cfg, canUndo: history!.canUndo, canRedo: history!.canRedo, saveState: "saving", lateralProposal: null });
    saver!.schedule(cfg);
  };

  /**
   * Authoritative compile of a SAVED revision, 300 ms after it was saved. A response is applied only
   * while its revision is still the latest saved one (late responses for older revisions are dropped).
   */
  const scheduleCompile = (projectId: string, revision: number, cfg: CameraSketchConfig, delayMs = COMPILE_DEBOUNCE_MS, attempt = 0) => {
    stopCompileLoop();
    lastSaved = { projectId, revision, cfg };
    if (!cfg.scene_asset_id || !cfg.depth_asset_id) return;
    compileTimer = setTimeout(() => {
      compileTimer = null;
      const live = () => get().project?.id === projectId && get().revision === revision;
      if (!live()) return;
      const p = cfg.path;
      api.compilePath(projectId, {
        revision, preset: p.preset, sketch: p.sketch_points,
        parameters: { lateral_ratio: p.lateral_ratio, push_ratio: p.push_ratio, arc_deg: p.arc_deg, easing: p.easing },
      }).then(
        (response) => { if (live()) set({ serverCompile: { revision, response }, compileError: null }); },
        (e) => {
          if (!live()) return;
          // One automatic retry after 2 s; then the error shows with a Retry action.
          if (attempt === 0) scheduleCompile(projectId, revision, cfg, COMPILE_RETRY_MS, 1);
          else set({ compileError: (e as { code?: string }).code ?? "compile_failed" });
        },
      );
    }, delayMs);
  };


  return {
    project: null,
    config: null,
    revision: 0,
    saveState: "saved",
    saveCode: null,
    clearSaveError: () => set({ saveCode: null, saveState: saver?.dirty ? "saving" : "saved" }),
    canUndo: false,
    canRedo: false,
    jobs: {},
    serverCompile: null,
    setServerCompile: (c) => set({ serverCompile: c }),
    compileError: null,
    playheadMs: 0,
    setPlayheadMs: (t) => set({ playheadMs: Number.isFinite(t) ? Math.max(0, t) : 0 }),
    playing: false,
    setPlaying: (on) => set({ playing: on }),
    tool: "none",
    setTool: (tool) => set({ tool }),
    lateralProposal: null,
    handledSceneIds: [],

    load: (project) => {
      saver?.cancel();
      stopCompileLoop();
      history = new History(clone(project.config));
      lastEdit = null;
      saver = new Autosaver<CameraSketchConfig>({
        revision: project.current_revision,
        save: async (rev, cfg) => {
          const p = await api.patchProject(project.id, rev, cfg);
          return { revision: p.current_revision, config: p.config };
        },
        onSaved: (rev, cfg) => {
          set({ revision: rev, saveCode: null, compileError: null, saveState: saver?.dirty ? "saving" : "saved" });
          scheduleCompile(project.id, rev, cfg);
        },
        onConflict: async () => {
          const fresh = await api.getProject(project.id);
          get().load(fresh);
          set({ saveState: "conflict" });
        },
        onError: (e) => set({ saveState: "error", saveCode: (e as { code?: string }).code ?? null }),
      });
      const jobs = Object.fromEntries(project.recent_jobs.map((j) => [j.id, j]));
      const handled = handledScenesOnLoad(project.assets, project.config);
      const handledSceneIds = get().project?.id === project.id ? [...new Set([...get().handledSceneIds, ...handled])] : handled;
      set({ project, handledSceneIds, config: clone(project.config), revision: project.current_revision, canUndo: false, canRedo: false,
        saveState: "saved", jobs: { ...get().jobs, ...jobs }, serverCompile: null, compileError: null, lateralProposal: null,
        playing: false, playheadMs: get().project?.id === project.id ? get().playheadMs : 0 });
      scheduleCompile(project.id, project.current_revision, project.config);
    },

    refresh: async () => {
      const p = get().project;
      if (!p) return;
      const fresh = await api.getProject(p.id);
      // Keep local edits; only take server-side lists (assets, jobs).
      set({ project: { ...fresh, config: get().config ?? fresh.config },
        jobs: { ...get().jobs, ...Object.fromEntries(fresh.recent_jobs.map((j) => [j.id, j])) } });
    },

    update: (mutate, coalesce) => {
      const cur = get().config;
      if (!cur || !history) return;
      const next = clone(cur);
      mutate(next);
      if (JSON.stringify(next) === JSON.stringify(cur)) return;
      const now = Date.now();
      if (coalesce && lastEdit?.key === coalesce && now - lastEdit.at < COALESCE_MS && history.canUndo) history.reset(next);
      else history.push(next);
      lastEdit = coalesce ? { key: coalesce, at: now } : null;
      applyLocal(clone(history.current));
    },
    link: (mutate) => {
      const cur = get().config;
      if (!cur || !history) return;
      history.rebase((c) => {
        const next = clone(c);
        mutate(next);
        return next;
      });
      set({ config: clone(history.current), saveState: "saving" });
      saver!.schedule(clone(history.current));
    },
    linkScene: (sceneId) => {
      if (!get().handledSceneIds.includes(sceneId)) set({ handledSceneIds: [...get().handledSceneIds, sceneId] });
      get().link((c) => { c.scene_asset_id = sceneId; c.depth_asset_id = null; });
    },
    undo: () => { lastEdit = null; if (history?.canUndo) applyLocal(clone(history.undo())); },
    redo: () => { lastEdit = null; if (history?.canRedo) applyLocal(clone(history.redo())); },

    commitSketch: (stroke) => {
      const { points } = sketchFromStroke(stroke);
      if (!points.length) return;
      get().update((c) => { c.path.sketch_points = points; });
      const p = get().config?.path;
      set({ lateralProposal: p ? lateralProposal(p.preset, p.lateral_ratio, points) : null });
    },
    applyLateralProposal: () => {
      const v = get().lateralProposal;
      if (v === null) return;
      get().update((c) => { c.path.lateral_ratio = v; });
    },
    dismissLateralProposal: () => set({ lateralProposal: null }),
    retryCompile: () => {
      const l = lastSaved;
      if (!l || l.projectId !== get().project?.id || l.revision !== get().revision) return;
      set({ compileError: null });
      scheduleCompile(l.projectId, l.revision, l.cfg, 0, 1);
    },
    restorePreset: () => get().update((c) => { c.path.sketch_points = []; }),
    applySuggestedParams: (params) => {
      const valid = PARAMS.filter((k) => typeof params[k] === "number" && Number.isFinite(params[k]));
      const ss = params.sketch_scale;
      const scale = typeof ss === "number" && Number.isFinite(ss) && ss > 0 && ss < 1 ? ss : null;
      if (!valid.length && scale === null) return;
      get().update((c) => {
        for (const k of valid) c.path[k as ParamName] = params[k];
        if (scale !== null && c.path.sketch_points.length) c.path.sketch_points = scaleSketchPoints(c.path.sketch_points, scale);
      });
    },

    flush: async () => {
      await saver?.flush();
      return saver?.revision ?? get().revision;
    },
    trackJob: (job) => set({ jobs: { ...get().jobs, [job.id]: job } }),
  };
});

/** The server path to show, or null while the browser's provisional compile should be shown. */
export function currentServerPath(s: Pick<EditorState, "serverCompile" | "revision" | "saveState">) {
  const c = s.serverCompile;
  return c && c.revision === s.revision && s.saveState === "saved" ? c.response.path : null;
}

export interface RenderGate {
  canRender: boolean;
  /** Reason codes (compiler codes or editor codes); map with `reasonKey` for text. */
  blockReasons: string[];
}

/**
 * Render buttons gate: only the server compile of the current saved revision can approve a path.
 * `invalid` blocks with its blocking reasons; `ok` and `warning` allow rendering.
 */
export function renderGate(
  s: Pick<EditorState, "serverCompile" | "revision" | "saveState" | "config"> & { compileError?: string | null },
): RenderGate {
  if (!s.config?.scene_asset_id || !s.config.depth_asset_id) return { canRender: false, blockReasons: ["depth_missing"] };
  if (s.saveState === "error") return { canRender: false, blockReasons: ["save_failed"] };
  if (s.compileError) return { canRender: false, blockReasons: [s.compileError === "depth_missing" ? "depth_missing" : "compile_failed"] };
  const path = currentServerPath(s);
  if (!path) return { canRender: false, blockReasons: ["compile_pending"] };
  if (path.status === "invalid") {
    const r = path.reasons.filter((x) => !NON_BLOCKING.has(x));
    return { canRender: false, blockReasons: r.length ? r : ["unknown"] };
  }
  return { canRender: true, blockReasons: [] };
}

export function assetById(project: CameraSketchProject | null, id: string | null | undefined) {
  return id ? project?.assets.find((a) => a.id === id) : undefined;
}
