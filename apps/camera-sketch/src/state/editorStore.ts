"use client";

import type { CameraSketchConfig, CameraSketchProject, Job } from "@oc/contracts";
import { Autosaver, History } from "@oc/web-core";
import { create } from "zustand";
import { api } from "../api/client";

export type SaveState = "saved" | "saving" | "error" | "conflict";

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
  update: (mutate: (draft: CameraSketchConfig) => void) => void;
  /** Automatic link (not a user edit): saved, but never an undo step and never undone. */
  link: (mutate: (draft: CameraSketchConfig) => void) => void;
  undo: () => void;
  redo: () => void;
  flush: () => Promise<number>;
  trackJob: (job: Job) => void;
}

let history: History<CameraSketchConfig> | null = null;
let saver: Autosaver<CameraSketchConfig> | null = null;

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

export const useEditor = create<EditorState>((set, get) => {
  const applyLocal = (cfg: CameraSketchConfig) => {
    set({ config: cfg, canUndo: history!.canUndo, canRedo: history!.canRedo, saveState: "saving" });
    saver!.schedule(cfg);
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

    load: (project) => {
      saver?.cancel();
      history = new History(clone(project.config));
      saver = new Autosaver<CameraSketchConfig>({
        revision: project.current_revision,
        save: async (rev, cfg) => {
          const p = await api.patchProject(project.id, rev, cfg);
          return { revision: p.current_revision, config: p.config };
        },
        onSaved: (rev) => set({ revision: rev, saveCode: null, saveState: saver?.dirty ? "saving" : "saved" }),
        onConflict: async () => {
          const fresh = await api.getProject(project.id);
          get().load(fresh);
          set({ saveState: "conflict" });
        },
        onError: (e) => set({ saveState: "error", saveCode: (e as { code?: string }).code ?? null }),
      });
      const jobs = Object.fromEntries(project.recent_jobs.map((j) => [j.id, j]));
      set({ project, config: clone(project.config), revision: project.current_revision, canUndo: false, canRedo: false,
        saveState: "saved", jobs: { ...get().jobs, ...jobs } });
    },

    refresh: async () => {
      const p = get().project;
      if (!p) return;
      const fresh = await api.getProject(p.id);
      // Keep local edits; only take server-side lists (assets, jobs).
      set({ project: { ...fresh, config: get().config ?? fresh.config },
        jobs: { ...get().jobs, ...Object.fromEntries(fresh.recent_jobs.map((j) => [j.id, j])) } });
    },

    update: (mutate) => {
      const cur = get().config;
      if (!cur || !history) return;
      const next = clone(cur);
      mutate(next);
      history.push(next);
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
    undo: () => history && history.canUndo && applyLocal(clone(history.undo())),
    redo: () => history && history.canRedo && applyLocal(clone(history.redo())),

    flush: async () => {
      await saver?.flush();
      return saver?.revision ?? get().revision;
    },
    trackJob: (job) => set({ jobs: { ...get().jobs, [job.id]: job } }),
  };
});

export function assetById(project: CameraSketchProject | null, id: string | null | undefined) {
  return id ? project?.assets.find((a) => a.id === id) : undefined;
}
