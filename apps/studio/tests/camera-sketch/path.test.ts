import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { cameraCenter } from "../../src/features/camera-sketch/model/camera";
import { PRESETS_V1, compilePathProvisional, presetCenter, sceneInfoFromTarget, simplifySketch, solveFraming } from "../../src/features/camera-sketch/model/path";

const PRESETS_JSON = fileURLToPath(new URL("../../../../presets/camera-sketch/presets.v1.json", import.meta.url));
const room = sceneInfoFromTarget({ focalMm: 50, srcW: 960, srcH: 540, u: 0.62, v: 0.55, depthScale: 1 });
const sk = (pts: [number, number, number][]) => pts.map(([t, u, v]) => ({ t, u, v }));

describe("presets", () => {
  it("mirror presets/camera-sketch/presets.v1.json exactly", () => {
    expect(PRESETS_V1).toEqual(JSON.parse(readFileSync(PRESETS_JSON, "utf8")).presets);
  });
});

describe("simplifySketch", () => {
  it("turns a single click into a static pair", () => {
    expect(simplifySketch([[0.3, 0.4, 0.6]])).toEqual({ controlPoints: [[0, 0.4, 0.6], [1, 0.4, 0.6]], warning: null });
  });
  it("downsamples > 128 raw points and keeps 2..5 control points ending at t = 1", () => {
    const pts = Array.from({ length: 500 }, (_, k): [number, number, number] => [k, 0.2 + 0.6 * (k / 499), 0.5 + 0.1 * Math.sin(k / 30)]);
    const r = simplifySketch(pts);
    expect(r.controlPoints.length).toBeGreaterThanOrEqual(2);
    expect(r.controlPoints.length).toBeLessThanOrEqual(5);
    expect(r.controlPoints[0][0]).toBe(0);
    expect(r.controlPoints.at(-1)![0]).toBe(1);
  });
  it("flags closed loops and > 120 degree turns across non-consecutive segments", () => {
    const loop = Array.from({ length: 33 }, (_, k): [number, number, number] => [k, 0.5 + 0.1 * Math.cos((2 * Math.PI * k) / 32), 0.5 + 0.1 * Math.sin((2 * Math.PI * k) / 32)]);
    expect(simplifySketch(loop).warning).toBe("unsupported_sketch");
    expect(simplifySketch([[0, 0.3, 0.5], [1, 0.5, 0.5], [2, 0.5, 0.6], [3, 0.3, 0.6]]).warning).toBe("unsupported_sketch");
    expect(simplifySketch([[0, 0.3, 0.5], [1, 0.5, 0.52]]).warning).toBeNull();
  });
  it("throws on an empty sketch", () => {
    expect(() => simplifySketch([])).toThrow();
  });
});

describe("solveFraming", () => {
  it("keeps p0 fixed (even outside the box) and bounds the rest to [0.05, 0.95]", () => {
    const p = solveFraming([[0.99, 0.01], [1.2, -0.2], [1.3, -0.3]]);
    expect(p[0]).toEqual([0.99, 0.01]);
    for (const row of p.slice(1)) for (const x of row) expect(x >= 0.05 && x <= 0.95).toBe(true);
  });
  it("smooths toward the desired path when unconstrained", () => {
    const d = [[0.5, 0.5], [0.6, 0.5], [0.5, 0.5]];
    const p = solveFraming(d);
    expect(p[1][0]).toBeLessThan(0.6);
    expect(p[1][0]).toBeGreaterThan(0.5);
  });
});

