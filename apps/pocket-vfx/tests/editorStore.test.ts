import type { PocketVfxConfig, PocketVfxProject } from "@oc/contracts";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  patchProject: vi.fn(async (..._a: unknown[]): Promise<unknown> => { throw new Error("no network in tests"); }),
  getProject: vi.fn(),
}));
vi.mock("../src/api/client", () => ({ api }));
const { useEditor, trackInputs, trackStatus } = await import("../src/state/editorStore");
const { shortcutFor } = await import("../src/hooks/shortcuts");
const { frameIndex } = await import("../src/model/timeline");

const config = (): PocketVfxConfig => ({
  schema_version: 1, video_asset_id: "clip", work_asset_id: "work", trim: { source_start_ms: 0, duration_ms: 5000 },
  effect: { preset_id: "impact_wave_v1", start_ms: 1400, peak_ms: 1800, end_ms: 3400, scale: 1, intensity: 0.8, palette: "amber",
    rotation_deg: 0, squash: 0.5, layer_order: "behind_subject" },
  anchor: { frame: 43, x: 0.5, y: 0.7, follow: true, track_window_frames: 10 },
  corrections: [], subject_prompts: [], mask_edits: [],
  plate: { mode: "bundled", bundled_id: null, asset_id: null, seed: 1, matte: { threshold: 0.06, knee: 0.04 } },
  clean_plate_asset_id: null, audio: { keep_original: true, sfx_enabled: false, sfx_gain_db: -9 }, output: { aspect: "16:9", fps: 24 },
  render_profile: "quick", model_profile: "mock_v1",
});
const project = (): PocketVfxProject => ({
  id: "p", app_slug: "pocket-vfx", title: "t", current_revision: 3, config: config(), assets: [], recent_jobs: [], created_at: "x",
});
const s = () => useEditor.getState();
const window = () => {
  const e = s().config!.effect;
  return [frameIndex(e.start_ms), frameIndex(e.peak_ms), frameIndex(e.end_ms)];
};

beforeEach(() => {
  s().load(project());
  s().setTrackBasis(null);
});

describe("timing handles keep 0 <= start <= peak <= end and snap to frames", () => {
  it("snaps a handle to whole frames", () => {
    s().dragTiming("start", 30);
    expect(window()).toEqual([30, 43, 82]);
    expect(s().config!.effect.start_ms).toBe(1250);
  });
  it("a start dragged past the peak stops at the peak (same ms, never after it)", () => {
    s().dragTiming("start", 60);
    expect(window()).toEqual([43, 43, 82]);
    expect(s().config!.effect.start_ms).toBe(s().config!.effect.peak_ms);
  });
  it("the peak stays between start and end", () => {
    s().dragTiming("peak", 5);
    expect(window()).toEqual([34, 34, 82]);
    expect(s().config!.effect.peak_ms).toBe(1400);
    s().dragTiming("peak", 200);
    expect(window()).toEqual([34, 82, 82]);
    expect(s().config!.effect.peak_ms).toBe(3400);
  });
  it("an end dragged before the peak stops at the peak; frames clamp to 0..N-1", () => {
    s().dragTiming("end", 10);
    expect(s().config!.effect.end_ms).toBe(1800);
    s().dragTiming("end", 500);
    expect(window()).toEqual([34, 43, 119]);
    s().dragTiming("start", -4);
    expect(s().config!.effect.start_ms).toBe(0);
    const e = s().config!.effect;
    expect(e.start_ms <= e.peak_ms && e.peak_ms <= e.end_ms && e.end_ms <= 5000).toBe(true);
  });
  it("one drag gesture is one undo step", () => {
    for (const f of [40, 41, 42]) s().dragTiming("peak", f, "drag:peak");
    expect(window()[1]).toBe(42);
    s().undo();
    expect(window()[1]).toBe(43);
    expect(s().canUndo).toBe(false);
  });
});

describe("track staleness: only inputs of the tracker mark the track stale", () => {
  beforeEach(() => s().setTrackBasis(trackInputs(s().config!)));

  it("is fresh right after tracking and none without a track", () => {
    expect(trackStatus(s().config!, s().trackBasis)).toBe("fresh");
    expect(trackStatus(s().config!, null)).toBe("none");
  });
  it("an anchor edit marks the track stale", () => {
    s().update((c) => { c.anchor.x = 0.4; });
    expect(trackStatus(s().config!, s().trackBasis)).toBe("stale");
  });
  it("an intensity edit (or scale, palette, rotation, squash, timing, layer order) does not", () => {
    s().update((c) => { c.effect.intensity = 0.3; });
    s().update((c) => { c.effect.scale = 1.7; c.effect.palette = "cyan"; c.effect.rotation_deg = 20; c.effect.squash = 0.8; });
    s().update((c) => { c.effect.layer_order = "split_glow"; });
    s().dragTiming("peak", 50);
    expect(trackStatus(s().config!, s().trackBasis)).toBe("fresh");
  });
  it("corrections, subject prompts, mask edits, trim and aspect do", () => {
    const edits: ((c: PocketVfxConfig) => void)[] = [
      (c) => { c.corrections.push({ frame: 50, x: 0.1, y: 0.1 }); },
      (c) => { c.subject_prompts.push({ frame: 43, points: [{ x: 0.5, y: 0.5, label: 1 }] }); },
      (c) => { c.mask_edits.push({ frame: 43, asset_id: "m" }); },
      (c) => { c.trim.duration_ms = 4000; },
      (c) => { c.output.aspect = "9:16"; },
    ];
    for (const edit of edits) {
      s().load(project());
      s().setTrackBasis(trackInputs(s().config!));
      s().update(edit);
      expect(trackStatus(s().config!, s().trackBasis)).toBe("stale");
    }
  });
});

