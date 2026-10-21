import { describe, expect, it } from "vitest";
import { effectWindow, fitTiming, frameCount, frameIndex, PlateLengthError, plateIndices, timingErrors, windowFrames } from "../src/model/timeline";

const timing = (start_ms: number, peak_ms: number, end_ms: number) => ({ start_ms, peak_ms, end_ms });

describe("frame mapping", () => {
  it("rounds half-up at 24 fps; the last valid frame is N-1", () => {
    expect(frameIndex(0)).toBe(0);
    expect(frameIndex(20.8333)).toBe(0);
    expect(frameIndex(20.84)).toBe(1);
    expect(frameIndex(1800)).toBe(43);
    expect(frameCount(5000)).toBe(120);
    expect(frameCount(1000)).toBe(24);
  });
  it("clamps the effect window to the clip", () => {
    expect(effectWindow(timing(1400, 1800, 3400), 5000)).toEqual({ start: 34, peak: 43, end: 82 });
    expect(effectWindow(timing(-100, 0, 9000), 5000)).toEqual({ start: 0, peak: 0, end: 119 });
    expect(windowFrames({ start: 34, peak: 43, end: 82 })).toBe(49);
  });
});

describe("timingErrors (0 <= start <= peak <= end <= duration)", () => {
  it("accepts ordered timing including equal boundaries", () => {
    expect(timingErrors(timing(1400, 1800, 3400), 5000)).toEqual([]);
    expect(timingErrors(timing(0, 0, 5000), 5000)).toEqual([]);
  });
  it("reports order and range violations with the field that moved", () => {
    expect(timingErrors(timing(2000, 1800, 3400), 5000)).toEqual([{ code: "timing_order", field: "start_ms" }]);
    expect(timingErrors(timing(1400, 3500, 3400), 5000)).toEqual([{ code: "timing_order", field: "peak_ms" }]);
    expect(timingErrors(timing(-1, 0, 100), 5000)).toEqual([{ code: "timing_range", field: "start_ms" }]);
    expect(timingErrors(timing(0, 0, 5001), 5000)).toEqual([{ code: "timing_range", field: "end_ms" }]);
  });
});

describe("plateIndices (trim, retime 0.5-2x, loop)", () => {
  it("trims a long plate, retimes a close one and loops only a loopable plate with a good seam", () => {
    expect(plateIndices(49, 20)).toEqual([...Array(20).keys()]);
    expect(plateIndices(9, 5)).toEqual([0, 2, 4, 6, 8]);
    expect(plateIndices(49, 99, { loopable: true, seamOk: true }).slice(47, 51)).toEqual([47, 48, 0, 1]);
    expect(() => plateIndices(49, 99)).toThrow(PlateLengthError);
    expect(() => plateIndices(49, 99, { loopable: true, seamOk: false })).toThrow(PlateLengthError);
  });
});

describe("fitTiming (after a shorter trim)", () => {
  it("leaves timing that fits the clip untouched", () => {
    expect(fitTiming(timing(1400, 1800, 3400), 43, 5000)).toEqual({ timing: timing(1400, 1800, 3400), anchorFrame: 43 });
  });
  it("scales timing that runs past the end into the clip and keeps the anchor inside", () => {
    const { timing: t, anchorFrame } = fitTiming(timing(1400, 1800, 3400), 43, 1700);
    expect(timingErrors(t, 1700)).toEqual([]);
    expect(t.end_ms).toBe(1700);
    expect(t.start_ms).toBeLessThan(t.peak_ms);
    expect(anchorFrame).toBe(Math.min(43, frameCount(1700) - 1));
    expect(fitTiming(timing(1400, 1800, 3400), 43, 1000).anchorFrame).toBe(23);
  });
});
