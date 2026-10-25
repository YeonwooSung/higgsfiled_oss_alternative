import type { Job, PocketVfxConfig, PocketVfxProject, VfxValidateResponse } from "@oc/contracts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  patchProject: vi.fn(async (_id: string, rev: number, config: unknown): Promise<unknown> => ({ current_revision: rev + 1, config })),
  getProject: vi.fn(),
  createJob: vi.fn(),
  getJob: vi.fn(),
}));
vi.mock("../../src/features/pocket-vfx/api/client", () => ({ api }));
const { useEditor, trackInputs, trackStatus, LIMITS } = await import("../../src/features/pocket-vfx/state/editorStore");
const { followActiveJobs, stopFollowingJobs, renderGate, retrackFrom, resetCorrections } = await import("../../src/features/pocket-vfx/hooks/jobs");
const { parseSeed, SEED_MAX } = await import("../../src/features/pocket-vfx/model/seed");

const config = (): PocketVfxConfig => ({
  schema_version: 1, video_asset_id: "clip", work_asset_id: "work", trim: { source_start_ms: 0, duration_ms: 5000 },
  effect: { preset_id: "impact_wave_v1", start_ms: 1400, peak_ms: 1800, end_ms: 3400, scale: 1, intensity: 0.8, palette: "amber",
    rotation_deg: 0, squash: 0.5, layer_order: "behind_subject" },
  anchor: { frame: 43, x: 0.5, y: 0.7, follow: true, track_window_frames: null },
  corrections: [], subject_prompts: [], mask_edits: [],
  plate: { mode: "bundled", bundled_id: null, asset_id: null, seed: 1, matte: { threshold: 0.06, knee: 0.04 } },
  clean_plate_asset_id: null, audio: { keep_original: true, sfx_enabled: false, sfx_gain_db: -9 }, output: { aspect: "16:9", fps: 24 },
  render_profile: "quick", model_profile: "mock_v1",
});
const job = (over: Partial<Job>): Job => ({
  id: "j", project_id: "p", kind: "vfx.track", state: "succeeded", stage: null, progress: null, input_revision: 3, options: {},
  model_profile: "mock_v1", cancellation_requested: false, error: null, output_asset_ids: [], result: null,
  created_at: "2026-10-21T10:00:00Z", finished_at: null, ...over,
});
const project = (jobs: Job[] = [], cfg = config()): PocketVfxProject => ({
  id: "p", app_slug: "pocket-vfx", title: "t", current_revision: 3, config: cfg, assets: [], recent_jobs: jobs, created_at: "x", upstream_mock: false, handoff: null,
});
const valid: VfxValidateResponse = { valid: true, blocking_errors: [], warnings: [] };
const s = () => useEditor.getState();
const gateNow = () => {
  const st = s();
  return renderGate({ config: st.config!, saveState: st.saveState, validation: { result: valid, error: false },
    trackStatus: trackStatus(st.config!, st.trackBasis), jobs: st.jobs, preset: null });
};

/** EventSource stand-in: `fire()` sends a "state" event to every open source. */
const sources: { listeners: (() => void)[]; closed: boolean }[] = [];
class FakeSource {
  onerror: (() => void) | null = null;
  private entry = { listeners: [] as (() => void)[], closed: false };
  constructor() { sources.push(this.entry); }
  addEventListener(type: string, fn: () => void) { if (type === "state") this.entry.listeners.push(fn); }
  close() { this.entry.closed = true; }
}
const fire = () => { for (const src of sources) if (!src.closed) for (const fn of src.listeners) fn(); };

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeSource);
  sources.length = 0;
  api.createJob.mockReset();
  api.getJob.mockReset();
  api.getProject.mockReset();
  useEditor.setState({ project: null, jobs: {} });
});
afterEach(() => {
  stopFollowingJobs();
  vi.unstubAllGlobals();
});

