import type { Job, PocketVfxConfig, PocketVfxProject, VfxPlateResult, VfxValidateResponse } from "@oc/contracts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  patchProject: vi.fn(async (_id: string, rev: number, config: unknown): Promise<unknown> => ({ current_revision: rev + 1, config })),
  getProject: vi.fn(),
  createJob: vi.fn(),
  getJob: vi.fn(),
}));
vi.mock("../src/api/client", () => ({ api }));
const { useEditor, trackInputs, trackStatus } = await import("../src/state/editorStore");
const { renderGate, trackGate, plateGate, startJob, generatedPlates, focusedPlate, selectGeneratedPlate, chooseBundledPlate } =
  await import("../src/hooks/jobs");

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
const project = (jobs: Job[] = []): PocketVfxProject => ({
  id: "p", app_slug: "pocket-vfx", title: "t", current_revision: 3, config: config(), assets: [], recent_jobs: jobs, created_at: "x",
});
const valid: VfxValidateResponse = { valid: true, blocking_errors: [], warnings: [] };
const ok = { result: valid, error: false };
const s = () => useEditor.getState();
const codes = (g: { reasons: { code: string }[] }) => g.reasons.map((r) => r.code);

/** Gate of the store's current state (the same inputs RenderBar passes). */
const gateNow = (validation: { result: VfxValidateResponse | null; error: boolean } | null = ok) => {
  const st = s();
  return renderGate({ config: st.config!, saveState: st.saveState, validation, trackStatus: trackStatus(st.config!, st.trackBasis), jobs: st.jobs, preset: null });
};

beforeEach(() => {
  api.patchProject.mockClear();
  useEditor.setState({ project: null, jobs: {} }); // a fresh editor (load keeps the jobs of the same project)
  s().load(project([job({ id: "track-1" })]));
  s().setTrackBasis(null);
});

describe("render gate", () => {
  it("a following effect needs a track first", () => {
    const g = gateNow();
    expect(g.ok).toBe(false);
    expect(codes(g)).toContain("track_required");
  });

  it("is disabled while the track is stale and enabled after a fresh track", () => {
    s().setTrackBasis(trackInputs(s().config!));
    expect(gateNow()).toMatchObject({ ok: true, trackJobId: "track-1", renderProfile: "quick" });

    s().setFrame(60);
    s().placeAnchor(0.3, 0.3); // a correction keyframe: the track no longer matches the config
    const stale = gateNow();
    expect(stale.ok).toBe(false);
    expect(codes(stale)).toContain("track_stale");
    expect(stale.trackJobId).toBeNull();

    // a new vfx.track ran on this config
    s().trackJob(job({ id: "track-2", created_at: "2026-10-21T11:00:00Z" }));
    s().setTrackBasis(trackInputs(s().config!));
    expect(gateNow()).toMatchObject({ ok: true, trackJobId: "track-2" });
  });

  it("scale / timing edits never make the track stale", () => {
    s().setTrackBasis(trackInputs(s().config!));
    s().update((c) => { c.effect.scale = 1.6; c.effect.peak_ms = 2000; });
    expect(gateNow().ok).toBe(true);
  });

  it("blocks with the server's blocking errors, and while the revision is being checked", () => {
    s().setTrackBasis(trackInputs(s().config!));
    const blocked = gateNow({ result: { valid: false, blocking_errors: [{ code: "mask_required", field: "config.subject_prompts", message: "m" }], warnings: [] }, error: false });
    expect(blocked.ok).toBe(false);
    expect(blocked.reasons).toContainEqual({ code: "mask_required", message: "m" });
    expect(codes(gateNow(null))).toEqual(["checking"]);
    expect(codes(gateNow({ result: null, error: true }))).toEqual(["check_failed"]);
  });

  it("is disabled while a render or a track runs", () => {
    s().setTrackBasis(trackInputs(s().config!));
    s().trackJob(job({ id: "r", kind: "vfx.render", state: "running" }));
    expect(codes(gateNow())).toContain("render_running");
  });

  it("an in-front effect that does not follow renders without a track", () => {
    s().update((c) => { c.anchor.follow = false; c.effect.layer_order = "in_front"; });
    expect(gateNow()).toMatchObject({ ok: true, trackJobId: null });
  });

  it("uses the generated profile only when a generated plate is selected", () => {
    s().setTrackBasis(trackInputs(s().config!));
    selectGeneratedPlate("plate-1");
    expect(s().config!.plate).toMatchObject({ mode: "generated", asset_id: "plate-1" });
    expect(s().config!.render_profile).toBe("generated");
    expect(gateNow().renderProfile).toBe("generated");
    s().undo();
    expect(s().config!.plate.mode).toBe("bundled");
    expect(gateNow().renderProfile).toBe("quick");
  });
});

