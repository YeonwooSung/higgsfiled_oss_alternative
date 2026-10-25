import type { Asset, Job, PocketVfxConfig } from "@oc/contracts";
import { describe, expect, it } from "vitest";
import { applyClipSettings, clipPhase, clipToLink, derivativesPending, derivedAsset, latestJob, linkClip, matchingWork, workToLink } from "../../src/features/pocket-vfx/flow/clip";

let seq = 0;
const asset = (p: Partial<Asset>): Asset => ({
  id: `a${++seq}`, project_id: "p", role: "clip", state: "ready", sha256: null, mime: "video/mp4", bytes: 1, width: 1280, height: 720,
  frame_count: 120, fps_num: null, fps_den: null, duration_ms: 5000, error_code: null, filename: null, source_asset_id: null, job_id: null,
  meta: {}, created_at: `2026-10-21T00:00:0${seq % 10}Z`, ...p,
});
const config = (p: Partial<PocketVfxConfig> = {}): PocketVfxConfig => ({
  schema_version: 1, video_asset_id: null, work_asset_id: null, trim: { source_start_ms: 0, duration_ms: 5000 },
  effect: { preset_id: "impact_wave_v1", start_ms: 1400, peak_ms: 1800, end_ms: 3400, scale: 1, intensity: 0.8, palette: "amber",
    rotation_deg: 0, squash: 0.5, layer_order: "behind_subject" },
  anchor: { frame: 43, x: 0.5, y: 0.7, follow: true, track_window_frames: null },
  corrections: [], subject_prompts: [], mask_edits: [],
  plate: { mode: "bundled", bundled_id: null, asset_id: null, seed: 12345, matte: { threshold: 0.06, knee: 0.04 } },
  clean_plate_asset_id: null, audio: { keep_original: true, sfx_enabled: false, sfx_gain_db: -9 },
  output: { aspect: "16:9", fps: 24 }, render_profile: "quick", model_profile: "mock_v1", ...p,
});
const job = (p: Partial<Job>): Job => ({
  id: `j${++seq}`, project_id: "p", kind: "vfx.analyze", state: "succeeded", stage: null, progress: null, input_revision: 2, options: {},
  model_profile: "mock_v1", cancellation_requested: false, error: null, output_asset_ids: [], result: null,
  created_at: `2026-10-21T00:01:0${seq % 10}Z`, finished_at: null, ...p,
});

describe("clip upload -> config link", () => {
  it("links the newest ready clip once; an inspected clip already linked is not re-linked", () => {
    const old = asset({ created_at: "2026-10-21T00:00:01Z" });
    const pending = asset({ state: "uploaded", created_at: "2026-10-21T00:00:09Z" });
    const fresh = asset({ created_at: "2026-10-21T00:00:05Z" });
    expect(clipToLink([old, fresh, pending], config())).toBe(fresh.id);
    expect(clipToLink([old, fresh], config({ video_asset_id: fresh.id }))).toBeNull();
    expect(clipToLink([pending], config())).toBeNull();
  });
  it("reports the clip phase while uploading, inspecting, rejected and ready", () => {
    expect(clipPhase([])).toBe("none");
    expect(clipPhase([asset({ state: "pending_upload" })])).toBe("uploading");
    expect(clipPhase([asset({ state: "uploaded" })])).toBe("inspecting");
    expect(clipPhase([asset({ state: "rejected", error_code: "unsupported_codec" })])).toBe("rejected");
    expect(clipPhase([asset({})])).toBe("ready");
  });
  it("sets the default trim and the aspect of the displayed orientation", () => {
    const portrait = asset({ width: 720, height: 1280, duration_ms: 4000 });
    const c = linkClip(config({ work_asset_id: "w-old" }), portrait);
    expect(c.video_asset_id).toBe(portrait.id);
    expect(c.trim).toEqual({ source_start_ms: 0, duration_ms: 4000 });
    expect(c.output.aspect).toBe("9:16");
    expect(c.work_asset_id).toBeNull();
    const long = linkClip(config(), asset({ duration_ms: 30_000 }));
    expect(long.trim.duration_ms).toBe(5000);
  });
});

