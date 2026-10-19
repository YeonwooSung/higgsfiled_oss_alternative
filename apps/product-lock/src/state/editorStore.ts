"use client";

import type { Asset, AssetRole, Job, ProductLockConfig, Project } from "@oc/contracts";
import { create } from "zustand";
import { api } from "../api/client";
import { Autosaver } from "@oc/web-core";
import { History } from "@oc/web-core";

export type Tool = "select" | "add" | "remove" | "protect";
export type View = "compose" | "mask" | "result";
export type SaveState = "saved" | "saving" | "error" | "conflict";

interface EditorState {
  project: Project | null;
  config: ProductLockConfig | null;
  revision: number;
  saveState: SaveState;
  canUndo: boolean;
  canRedo: boolean;
  tool: Tool;
  brush: number;
  view: View;
  zoom: "fit" | 1 | 2;
  showGrid: boolean;
  selectedRenderJobId: string | null;
  jobs: Record<string, Job>;
  notice: string | null;
  load: (project: Project) => void;
  refresh: () => Promise<void>;
  update: (mutate: (draft: ProductLockConfig) => void) => void;
  undo: () => void;
  redo: () => void;
  flush: () => Promise<number>;
  setTool: (t: Tool) => void;
  setBrush: (n: number) => void;
  setView: (v: View) => void;
  setZoom: (z: "fit" | 1 | 2) => void;
  toggleGrid: () => void;
  selectRender: (jobId: string | null) => void;
  trackJob: (job: Job) => void;
  setNotice: (n: string | null) => void;
}

let history: History<ProductLockConfig> | null = null;
let saver: Autosaver<ProductLockConfig> | null = null;

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

export const useEditor = create<EditorState>((set, get) => {
  const applyLocal = (cfg: ProductLockConfig) => {
    set({ config: cfg, canUndo: history!.canUndo, canRedo: history!.canRedo, saveState: "saving" });
    saver!.schedule(cfg);
  };

  return {
    project: null,
    config: null,
    revision: 0,
    saveState: "saved",
    canUndo: false,
    canRedo: false,
    tool: "select",
    brush: 24,
    view: "compose",
    zoom: "fit",
    showGrid: false,
    selectedRenderJobId: null,
    jobs: {},
    notice: null,

    load: (project) => {
      saver?.cancel();
      history = new History(clone(project.config));
      saver = new Autosaver<ProductLockConfig>({
        revision: project.current_revision,
        save: async (rev, cfg) => {
          const p = await api.patchProject(project.id, rev, cfg);
          return { revision: p.current_revision, config: p.config };
        },
        onSaved: (rev) => set({ revision: rev, saveState: saver?.dirty ? "saving" : "saved" }),
        onConflict: async () => {
          const fresh = await api.getProject(project.id);
          get().load(fresh);
          set({ saveState: "conflict" });
        },
        onError: () => set({ saveState: "error" }),
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
    undo: () => history && history.canUndo && applyLocal(clone(history.undo())),
    redo: () => history && history.canRedo && applyLocal(clone(history.redo())),

    flush: async () => {
      await saver?.flush();
      return saver?.revision ?? get().revision;
    },

    setTool: (tool) => set({ tool, view: "mask" }),
    setBrush: (brush) => set({ brush }),
    setView: (view) => set({ view }),
    setZoom: (zoom) => set({ zoom }),
    toggleGrid: () => set({ showGrid: !get().showGrid }),
    selectRender: (selectedRenderJobId) => set({ selectedRenderJobId, view: selectedRenderJobId ? "result" : "compose" }),
    trackJob: (job) => set({ jobs: { ...get().jobs, [job.id]: job } }),
    setNotice: (notice) => set({ notice }),
  };
});

export function assetsByRole(project: Project | null, role: AssetRole): Asset[] {
  return (project?.assets ?? []).filter((a) => a.role === role && a.state === "ready");
}

export function assetById(project: Project | null, id: string | null | undefined): Asset | undefined {
  return id ? project?.assets.find((a) => a.id === id) : undefined;
}
