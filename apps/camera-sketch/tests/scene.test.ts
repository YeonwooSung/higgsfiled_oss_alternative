import type { Asset, CameraSketchConfig, Job } from "@oc/contracts";
import { describe, expect, it } from "vitest";
import { SAMPLES, analysisState, depthToLink, sceneToLink } from "../src/flow/scene";

const asset = (p: Partial<Asset>): Asset => ({
  id: "a", project_id: "p", role: "scene", state: "ready", sha256: null, mime: "image/png", bytes: 1, width: 1, height: 1,
  frame_count: null, duration_ms: null, error_code: null, filename: null, source_asset_id: null, job_id: null, meta: {},
  created_at: "2026-01-01T00:00:00Z", ...p,
});
const cfg = (p: Partial<CameraSketchConfig> = {}) => ({ scene_asset_id: null, depth_asset_id: null, ...p }) as CameraSketchConfig;
const job = (p: Partial<Job>): Job => ({ id: "j", project_id: "p", kind: "camera.analyze", state: "queued", created_at: "2026-01-01T00:00:00Z", result: null, ...p }) as Job;

describe("samples", () => {
  it("offers indoor, product table and architecture scenes from public/samples", () => {
    expect(SAMPLES.map((s) => s.id)).toEqual(["room", "product", "facade"]);
    for (const s of SAMPLES) expect(s.url).toMatch(/^\/samples\/\w+\.png$/);
  });
});

describe("sceneToLink", () => {
  it("returns the newest ready scene asset not yet in config", () => {
    const assets = [asset({ id: "old", created_at: "2026-01-01T00:00:00Z" }), asset({ id: "new", created_at: "2026-01-02T00:00:00Z" })];
    expect(sceneToLink(assets, cfg())).toBe("new");
  });
  it("ignores pending assets and already-linked scenes", () => {
    expect(sceneToLink([asset({ state: "uploaded" })], cfg())).toBeNull();
    expect(sceneToLink([asset({ id: "s" })], cfg({ scene_asset_id: "s" }))).toBeNull();
  });
});

describe("depthToLink", () => {
  const scene = asset({ id: "s" });
  it("links a depth asset derived from the current scene", () => {
    const depth = asset({ id: "d", role: "depth", source_asset_id: "s" });
    const j = job({ state: "succeeded", result: { depth_asset_id: "d" } });
    expect(depthToLink([scene, depth], cfg({ scene_asset_id: "s" }), j)).toBe("d");
  });
  it("rejects stale depth after the scene was replaced", () => {
    const depth = asset({ id: "d", role: "depth", source_asset_id: "old" });
    const j = job({ state: "succeeded", result: { depth_asset_id: "d" } });
    expect(depthToLink([scene, depth], cfg({ scene_asset_id: "s" }), j)).toBeNull();
  });
  it("does nothing when already linked or job not succeeded", () => {
    const depth = asset({ id: "d", role: "depth", source_asset_id: "s" });
    expect(depthToLink([depth], cfg({ scene_asset_id: "s", depth_asset_id: "d" }), job({ state: "succeeded", result: { depth_asset_id: "d" } }))).toBeNull();
    expect(depthToLink([depth], cfg({ scene_asset_id: "s" }), job({ state: "running" }))).toBeNull();
  });
});

describe("analysisState", () => {
  it("walks no_scene -> ready -> running -> done / failed", () => {
    expect(analysisState(cfg(), undefined)).toBe("no_scene");
    expect(analysisState(cfg({ scene_asset_id: "s" }), undefined)).toBe("ready");
    expect(analysisState(cfg({ scene_asset_id: "s" }), job({ state: "running" }))).toBe("running");
    expect(analysisState(cfg({ scene_asset_id: "s" }), job({ state: "failed" }))).toBe("failed");
    expect(analysisState(cfg({ scene_asset_id: "s", depth_asset_id: "d" }), job({ state: "succeeded" }))).toBe("done");
  });
  it("is ready again when the scene changed after the analysis (no depth linked)", () => {
    expect(analysisState(cfg({ scene_asset_id: "s2" }), job({ state: "succeeded" }))).toBe("ready");
  });
});
