import type { Job, PocketVfxConfig, VfxRenderResult } from "@oc/contracts";
import { describe, expect, it } from "vitest";
import {
  EXPORT_FORMATS, audioSummary, isMockResult, isPreviousVersion, occlusionView, profileBadgeOf, renderInputs, renderResults,
  roiQaView, trackingWarningItems,
} from "../src/model/results";

const config = (): PocketVfxConfig => ({
  schema_version: 1,
  video_asset_id: "clip-1",
  work_asset_id: "work-1",
  trim: { source_start_ms: 0, duration_ms: 5000 },
  effect: { preset_id: "impact_wave_v1", start_ms: 1000, peak_ms: 1500, end_ms: 2500, scale: 1, intensity: 0.8, palette: "violet",
    rotation_deg: 0, squash: 0.5, layer_order: "behind_subject" },
  anchor: { frame: 36, x: 0.5, y: 0.6, follow: true, track_window_frames: null },
  corrections: [],
  subject_prompts: [],
  mask_edits: [],
  plate: { mode: "bundled", bundled_id: null, asset_id: null, seed: 12345, matte: { threshold: 0.06, knee: 0.04 } },
  clean_plate_asset_id: null,
  audio: { keep_original: true, sfx_enabled: true, sfx_gain_db: -12 },
  output: { aspect: "16:9", fps: 24 },
  render_profile: "quick",
  model_profile: "mock_v1",
});

const result = (over: Partial<VfxRenderResult> = {}): VfxRenderResult => ({
  render_profile: "quick", label: "Quick",
  badge: { render_profile: "quick", label: "Quick", label_ko: "Quick", plate_source: "bundled", mock: false },
  roi_qa: { status: "pass", max_err: 0, worst_frame: null, frames: 120 },
  occlusion_qa: 0, occlusion: true, mask_warnings: [], tracking_warnings: [], plate_source: "bundled", plate_asset_id: null,
  bundled_plate_id: "impact_dust_01", frame_count: 120, mock: false, has_audio: false, audio: null,
  render_video_asset_id: "v", cover_asset_id: "c", roi_qa_asset_id: "q", layers_asset_id: "l", track_job_id: null, revision: 3,
  width: 1280, height: 720, ...over,
});

const job = (id: string, created: string, over: Partial<Job> = {}): Job => ({
  id, project_id: "p", kind: "vfx.render", state: "succeeded", stage: null, progress: null, input_revision: 3, model_profile: "mock_v1",
  options: {}, output_asset_ids: [], result: result(), error: null, created_at: created, updated_at: created, ...over,
} as unknown as Job);

describe("Previous version", () => {
  it("is not marked while the render is the current revision", () => {
    const c = config();
    expect(isPreviousVersion({ renderRevision: 3, renderConfig: c, revision: 3, config: c })).toBe(false);
  });

  it("is marked when a later revision changed a render-affecting field", () => {
    const before = config();
    for (const edit of [
      (c: PocketVfxConfig) => { c.effect.scale = 1.5; },
      (c: PocketVfxConfig) => { c.effect.peak_ms = 1600; },
      (c: PocketVfxConfig) => { c.effect.palette = "amber"; },
      (c: PocketVfxConfig) => { c.anchor.x = 0.4; },
      (c: PocketVfxConfig) => { c.corrections = [{ frame: 50, x: 0.3, y: 0.3 }]; },
      (c: PocketVfxConfig) => { c.plate.seed = 7; },
      (c: PocketVfxConfig) => { c.plate.matte.threshold = 0.1; },
      (c: PocketVfxConfig) => { c.audio.sfx_gain_db = -20; },
      (c: PocketVfxConfig) => { c.plate.mode = "generated"; c.plate.asset_id = "plate-1"; c.render_profile = "generated"; },
    ]) {
      const now = config();
      edit(now);
      expect(isPreviousVersion({ renderRevision: 3, renderConfig: before, revision: 4, config: now })).toBe(true);
    }
  });

  it("is not marked when later revisions changed nothing the render reads", () => {
    // e.g. an edit undone (new revision, same settings), or a plate field the render ignores in this mode
    const before = config();
    const now = config();
    now.plate.asset_id = "plate-unused"; // bundled mode: the render ignores plate.asset_id
    expect(isPreviousVersion({ renderRevision: 3, renderConfig: before, revision: 7, config: config() })).toBe(false);
    expect(isPreviousVersion({ renderRevision: 3, renderConfig: before, revision: 7, config: now })).toBe(false);
    expect(renderInputs(before)).toBe(renderInputs(now));
  });

  it("needs a newer revision: an older current revision (reload race) never marks", () => {
    const now = config();
    now.effect.scale = 2;
    expect(isPreviousVersion({ renderRevision: 5, renderConfig: config(), revision: 4, config: now })).toBe(false);
  });

  it("is unknown (null) until the render's revision config is loaded", () => {
    expect(isPreviousVersion({ renderRevision: 3, renderConfig: undefined, revision: 4, config: config() })).toBeNull();
    expect(isPreviousVersion({ renderRevision: 3, renderConfig: undefined, revision: 3, config: config() })).toBe(false);
  });
});