describe("presetCenter", () => {
  const T = room.target_world;
  it("starts every preset at the origin and adds push on z", () => {
    for (const p of ["slide", "push_in", "small_arc"]) expect(presetCenter(p, { lateral_ratio: 0.05, push_ratio: 0.1, arc_deg: 6 }, 0, T).map(Math.abs)).toEqual([0, 0, 0]);
    expect(presetCenter("slide", { lateral_ratio: 0.04, push_ratio: 0.1 }, 0.5, T)).toEqual([0.02, 0, 0.05]);
  });
  it("small_arc with a positive angle moves toward +x on the circle around the target", () => {
    const c = presetCenter("small_arc", { arc_deg: 6 }, 1, T);
    expect(c[0]).toBeGreaterThan(0);
    const r0 = Math.hypot(T[0], T[2]);
    expect(Math.hypot(c[0] - T[0], c[2] - T[2])).toBeCloseTo(r0, 12);
    expect(() => presetCenter("dolly", {}, 0.5, T)).toThrow();
  });
});

describe("compilePathProvisional", () => {
  it("returns the CompiledPath shape with 120 samples and keyframes at t=0/1 without a sketch", () => {
    const r = compilePathProvisional({ preset: "slide" }, room, 1280, 720);
    expect(r.status).toBe("ok");
    expect(r.schema_version).toBe(1);
    expect(r.solver_version).toBe("cs_framing_ls_v1");
    expect(r.coordinate_convention).toBe("opencv_T_cw");
    expect(r.fps).toBe(24);
    expect(r.control_points).toEqual([]);
    expect(r.keyframes.map((k) => k.t)).toEqual([0, 1]);
    expect(r.samples).toHaveLength(120);
    expect(r.samples[119].t_ms).toBeCloseTo(119000 / 24, 9);
    expect(r.params).toEqual({ lateral_ratio: 0.04, push_ratio: 0, arc_deg: 0 });
    expect(cameraCenter(r.samples[119].T_cw)[0]).toBeCloseTo(0.04, 12);
    expect(r.depth_mapping).toBeNull();
  });
  it("reports unknown easing, unknown preset and malformed input as invalid without samples", () => {
    expect(compilePathProvisional({ preset: "slide", easing: "bounce" }, room, 1280, 720).reasons).toEqual(["unknown_easing"]);
    const bad = compilePathProvisional({ preset: "slide", sketch_points: [[0, 0.5]] }, room, 1280, 720);
    expect(bad.status).toBe("invalid");
    expect(bad.reasons).toEqual(["invalid_input"]);
    expect(bad.diagnostics.details).toEqual([{ code: "invalid_input", field: "sketch_points" }]);
    expect(bad.samples).toEqual([]);
    expect(compilePathProvisional({ preset: "slide", lateral_ratio: Number.NaN }, room, 1280, 720).diagnostics.details).toEqual([{ code: "invalid_input", field: "lateral_ratio" }]);
    expect(compilePathProvisional({ preset: "slide" }, room, 1280, 720, { nFrames: 1 }).reasons).toEqual(["invalid_input"]);
  });
  it("warns on an unsupported sketch but still compiles", () => {
    const r = compilePathProvisional({ preset: "slide", sketch_points: sk([[0, 0.5, 0.5], [0.5, 0.55, 0.5], [1, 0.5, 0.5]]) }, room, 1280, 720);
    expect(r.status).toBe("warning");
    expect(r.reasons).toEqual(["unsupported_sketch"]);
    expect(r.samples).toHaveLength(120);
  });
  it("suggests halved params for an out-of-bounds motion", () => {
    const r = compilePathProvisional({ preset: "slide", lateral_ratio: 0.08 }, room, 1280, 720);
    expect(r.status).toBe("invalid");
    expect(r.reasons).toEqual(["param_out_of_bounds"]);
    expect(r.suggested_params).toEqual({ lateral_ratio: 0.04, push_ratio: 0, arc_deg: 0, sketch_scale: 1 });
  });
  it("passes the scene's depth mapping through", () => {
    const dm = { kind: "inverse_relative" as const, scale: 1, offset: 0, eps: 1e-6, z_far_cap: 50, target_z: 1 };
    expect(compilePathProvisional({ preset: "push_in" }, { ...room, depth_mapping: dm }, 720, 1280).depth_mapping).toEqual(dm);
  });
});
