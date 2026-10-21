import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { impactCurve, impactGlow } from "../src/model/curves";
import { effectWindow, FPS, frameCount, frameIndex, PlateLengthError, plateIndices, timingErrors } from "../src/model/timeline";

/** Python reference output: `uv run python fixtures/pocket-vfx/make_fixtures.py --parity`. */
interface Timing {
  start_ms: number;
  peak_ms: number;
  end_ms: number;
}
interface Parity {
  version: number;
  fps: number;
  frame_index: { t_ms: number; frame: number }[];
  frame_count: { duration_ms: number; n: number }[];
  effect_window: { effect: Timing; duration_ms: number; window: [number, number, number] }[];
  timing_errors: { effect: Timing; duration_ms: number; errors: { code: string; field: string }[] }[];
  impact: {
    window: [number, number, number];
    short_side: number;
    scale: number;
    intensity: number;
    r_flash: number;
    tau: number;
    samples: { frame: number; curve: [number, number] | null; glow: [number, number, number] | null }[];
  }[];
  plate_indices: { plate_frames: number; window_frames: number; loopable: boolean; seam_ok: boolean; kind: string; indices: number[] | null }[];
}

const FIXTURE = fileURLToPath(new URL("../../../fixtures/pocket-vfx/parity.v1.json", import.meta.url));
const P = JSON.parse(readFileSync(FIXTURE, "utf8")) as Parity;

/** Python and JS share IEEE doubles, but pow/exp may differ in the last ulp between libm and V8. */
const close = (a: number[] | null, b: number[] | null) => {
  if (a === null || b === null) return expect(a).toEqual(b);
  expect(a.length).toBe(b.length);
  a.forEach((x, i) => expect(Math.abs(x - b[i])).toBeLessThanOrEqual(1e-9 * Math.max(1, Math.abs(b[i]))));
};

describe("parity with the Python timeline (parity.v1.json)", () => {
  it("uses the same frame rate", () => {
    expect(P.version).toBe(1);
    expect(FPS).toBe(P.fps);
  });
  it("frameIndex equals frame_index exactly, including the half-frame edges", () => {
    for (const c of P.frame_index) expect([c.t_ms, frameIndex(c.t_ms)]).toEqual([c.t_ms, c.frame]);
  });
  it("frameCount equals frame_count", () => {
    for (const c of P.frame_count) expect(frameCount(c.duration_ms)).toBe(c.n);
  });
  it("effectWindow equals effect_window (clamped to N-1)", () => {
    for (const c of P.effect_window) {
      const w = effectWindow(c.effect, c.duration_ms);
      expect([w.start, w.peak, w.end]).toEqual(c.window);
    }
  });
  it("timingErrors returns the Python codes and fields in the same order", () => {
    for (const c of P.timing_errors) expect(timingErrors(c.effect, c.duration_ms)).toEqual(c.errors);
  });
});

describe("parity with the Python Impact Wave curves", () => {
  it("impactCurve and impactGlow equal the Python ring and anchor glow (<= 1e-9)", () => {
    for (const c of P.impact) {
      const [start, peak, end] = c.window;
      const w = { start, peak, end };
      const params = { scale: c.scale, intensity: c.intensity };
      for (const s of c.samples) {
        close(impactCurve(w, s.frame, params, c.short_side), s.curve);
        close(impactGlow(w, s.frame, params, c.short_side), s.glow);
      }
      // the flash at the peak frame: radius 0.13 * short * scale, full intensity
      close(impactGlow(w, peak, params, c.short_side), [c.r_flash, c.intensity, 1]);
    }
  });
});

describe("parity with the Python plate retime (matte.plate_indices)", () => {
  it("plateIndices maps window frames to the same plate frames, or refuses the same cases", () => {
    for (const c of P.plate_indices) {
      const run = () => plateIndices(c.plate_frames, c.window_frames, { loopable: c.loopable, seamOk: c.seam_ok });
      if (c.kind === "plate_length") expect(run).toThrow(PlateLengthError);
      else if (c.kind === "invalid") expect(run).toThrow(RangeError);
      else expect(run()).toEqual(c.indices);
    }
  });
});
