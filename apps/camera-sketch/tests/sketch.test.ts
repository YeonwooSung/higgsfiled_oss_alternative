import type { PathReason } from "@oc/contracts";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { en } from "../src/i18n/en";
import { ko } from "../src/i18n/ko";
import { compilePathProvisional, PRESETS_V1, sceneInfoFromTarget, simplifySketch } from "../src/model/path";
import {
  addControlPoint, captureSketch, keyframeTimeMs, lateralProposal, motionSuggestion, moveControlPoint, outputToSourceUv, PATH_REASONS, reasonKey,
  removeControlPoint, retime, sketchAtTarget, sketchFromStroke, targetOutputUv,
  sourceToOutputUv, strengthOf, switchPreset, withStrength,
} from "../src/model/sketch";

const slidePath = (over: Partial<Record<string, unknown>> = {}) => ({
  preset: "slide" as const, sketch_space: "target_screen_position" as const, sketch_points: [], lateral_ratio: 0.04, push_ratio: 0,
  arc_deg: 0, easing: "ease_in_out" as const, ...over,
});

describe("captureSketch", () => {
  it("a single click becomes a static path", () => {
    const raw = captureSketch([[0.4, 0.6]]);
    expect(raw).toEqual([[0, 0.4, 0.6]]);
    const { points, warning } = sketchFromStroke([[0.4, 0.6]]);
    expect(warning).toBeNull();
    expect(points).toEqual([{ t: 0, u: 0.4, v: 0.6 }, { t: 1, u: 0.4, v: 0.6 }]);
    const scene = sceneInfoFromTarget({ focalMm: 50, srcW: 1280, srcH: 720, u: 0.5, v: 0.5, depthScale: 1 });
    const c = compilePathProvisional({ ...slidePath({ lateral_ratio: 0 }), sketch_points: points }, scene, 1280, 720);
    expect(c.status).toBe("ok");
    const first = c.samples[0].T_cw, last = c.samples[c.samples.length - 1].T_cw;
    for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) expect(last[i][j]).toBeCloseTo(first[i][j], 9);
  });

  it("caps 300 raw points to 128 (uniform downsample) before simplification", () => {
    const stroke = Array.from({ length: 300 }, (_, i): [number, number] => [0.2 + (0.5 * i) / 299, 0.5 + 0.1 * Math.sin(i / 40)]);
    const raw = captureSketch(stroke);
    expect(raw).toHaveLength(128);
    raw.forEach(([, u, v], k) => {
      const src = stroke[Math.floor((k * 299) / 127)];
      expect([u, v]).toEqual(src);
    });
    expect(raw[0][0]).toBe(0);
    expect(raw[127][0]).toBe(1);
    for (let k = 1; k < raw.length; k++) expect(raw[k][0]).toBeGreaterThan(raw[k - 1][0]);
    const { points } = sketchFromStroke(stroke);
    expect(points.length).toBeGreaterThanOrEqual(2);
    expect(points.length).toBeLessThanOrEqual(5);
    expect(points).toEqual(simplifySketch(raw).controlPoints.map(([t, u, v]) => ({ t, u, v })));
  });

  it("clamps pointer positions to the frame and drops non-finite ones", () => {
    const raw = captureSketch([[-0.2, 0.5], [Number.NaN, 0.5], [1.4, 1.2]]);
    expect(raw).toEqual([[0, 0, 0.5], [1, 1, 1]]);
  });

  it("flags a closed loop as unsupported", () => {
    const loop = Array.from({ length: 60 }, (_, i): [number, number] => [0.5 + 0.2 * Math.cos((2 * Math.PI * i) / 59), 0.5 + 0.2 * Math.sin((2 * Math.PI * i) / 59)]);
    expect(sketchFromStroke(loop).warning).toBe("unsupported_sketch");
  });
});

describe("lateral sign proposal (slide only, never applied silently)", () => {
  const right = [{ t: 0, u: 0.3, v: 0.5 }, { t: 1, u: 0.6, v: 0.5 }];
  const left = [{ t: 0, u: 0.6, v: 0.5 }, { t: 1, u: 0.3, v: 0.5 }];
  it("target moving right on screen proposes a negative lateral", () => {
    expect(lateralProposal("slide", 0.04, right)).toBe(-0.04);
    expect(lateralProposal("slide", 0, right)).toBe(-0.03);
  });
  it("target moving left proposes a positive lateral", () => {
    expect(lateralProposal("slide", -0.02, left)).toBe(0.02);
  });
  it("no proposal when the sign already matches, the stroke is vertical, or the preset is not slide", () => {
    expect(lateralProposal("slide", -0.04, right)).toBeNull();
    expect(lateralProposal("slide", 0.04, [{ t: 0, u: 0.5, v: 0.2 }, { t: 1, u: 0.503, v: 0.8 }])).toBeNull();
    expect(lateralProposal("push_in", 0, right)).toBeNull();
    expect(lateralProposal("small_arc", 0, right)).toBeNull();
  });
});

