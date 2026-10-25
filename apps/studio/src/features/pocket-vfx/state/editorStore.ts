"use client";

/**
 * Editor state (zustand): the local config with undo/redo (50 steps) and 600 ms autosave on the latest revision
 * (`@oc/web-core` History + Autosaver, as in camera-sketch), the current frame / playback / tool / view, and the
 * editing operations of the stage and timeline. A 409 reloads the project.
 *
 * Track staleness: `trackInputs` is everything the tracker reads (keys.track_key fields minus the source hash);
 * `trackBasis` holds it for the config the latest succeeded vfx.track ran on. Anchor, corrections, subject prompts,
 * mask edits, trim and aspect change it; scale, intensity, palette, rotation, squash, timing and layer order never do
 * (they redraw the local preview only).
 */
import type { Job, PocketVfxConfig, PocketVfxProject, VfxPreset } from "@oc/contracts";
import { Autosaver, History } from "@oc/web-core";
import { create } from "zustand";
import { api } from "../api/client";
import { clampFrame, frameMs, outsideTrackWindow } from "../model/frames";
import type { PreviewView } from "../model/preview";
import { effectWindow, frameCount } from "../model/timeline";

export type SaveState = "saved" | "saving" | "error" | "conflict";
export type Tool = "none" | "anchor" | "subject" | "mask" | "clean_plate";
export type TimingHandle = "start" | "peak" | "end";
export type EditorNote =
  | { code: "outside_window"; frame: number; window: number }
  | { code: "corrections_full" | "prompts_full" | "points_full" | "mask_edits_full" };

/** Server limits (PocketVfxConfig max_length). */
export const LIMITS = { corrections: 32, subjectPrompts: 8, promptPoints: 16, maskEdits: 16 } as const;

/** Inputs of the tracker (`keys.track_key`): any change makes the last track stale. */
export function trackInputs(c: PocketVfxConfig): string {
  const a = c.anchor;
  return JSON.stringify({
    clip: c.video_asset_id, profile: c.model_profile,
    trim: { source_start_ms: c.trim.source_start_ms, duration_ms: c.trim.duration_ms }, aspect: c.output.aspect,
    anchor: { frame: a.frame, x: a.x, y: a.y, follow: a.follow, track_window_frames: a.track_window_frames },
    corrections: c.corrections.map(({ frame, x, y }) => ({ frame, x, y })),
    subject_prompts: c.subject_prompts.map(({ frame, points }) => ({ frame, points: points.map(({ x, y, label }) => ({ x, y, label })) })),
    mask_edits: c.mask_edits.map(({ frame, asset_id }) => ({ frame, asset_id })),
  });
}

export type TrackStatus = "none" | "fresh" | "stale";

export function trackStatus(config: PocketVfxConfig, basis: string | null): TrackStatus {
  if (basis === null) return "none";
  return trackInputs(config) === basis ? "fresh" : "stale";
}

interface EditorState {
  project: PocketVfxProject | null;
  config: PocketVfxConfig | null;
  revision: number;
  saveState: SaveState;
  canUndo: boolean;
  canRedo: boolean;
  jobs: Record<string, Job>;
  load: (project: PocketVfxProject) => void;
  /** Reload assets and jobs, keeping local edits. */
  refresh: () => Promise<void>;
  /** User edit = one undo step; edits with the same `coalesce` key within 1 s (a drag) replace the last step. */
  update: (mutate: (draft: PocketVfxConfig) => void, coalesce?: string) => void;
  /** Automatic link (not a user edit): saved, never an undo step. */
  link: (mutate: (draft: PocketVfxConfig) => void) => void;
  undo: () => void;
  redo: () => void;
  /** Save pending edits now; resolves with the saved revision. */
  flush: () => Promise<number>;
  trackJob: (job: Job) => void;

  /** current frame of the work proxy, 0..N-1 */
  frame: number;
  setFrame: (frame: number) => void;
  playing: boolean;
  setPlaying: (on: boolean) => void;
  tool: Tool;
  setTool: (tool: Tool) => void;
  /** Select subject: label of the next point on touch (Alt gives 0 with a mouse) */
  pointLabel: 0 | 1;
  setPointLabel: (label: 0 | 1) => void;
  view: PreviewView;
  setView: (view: PreviewView) => void;
  effectsOff: boolean;
  setEffectsOff: (off: boolean) => void;
  /** `trackInputs` of the config the latest succeeded track ran on (null: never tracked) */
  trackBasis: string | null;
  setTrackBasis: (basis: string | null) => void;
  /** inline note of the last stage edit (e.g. a correction outside the track window) */
  note: EditorNote | null;
  clearNote: () => void;