describe("render badge", () => {
  it("never says Generated plate for a Quick render", () => {
    for (const plate_source of ["bundled", "procedural", "generated"]) {
      expect(profileBadgeOf(result({ render_profile: "quick", plate_source }))).toBe("quick");
    }
  });

  it("says Generated plate only for a generated render with a generated plate", () => {
    expect(profileBadgeOf(result({ render_profile: "generated", plate_source: "generated" }))).toBe("generated");
    expect(profileBadgeOf(result({ render_profile: "generated", plate_source: "bundled" }))).toBe("quick");
    expect(profileBadgeOf(null)).toBe("quick");
  });

  it("is Mock when the result says so or the job ran on a mock profile", () => {
    const mockProfile = (id: string) => id === "mock_v1";
    expect(isMockResult(result({ mock: true }), "open_weights_v1", mockProfile)).toBe(true);
    expect(isMockResult(result({ mock: false }), "mock_v1", mockProfile)).toBe(true);
    expect(isMockResult(result({ mock: false }), "open_weights_v1", mockProfile)).toBe(false);
  });
});

describe("result summaries", () => {
  it("lists succeeded renders newest first", () => {
    const jobs = {
      a: job("a", "2026-10-21T10:00:00Z"),
      b: job("b", "2026-10-21T11:00:00Z"),
      c: job("c", "2026-10-21T12:00:00Z", { state: "failed" }),
      d: job("d", "2026-10-21T13:00:00Z", { kind: "vfx.track" }),
    };
    expect(renderResults(jobs).map((j) => j.id)).toEqual(["b", "a"]);
  });

  it("offers the four export formats", () => {
    expect(EXPORT_FORMATS).toEqual(["mp4", "cover_png", "recipe_zip", "layers_zip"]);
  });

  it("only a well-formed pass is a pass for the outside-ROI check", () => {
    expect(roiQaView(result().roi_qa)).toEqual({ kind: "pass", maxErr: 0 });
    expect(roiQaView({ status: "fail", max_err: 3, worst_frame: 4, frames: 120 }).kind).toBe("fail");
    expect(roiQaView(undefined).kind).toBe("unknown");
    expect(roiQaView({ status: "pass" } as never).kind).toBe("unknown");
  });

  it("describes occlusion by layer order", () => {
    expect(occlusionView(result({ occlusion: false, occlusion_qa: null }), "in_front")).toEqual({ kind: "in_front" });
    expect(occlusionView(result({ occlusion_qa: 0 }), "behind_subject")).toEqual({ kind: "clear" });
    expect(occlusionView(result({ occlusion_qa: 0.042 }), "behind_subject")).toEqual({ kind: "touches", pct: 4.2 });
    expect(occlusionView(result({ occlusion_qa: 0.3 }), "split_glow")).toEqual({ kind: "split_glow", pct: 30 });
    expect(occlusionView(result({ occlusion_qa: null }), "behind_subject")).toEqual({ kind: "unknown" });
  });

  it("names tracking warnings with 1-based frames", () => {
    const items = trackingWarningItems(result({
      tracking_warnings: [
        { code: "track_lost", frames: [[10, 12], [40, 40]], policy: "hide" },
        { code: "track_invalid_at_peak", frame: 36 },
        { code: "camera_not_fixed", status: "handheld" },
        { code: "camera_motion_unknown", status: null },
      ],
      mask_warnings: [{ code: "holes", frame: 5 }],
    }));
    expect(items).toEqual([
      { key: "res_track_lost_hide", vars: { frames: "11–13, 41" } },
      { key: "res_track_invalid_at_peak", vars: { frame: 37 } },
      { key: "res_camera_not_fixed", vars: {} },
      { key: "res_camera_motion_unknown", vars: {} },
      { key: "res_mask_holes", vars: { frames: "6" } },
    ]);
  });

  it("groups mask warnings of one code into runs of consecutive frames", () => {
    const items = trackingWarningItems(result({
      mask_warnings: [
        { code: "mask_empty", frame: 10 }, { code: "area_jump", frame: 11 }, { code: "mask_empty", frame: 11 },
        { code: "mask_empty", frame: 12 }, { code: "mask_empty", frame: 20 }, { code: "area_jump", frame: 21 },
        { code: "area_jump", frame: 22 },
      ],
    }));
    expect(items).toEqual([
      { key: "res_mask_mask_empty", vars: { frames: "11–13, 21" } },
      { key: "res_mask_area_jump", vars: { frames: "12, 22–23" } },
    ]);
  });

  it("summarises the sound", () => {
    expect(audioSummary(result())).toBe("none");
    const audio = { original_kept: true, sfx: true, sfx_delay_ms: 1000, sfx_gain_db: -12, duration_ms: 5000 };
    expect(audioSummary(result({ has_audio: true, audio }))).toBe("original_sfx");
    expect(audioSummary(result({ has_audio: true, audio: { ...audio, sfx: false } }))).toBe("original");
    expect(audioSummary(result({ has_audio: true, audio: { ...audio, original_kept: false } }))).toBe("sfx");
  });
});