describe("control point edits", () => {
  it("retime sets t to the arc-length fraction (what the compiler uses)", () => {
    const p = retime([{ t: 0.9, u: 0, v: 0 }, { t: 0.1, u: 0.3, v: 0 }, { t: 0.5, u: 0.4, v: 0 }]);
    expect(p[0].t).toBe(0);
    expect(p[1].t).toBeCloseTo(0.75, 12);
    expect(p[2].t).toBe(1);
  });
  it("moveControlPoint clamps to [0,1] and retimes", () => {
    const p = moveControlPoint([{ t: 0, u: 0.1, v: 0.5 }, { t: 1, u: 0.2, v: 0.5 }], 1, 1.5, 0.5);
    expect(p[1]).toEqual({ t: 1, u: 1, v: 0.5 });
    const s = moveControlPoint([{ t: 0, u: 0.1, v: 0.5 }, { t: 1, u: 0.1, v: 0.5 }], 0, 0.1, 0.5);
    expect(s.map((q) => q.t)).toEqual([0, 1]);
  });
});

describe("output <-> source uv (cover-fit)", () => {
  it("round-trips for a portrait source in a landscape output", () => {
    const [u, v] = outputToSourceUv([0.25, 0.75], 720, 1280, 1280, 720);
    const back = sourceToOutputUv([u, v], 720, 1280, 1280, 720);
    expect(back[0]).toBeCloseTo(0.25, 12);
    expect(back[1]).toBeCloseTo(0.75, 12);
    // Portrait in landscape: full width visible, the middle band of the height.
    expect(u).toBeCloseTo(0.25, 12);
    const visible = 720 / (1280 * (1280 / 720));
    expect(v).toBeCloseTo(0.5 + (0.75 - 0.5) * visible, 12);
  });
  it("the output centre is the source centre", () => {
    expect(outputToSourceUv([0.5, 0.5], 1000, 800, 1280, 720)).toEqual([0.5, 0.5]);
  });
});

describe("preset switching and the mobile strength slider", () => {
  it("zeroes params the new preset does not allow and fills its main motion", () => {
    const p = switchPreset(slidePath({ lateral_ratio: 0.04, push_ratio: 0.02 }), "small_arc", PRESETS_V1);
    expect(p).toMatchObject({ preset: "small_arc", lateral_ratio: 0, push_ratio: 0.02, arc_deg: 4 });
    const q = switchPreset(slidePath({ lateral_ratio: 0.04 }), "push_in", PRESETS_V1);
    expect(q).toMatchObject({ preset: "push_in", lateral_ratio: 0, push_ratio: 0.06, arc_deg: 0 });
  });
  it("strength maps to the active preset's main param, keeping its sign", () => {
    const slide = slidePath({ lateral_ratio: -0.04 });
    expect(strengthOf(slide, PRESETS_V1)).toBeCloseTo(0.8, 12);
    expect(withStrength(slide, 0.5, PRESETS_V1)).toEqual({ lateral_ratio: -0.025 });
    expect(withStrength({ ...slide, preset: "push_in" }, 1, PRESETS_V1)).toEqual({ push_ratio: 0.1 });
    expect(withStrength({ ...slide, preset: "small_arc", arc_deg: 0 }, 0.5, PRESETS_V1)).toEqual({ arc_deg: 3 });
  });
});