  dragTiming: (handle: TimingHandle, frame: number, coalesce?: string) => void;
  /** Anchor tool at the current frame: move the anchor, or (tracked, other frame) set a correction keyframe. */
  placeAnchor: (x: number, y: number, coalesce?: string) => void;
  /**
   * Fine adjust by whole proxy pixels, with the same rule as `placeAnchor`: on a tracked frame other than the anchor
   * frame it sets the correction there, starting from that correction or from `shown` (the tracked position drawn
   * on this frame); otherwise it moves the anchor.
   */
  nudge: (dx: number, dy: number, proxy: [number, number], shown?: [number, number] | null) => void;
  /** Select subject: add a point on the current frame, or remove the point within `hit` (uv) of it. */
  togglePromptPoint: (x: number, y: number, label: 0 | 1, hit: number) => void;
  /** Refine mask: the uploaded mask_edit replaces any earlier edit of `frame`. */
  setMaskEdit: (frame: number, assetId: string) => void;
  selectPreset: (preset: VfxPreset) => void;
}

let history: History<PocketVfxConfig> | null = null;
let saver: Autosaver<PocketVfxConfig> | null = null;
let lastEdit: { key: string; at: number } | null = null;
const COALESCE_MS = 1000;
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
const clamp01 = (v: number) => Math.min(Math.max(v, 0), 1);