describe("jobs already running when the editor loads", () => {
  it("are followed once, and the gate re-opens when the render finishes", async () => {
    const running = job({ id: "r1", kind: "vfx.render", state: "running" });
    s().load(project([job({ id: "t1" }), running]));
    s().setTrackBasis(trackInputs(s().config!));
    expect(gateNow().reasons.map((r) => r.code)).toEqual(["render_running"]);

    followActiveJobs(s().jobs);
    followActiveJobs(s().jobs); // a second pass (e.g. a refresh) does not open another watcher
    expect(sources).toHaveLength(1);

    const done = { ...running, state: "succeeded" as const };
    api.getJob.mockResolvedValue(done);
    api.getProject.mockResolvedValue(project([job({ id: "t1" }), done]));
    fire();
    await vi.waitFor(() => expect(s().jobs.r1.state).toBe("succeeded"));
    expect(gateNow()).toMatchObject({ ok: true, trackJobId: "t1" });
    expect(sources[0].closed).toBe(true);
  });

  it("stopping (editor unmount) closes the watchers", () => {
    s().load(project([job({ id: "g1", kind: "vfx.generate_plate", state: "queued" })]));
    followActiveJobs(s().jobs);
    stopFollowingJobs();
    expect(sources[0].closed).toBe(true);
  });
});

describe("Re-track from here and Reset", () => {
  beforeEach(() => {
    s().load(project([job({ id: "t1" })]));
    s().setTrackBasis(trackInputs(s().config!));
    s().setFrame(60);
  });

  it("adds the correction on the current frame at the shown position, then starts a track", async () => {
    api.createJob.mockImplementation(async (_p: string, kind: string, revision: number) => job({ id: "t2", kind: kind as Job["kind"], state: "succeeded", input_revision: revision }));
    const p = retrackFrom([0.3, 0.4]);
    expect(p).not.toBeNull();
    expect(s().config!.corrections).toEqual([{ frame: 60, x: 0.3, y: 0.4 }]);
    expect(s().config!.anchor).toMatchObject({ frame: 43, x: 0.5, y: 0.7 });
    await p;
    expect(api.createJob).toHaveBeenCalledTimes(1);
    const [, kind, revision, options, key] = api.createJob.mock.calls[0];
    expect([kind, options]).toEqual(["vfx.track", {}]);
    expect(revision).toBe(4); // the correction was saved first (flush), the job pins that revision
    expect(key).toMatch(/[0-9a-f-]{36}/);
  });

  it("returns null and starts no job when 32 corrections are already set", () => {
    const frames = Array.from({ length: 120 }, (_, f) => f).filter((f) => f !== 60 && f !== 43).slice(0, LIMITS.corrections);
    s().update((c) => { c.corrections = frames.map((frame) => ({ frame, x: 0.5, y: 0.5 })); });
    expect(s().config!.corrections).toHaveLength(LIMITS.corrections);
    expect(retrackFrom([0.3, 0.4])).toBeNull();
    expect(api.createJob).not.toHaveBeenCalled();
    expect(s().config!.corrections.some((k) => k.frame === 60)).toBe(false);
  });

  it("Reset removes every correction in one undoable step and keeps the anchor", () => {
    s().placeAnchor(0.2, 0.2);
    s().setFrame(80);
    s().placeAnchor(0.6, 0.6);
    const before = s().config!.corrections;
    expect(before).toHaveLength(2);
    resetCorrections();
    expect(s().config!.corrections).toEqual([]);
    expect(s().config!.anchor).toMatchObject({ frame: 43, x: 0.5, y: 0.7 });
    s().undo();
    expect(s().config!.corrections).toEqual(before);
  });
});

describe("seed field", () => {
  it("ignores empty and partial input instead of writing 0", () => {
    expect(parseSeed("")).toBeNull();
    expect(parseSeed("  ")).toBeNull();
    expect(parseSeed("-")).toBeNull();
    expect(parseSeed("1e3")).toBeNull();
    expect(parseSeed("12.5")).toBeNull();
    expect(parseSeed("0")).toBe(0);
    expect(parseSeed(" 4242 ")).toBe(4242);
    expect(parseSeed("99999999999")).toBe(SEED_MAX);
  });
});

describe("following store jobs", () => {
  it("does not write a store job back (no update loop with the effect on `jobs`)", () => {
    s().load(project([job({ id: "r9", kind: "vfx.render", state: "running" })]));
    const jobs = s().jobs;
    followActiveJobs(jobs);
    followActiveJobs(s().jobs);
    expect(s().jobs).toBe(jobs);
  });
});