describe("reason text", () => {
  it("every compiler reason has en and ko text (no raw codes in the UI)", () => {
    const all: Record<PathReason, true> = {
      unsupported_sketch: true, framing_clamped: true, param_out_of_bounds: true, framing_unsolvable: true, camera_upside_down: true,
      camera_turned_around: true, target_behind_camera: true, target_out_of_frame: true, invalid_input: true, non_finite: true,
      unknown_easing: true, unknown_preset: true,
    };
    expect([...PATH_REASONS].sort()).toEqual(Object.keys(all).sort());
    for (const code of [...PATH_REASONS, "compile_pending", "depth_missing", "holes_high", "compile_failed", "something_new"]) {
      const k = reasonKey(code);
      expect(k in en).toBe(true);
      expect(k in ko).toBe(true);
      expect((en as Record<string, string>)[k]).not.toContain(code);
    }
    expect(reasonKey("something_new")).toBe("reason_unknown");
  });

  it("covers every reason the Python and TS compilers can emit", () => {
    const root = new URL("../../../", import.meta.url);
    const py = readFileSync(new URL("nodes/open_creative/open_creative/camera_sketch/path.py", root), "utf8");
    const ts = readFileSync(new URL("apps/camera-sketch/src/model/path.ts", root), "utf8");
    const found = new Set<string>();
    for (const src of [py, ts]) {
      for (const m of src.matchAll(/fail\(\s*["']([a-z_]+)["']/g)) found.add(m[1]);
      for (const m of src.matchAll(/["'](unsupported_sketch)["']/g)) found.add(m[1]);
    }
    expect(found.size).toBeGreaterThanOrEqual(10);
    for (const code of found) {
      expect(PATH_REASONS as readonly string[]).toContain(code);
      expect(reasonKey(code)).toBe(`reason_${code}`);
      expect((ko as Record<string, string>)[reasonKey(code)]).toBeTruthy();
    }
  });
});

describe("editor helpers", () => {
  const scene = sceneInfoFromTarget({ focalMm: 50, srcW: 1280, srcH: 720, u: 0.5, v: 0.5, depthScale: 1 });
  it("keyframe markers sit at t * frame 119", () => {
    expect(keyframeTimeMs(0)).toBe(0);
    expect(keyframeTimeMs(1)).toBeCloseTo((119 * 1000) / 24, 9);
    const c = compilePathProvisional(slidePath(), scene, 1280, 720);
    expect(keyframeTimeMs(1)).toBeCloseTo(c.samples[c.samples.length - 1].t_ms, 9);
  });
  it("reduced motion: compiler suggestion first, else halve on high holes, else nothing", () => {
    const bad = compilePathProvisional(slidePath({ lateral_ratio: 0.05, sketch_points: [{ t: 0, u: 0.5, v: 0.5 }, { t: 1, u: 0.97, v: 0.5 }] }), scene, 1280, 720);
    const ok = compilePathProvisional(slidePath(), scene, 1280, 720);
    expect(motionSuggestion(ok, 0.01)).toBeNull();
    expect(motionSuggestion(ok, 0.2)).toEqual({ params: { lateral_ratio: 0.02, push_ratio: 0, arc_deg: 0 }, source: "holes" });
    expect(motionSuggestion(null, 0.2)).toBeNull();
    const fake = { ...ok, suggested_params: { lateral_ratio: 0.01, push_ratio: 0, arc_deg: 0 } };
    expect(motionSuggestion(fake, 0.2)).toEqual({ params: { lateral_ratio: 0.01, push_ratio: 0, arc_deg: 0 }, source: "compiler" });
    if (bad.suggested_params) expect(motionSuggestion(bad, null)?.source).toBe("compiler");
  });
  it("target reticle at t=0 is the cover-fit position of the source target", () => {
    const s = sceneInfoFromTarget({ focalMm: 50, srcW: 720, srcH: 1280, u: 0.3, v: 0.4, depthScale: 1 });
    const c = compilePathProvisional(slidePath({ lateral_ratio: 0 }), s, 1280, 720);
    const uv = targetOutputUv(c.K, c.samples[0].T_cw, s.target_world, 1280, 720)!;
    const want = sourceToOutputUv([0.3, 0.4], 720, 1280, 1280, 720);
    expect(uv[0]).toBeCloseTo(want[0], 9);
    expect(uv[1]).toBeCloseTo(want[1], 9);
  });
  it("numeric control point add/remove keeps 2..5 points", () => {
    const two = sketchAtTarget([0.5, 1.2]);
    expect(two).toEqual([{ t: 0, u: 0.5, v: 1 }, { t: 1, u: 0.5, v: 1 }]);
    let p = [{ t: 0, u: 0.2, v: 0.5 }, { t: 1, u: 0.6, v: 0.5 }];
    p = addControlPoint(p);
    expect(p.map((q) => q.u)).toEqual([0.2, 0.4, 0.6]);
    expect(p[1].t).toBeCloseTo(0.5, 12);
    for (let i = 0; i < 5; i++) p = addControlPoint(p);
    expect(p).toHaveLength(5);
    expect(removeControlPoint(p, 2)).toHaveLength(4);
    expect(removeControlPoint([{ t: 0, u: 0, v: 0 }, { t: 1, u: 1, v: 1 }], 0)).toEqual([]);
  });
});