export const useEditor = create<EditorState>((set, get) => {
  const applyLocal = (cfg: PocketVfxConfig) => {
    set({ config: cfg, canUndo: history!.canUndo, canRedo: history!.canRedo, saveState: "saving" });
    saver!.schedule(cfg);
  };
  const frames = () => frameCount(get().config?.trim.duration_ms ?? 0);

  return {
    project: null,
    config: null,
    revision: 0,
    saveState: "saved",
    canUndo: false,
    canRedo: false,
    jobs: {},
    frame: 0,
    playing: false,
    tool: "none",
    pointLabel: 1,
    view: "composite",
    effectsOff: false,
    trackBasis: null,
    note: null,

    load: (project) => {
      saver?.cancel();
      history = new History(clone(project.config));
      lastEdit = null;
      saver = new Autosaver<PocketVfxConfig>({
        revision: project.current_revision,
        save: async (rev, cfg) => {
          const p = await api.patchProject(project.id, rev, cfg);
          return { revision: p.current_revision, config: p.config };
        },
        onSaved: (rev) => set({ revision: rev, saveState: saver?.dirty ? "saving" : "saved" }),
        onConflict: async () => {
          const fresh = await api.getProject(project.id);
          if (get().project?.id !== project.id) return; // another project opened meanwhile
          get().load(fresh);
          set({ saveState: "conflict" });
        },
        onError: () => set({ saveState: "error" }),
      });
      const same = get().project?.id === project.id;
      const jobs = Object.fromEntries(project.recent_jobs.map((j) => [j.id, j]));
      const n = frameCount(project.config.trim.duration_ms);
      set({ project, config: clone(project.config), revision: project.current_revision, canUndo: false, canRedo: false,
        saveState: "saved", jobs: same ? { ...get().jobs, ...jobs } : jobs, playing: false, note: null,
        frame: same ? clampFrame(get().frame, n) : clampFrame(project.config.anchor.frame, n),
        trackBasis: same ? get().trackBasis : null, tool: same ? get().tool : "none" });
    },

    refresh: async () => {
      const p = get().project;
      if (!p) return;
      const fresh = await api.getProject(p.id);
      if (get().project?.id !== fresh.id) return;
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
      if (!get().config || !history) return;
      history.rebase((c) => {
        const next = clone(c);
        mutate(next);
        return next;
      });
      set({ config: clone(history.current), saveState: "saving" });
      saver!.schedule(clone(history.current));
    },
    undo: () => { lastEdit = null; if (history?.canUndo) applyLocal(clone(history.undo())); },
    redo: () => { lastEdit = null; if (history?.canRedo) applyLocal(clone(history.redo())); },
    flush: async () => {
      await saver?.flush();
      return saver?.revision ?? get().revision;
    },
    trackJob: (job) => set({ jobs: { ...get().jobs, [job.id]: job } }),

    setFrame: (frame) => set({ frame: clampFrame(Number.isFinite(frame) ? frame : 0, frames()) }),
    setPlaying: (on) => set({ playing: on }),
    setTool: (tool) => set({ tool, note: null }),
    setPointLabel: (pointLabel) => set({ pointLabel }),
    setView: (view) => set({ view }),
    setEffectsOff: (effectsOff) => set({ effectsOff }),
    setTrackBasis: (trackBasis) => set({ trackBasis }),
    clearNote: () => set({ note: null }),

    dragTiming: (handle, frame, coalesce) => {
      const cfg = get().config;
      if (!cfg) return;
      const n = frames();
      const f = clampFrame(frame, n);
      const w = effectWindow(cfg.effect, cfg.trim.duration_ms);
      const e = cfg.effect;
      // A handle stops on its neighbour's frame with the neighbour's exact ms, so the order holds in ms as well.
      let ms: number;
      if (handle === "start") ms = f >= w.peak ? e.peak_ms : frameMs(f);
      else if (handle === "end") ms = f <= w.peak ? e.peak_ms : frameMs(f);
      else ms = f <= w.start ? e.start_ms : f >= w.end ? e.end_ms : frameMs(f);
      ms = Math.min(Math.max(ms, 0), cfg.trim.duration_ms);
      get().update((c) => { c.effect[`${handle}_ms`] = ms; }, coalesce);
    },

    placeAnchor: (x, y, coalesce) => {
      const { config: cfg, frame, trackBasis } = get();
      if (!cfg) return;
      const u = clamp01(x), v = clamp01(y);
      const correction = trackBasis !== null && cfg.anchor.follow && frame !== cfg.anchor.frame;
      if (!correction) {
        set({ note: null });
        get().update((c) => { c.anchor.frame = frame; c.anchor.x = u; c.anchor.y = v; }, coalesce);
        return;
      }
      const existing = cfg.corrections.some((k) => k.frame === frame);
      if (!existing && cfg.corrections.length >= LIMITS.corrections) {
        set({ note: { code: "corrections_full" } });
        return;
      }
      get().update((c) => {
        c.corrections = [...c.corrections.filter((k) => k.frame !== frame), { frame, x: u, y: v }].sort((a, b) => a.frame - b.frame);
      }, coalesce);
      set({ note: outsideTrackWindow(cfg.anchor, frame) ? { code: "outside_window", frame, window: cfg.anchor.track_window_frames! } : null });
    },

    nudge: (dx, dy, [pw, ph], shown) => {
      const { config: cfg, frame, trackBasis } = get();
      if (!cfg) return;
      const correction = trackBasis !== null && cfg.anchor.follow && frame !== cfg.anchor.frame;
      const k = cfg.corrections.find((c) => c.frame === frame);
      const [x, y] = correction ? (k ? [k.x, k.y] : shown ?? [cfg.anchor.x, cfg.anchor.y]) : [cfg.anchor.x, cfg.anchor.y];
      get().placeAnchor(x + dx / pw, y + dy / ph, "nudge");
    },

    togglePromptPoint: (x, y, label, hit) => {
      const { config: cfg, frame } = get();
      if (!cfg) return;
      const prompt = cfg.subject_prompts.find((p) => p.frame === frame);
      const near = prompt?.points.findIndex((p) => Math.hypot(p.x - x, p.y - y) <= hit) ?? -1;
      if (near < 0) {
        if (!prompt && cfg.subject_prompts.length >= LIMITS.subjectPrompts) return set({ note: { code: "prompts_full" } });
        if (prompt && prompt.points.length >= LIMITS.promptPoints) return set({ note: { code: "points_full" } });
      }
      set({ note: null });
      get().update((c) => {
        const p = c.subject_prompts.find((q) => q.frame === frame);
        if (near >= 0 && p) {
          p.points.splice(near, 1);
          if (!p.points.length) c.subject_prompts = c.subject_prompts.filter((q) => q !== p);
        } else if (p) p.points.push({ x: clamp01(x), y: clamp01(y), label });
        else c.subject_prompts = [...c.subject_prompts, { frame, points: [{ x: clamp01(x), y: clamp01(y), label }] }].sort((a, b) => a.frame - b.frame);
      });
    },

    setMaskEdit: (frame, assetId) => {
      const cfg = get().config;
      if (!cfg) return;
      if (!cfg.mask_edits.some((m) => m.frame === frame) && cfg.mask_edits.length >= LIMITS.maskEdits) {
        set({ note: { code: "mask_edits_full" } });
        return;
      }
      get().update((c) => {
        c.mask_edits = [...c.mask_edits.filter((m) => m.frame !== frame), { frame, asset_id: assetId }].sort((a, b) => a.frame - b.frame);
      });
    },

    selectPreset: (preset) => {
      const cfg = get().config;
      if (!cfg || cfg.effect.preset_id === preset.id) return;
      // Controls take the preset defaults; the chosen moment (peak) stays and the window takes the preset durations.
      const d = preset.defaults;
      const last = frameMs(frames() - 1);
      const peak = Math.min(cfg.effect.peak_ms, last);
      get().update((c) => {
        c.effect = {
          ...c.effect, preset_id: preset.id, scale: d.scale, intensity: d.intensity, palette: d.palette, rotation_deg: d.rotation_deg,
          layer_order: d.layer_order, squash: d.squash ?? c.effect.squash,
          start_ms: Math.max(0, peak - d.durations_ms.pre_peak), peak_ms: peak,
          end_ms: Math.max(peak, Math.min(last, peak + d.durations_ms.post_peak)),
        };
      });
    },
  };
});

export function assetById(project: PocketVfxProject | null, id: string | null | undefined) {
  return id ? project?.assets.find((a) => a.id === id) : undefined;
}