describe("anchor tool", () => {
  it("moves the anchor to the current frame while there is no track", () => {
    s().setFrame(20);
    s().placeAnchor(0.25, 0.75);
    expect(s().config!.anchor).toMatchObject({ frame: 20, x: 0.25, y: 0.75 });
    expect(s().config!.corrections).toEqual([]);
  });
  it("with a track, a click on another frame adds (or replaces) a correction keyframe", () => {
    s().setTrackBasis(trackInputs(s().config!));
    s().setFrame(48);
    s().placeAnchor(0.3, 0.6);
    s().placeAnchor(0.31, 0.61);
    expect(s().config!.corrections).toEqual([{ frame: 48, x: 0.31, y: 0.61 }]);
    expect(s().note).toBeNull();
  });
  it("a correction outside +-track_window_frames is kept but noted (Ruling R12), not silently accepted", () => {
    s().setTrackBasis(trackInputs(s().config!));
    s().setFrame(70);
    s().placeAnchor(0.3, 0.6);
    expect(s().config!.corrections).toEqual([{ frame: 70, x: 0.3, y: 0.6 }]);
    expect(s().note).toEqual({ code: "outside_window", frame: 70, window: 10 });
  });
  it("a nudge on a tracked frame other than the anchor frame sets a correction from the shown tracked position", () => {
    s().setTrackBasis(trackInputs(s().config!));
    s().setFrame(48);
    s().nudge(1, 0, [640, 360], [0.3, 0.6]);
    expect(s().config!.anchor).toMatchObject({ frame: 43, x: 0.5, y: 0.7 });
    expect(s().config!.corrections).toHaveLength(1);
    expect(s().config!.corrections[0].frame).toBe(48);
    expect(s().config!.corrections[0].x).toBeCloseTo(0.3 + 1 / 640, 12);
    expect(s().config!.corrections[0].y).toBeCloseTo(0.6, 12);
    // the next nudge moves that correction, and both nudges are one undo step
    s().nudge(0, 1, [640, 360], [0.9, 0.9]);
    expect(s().config!.corrections[0].y).toBeCloseTo(0.6 + 1 / 360, 12);
    s().undo();
    expect(s().config!.corrections).toEqual([]);
  });
  it("nudges the anchor by whole proxy pixels", () => {
    s().nudge(1, -1, [640, 360]);
    expect(s().config!.anchor.x).toBeCloseTo(0.5 + 1 / 640, 12);
    expect(s().config!.anchor.y).toBeCloseTo(0.7 - 1 / 360, 12);
  });
});

describe("subject points", () => {
  it("adds positive / negative points on the current frame and removes a point clicked again", () => {
    s().setFrame(43);
    s().togglePromptPoint(0.5, 0.4, 1, 0.01);
    s().togglePromptPoint(0.2, 0.2, 0, 0.01);
    expect(s().config!.subject_prompts).toEqual([{ frame: 43, points: [{ x: 0.5, y: 0.4, label: 1 }, { x: 0.2, y: 0.2, label: 0 }] }]);
    s().togglePromptPoint(0.501, 0.401, 1, 0.01);
    expect(s().config!.subject_prompts[0].points).toEqual([{ x: 0.2, y: 0.2, label: 0 }]);
    s().togglePromptPoint(0.2, 0.2, 0, 0.01);
    expect(s().config!.subject_prompts).toEqual([]);
  });
});

describe("shortcuts (shared spec §9)", () => {
  const key = (k: string, mods: Partial<KeyboardEvent> = {}) => ({ key: k, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...mods });
  it("maps undo / redo / play / cancel and is off while typing", () => {
    expect(shortcutFor(key("z", { metaKey: true }), false)).toBe("undo");
    expect(shortcutFor(key("Z", { ctrlKey: true, shiftKey: true }), false)).toBe("redo");
    expect(shortcutFor(key(" "), false)).toBe("play");
    expect(shortcutFor(key(" "), false, true)).toBeNull();
    expect(shortcutFor(key("Escape"), false)).toBe("cancel");
    expect(shortcutFor(key("z", { metaKey: true }), true)).toBeNull();
  });
});