describe("prepared work clip", () => {
  const clip = asset({});
  const work = (trim: { source_start_ms: number; duration_ms: number }, aspect = "16:9", p: Partial<Asset> = {}) =>
    asset({ role: "work_clip", source_asset_id: clip.id, meta: { source_asset_id: clip.id, trim, aspect }, ...p });

  it("matches only a ready work clip of the same clip, trim and aspect", () => {
    const w = work({ source_start_ms: 0, duration_ms: 5000 });
    const other = work({ source_start_ms: 100, duration_ms: 5000 });
    const portrait = work({ source_start_ms: 0, duration_ms: 5000 }, "9:16");
    const cfg = config({ video_asset_id: clip.id });
    expect(matchingWork([clip, other, portrait, w], cfg)?.id).toBe(w.id);
    expect(matchingWork([clip, other], cfg)).toBeUndefined();
    expect(matchingWork([clip, work({ source_start_ms: 0, duration_ms: 5000 }, "16:9", { state: "uploaded" })], cfg)).toBeUndefined();
  });
  it("changing trim or aspect drops the work clip, going back to analyzed settings relinks it", () => {
    const w = work({ source_start_ms: 0, duration_ms: 5000 });
    const cfg = config({ video_asset_id: clip.id, work_asset_id: w.id });
    const changed = applyClipSettings(cfg, { trim: { source_start_ms: 500, duration_ms: 3000 }, aspect: "16:9" }, [clip, w]);
    expect(changed.work_asset_id).toBeNull();
    expect(changed.trim).toEqual({ source_start_ms: 500, duration_ms: 3000 });
    expect(timingFits(changed)).toBe(true);
    const back = applyClipSettings(changed, { trim: { source_start_ms: 0, duration_ms: 5000 }, aspect: "16:9" }, [clip, w]);
    expect(back.work_asset_id).toBe(w.id);
  });
  it("links the analyze result only while it still matches the config", () => {
    const w = work({ source_start_ms: 0, duration_ms: 5000 });
    const done = job({ result: { work_asset_id: w.id } });
    const cfg = config({ video_asset_id: clip.id });
    expect(workToLink([clip, w], cfg, done)).toBe(w.id);
    expect(workToLink([clip, w], config({ video_asset_id: clip.id, work_asset_id: w.id }), done)).toBeNull();
    expect(workToLink([clip, w], config({ video_asset_id: clip.id, trim: { source_start_ms: 0, duration_ms: 4000 } }), done)).toBeNull();
    expect(workToLink([clip, w], cfg, job({ state: "failed", result: { work_asset_id: w.id } }))).toBeNull();
    expect(workToLink([clip, w], cfg, undefined)).toBeNull();
  });
  it("finds derived assets and the latest job of a kind", () => {
    const preview = asset({ role: "clip_preview", source_asset_id: clip.id });
    expect(derivedAsset([clip, preview], "clip_preview", clip.id)?.id).toBe(preview.id);
    expect(derivedAsset([clip, preview], "clip_filmstrip", clip.id)).toBeUndefined();
    const a = job({ created_at: "2026-10-21T00:01:00Z" });
    const b = job({ created_at: "2026-10-21T00:02:00Z" });
    expect(latestJob([a, b, job({ kind: "vfx.track", created_at: "2026-10-21T00:03:00Z" })], "vfx.analyze")?.id).toBe(b.id);
  });
});

function timingFits(c: PocketVfxConfig): boolean {
  return c.effect.end_ms <= c.trim.duration_ms && c.anchor.frame < Math.floor((c.trim.duration_ms * 24) / 1000 + 0.5);
}

describe("frame-indexed edits after a shorter trim", () => {
  it("drops corrections, subject prompts and mask edits at or past the new frame count", () => {
    const clip = asset({ duration_ms: 10_000 });
    const cfg = config({
      video_asset_id: clip.id,
      corrections: [{ frame: 10, x: 0.5, y: 0.5 }, { frame: 23, x: 0.5, y: 0.5 }, { frame: 24, x: 0.5, y: 0.5 }],
      subject_prompts: [{ frame: 5, points: [] }, { frame: 100, points: [] }],
      mask_edits: [{ frame: 24, asset_id: "m1" }, { frame: 0, asset_id: "m0" }],
    });
    const c = applyClipSettings(cfg, { trim: { source_start_ms: 0, duration_ms: 1000 }, aspect: "16:9" }, [clip]); // N = 24
    expect(c.corrections.map((x) => x.frame)).toEqual([10, 23]);
    expect(c.subject_prompts.map((x) => x.frame)).toEqual([5]);
    expect(c.mask_edits.map((x) => x.frame)).toEqual([0]);
    expect(applyClipSettings(cfg, { trim: { source_start_ms: 0, duration_ms: 5000 }, aspect: "16:9" }, [clip]).corrections).toHaveLength(3);
  });
});

describe("inspection derivatives", () => {
  it("reports a ready clip whose preview or filmstrip is not stored yet", () => {
    const clip = asset({});
    const preview = asset({ role: "clip_preview", source_asset_id: clip.id });
    const strip = asset({ role: "clip_filmstrip", source_asset_id: clip.id });
    expect(derivativesPending([clip])).toBe(true);
    expect(derivativesPending([clip, preview])).toBe(true);
    expect(derivativesPending([clip, preview, strip])).toBe(false);
    expect(derivativesPending([asset({ state: "uploaded" })])).toBe(false);
    expect(derivativesPending([])).toBe(false);
  });
});