describe("track and plate gates", () => {
  it("track is blocked only by its prerequisites", () => {
    const res = (code: string) => ({ result: { valid: false, blocking_errors: [{ code, field: "f", message: code }], warnings: [] }, error: false });
    expect(trackGate({ validation: res("mask_required"), jobs: {} }).ok).toBe(true);
    expect(trackGate({ validation: res("analysis_stale"), jobs: {} }).ok).toBe(false);
    expect(codes(trackGate({ validation: ok, jobs: { t: job({ id: "t", state: "running" }) } }))).toEqual(["track_running"]);
  });
  it("plate generation ignores mask and plate errors (it makes the plate)", () => {
    const res = { result: { valid: false, blocking_errors: [
      { code: "mask_required", field: "f", message: "m" }, { code: "plate_length", field: "f", message: "p" },
    ], warnings: [] }, error: false };
    expect(plateGate({ validation: res, jobs: {}, saveState: "saved" }).ok).toBe(true);
    const timing = { result: { valid: false, blocking_errors: [{ code: "timing_order", field: "f", message: "t" }], warnings: [] }, error: false };
    expect(codes(plateGate({ validation: timing, jobs: {}, saveState: "saved" }))).toEqual(["timing_order"]);
  });
});

describe("generated plate with matte check", () => {
  const plateResult = (status: "ok" | "check"): VfxPlateResult => ({
    plate_asset_id: "plate-9", matte_preview_asset_id: "mp", matte_qa_asset_id: "qa",
    matte_qa: { status, bg_nonuniformity: 0.2, flicker: 0.1, coverage: 0.01, reasons: status === "check" ? ["halo_risk", "flicker"] : [] },
    frames: 49, width: 768, height: 448, seed: 1, palette: "amber", preset_id: "impact_wave_v1", mock: true, revision: 3,
  });

  class FakeSource {
    onerror: (() => void) | null = null;
    addEventListener(type: string, fn: () => void) { if (type === "state") setTimeout(fn, 0); }
    close() {}
  }
  beforeEach(() => { vi.stubGlobal("EventSource", FakeSource); });
  afterEach(() => vi.unstubAllGlobals());

  it("never switches the plate automatically; the user picks bundled explicitly", async () => {
    const before = JSON.stringify(s().config!.plate);
    const done = job({ id: "g", kind: "vfx.generate_plate", result: plateResult("check") as unknown as Job["result"] });
    api.createJob.mockResolvedValue({ ...done, state: "queued", result: null });
    api.getJob.mockResolvedValue(done);
    api.getProject.mockResolvedValue({ ...project([done]) });

    const finished = await startJob("vfx.generate_plate");
    expect(finished.state).toBe("succeeded");
    expect(api.createJob.mock.calls[0][4]).toMatch(/[0-9a-f-]{36}/); // Idempotency-Key
    expect(JSON.stringify(s().config!.plate)).toBe(before);
    expect(s().config!.render_profile).toBe("quick");
    expect(s().canUndo).toBe(false);

    const plates = generatedPlates(s().jobs, "impact_wave_v1");
    expect(plates.map((p) => p.result.plate_asset_id)).toEqual(["plate-9"]);
    expect(focusedPlate(plates, s().config!)?.result.matte_qa.reasons).toEqual(["halo_risk", "flicker"]);

    // the user selects it, sees the check, and explicitly goes back to the bundled plate (undoable)
    selectGeneratedPlate("plate-9");
    chooseBundledPlate();
    expect(s().config!.plate).toMatchObject({ mode: "bundled", asset_id: null });
    expect(s().config!.render_profile).toBe("quick");
    s().undo();
    expect(s().config!.plate).toMatchObject({ mode: "generated", asset_id: "plate-9" });
  });
});
