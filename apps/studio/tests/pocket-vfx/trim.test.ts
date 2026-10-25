import { describe, expect, it } from "vitest";
import { defaultAspect, defaultTrim, moveTrim, normalizeTrim, setTrimDuration, setTrimEnd, setTrimStart, TRIM_MAX_MS, TRIM_MIN_MS } from "../../src/features/pocket-vfx/model/trim";

const tr = (source_start_ms: number, duration_ms: number) => ({ source_start_ms, duration_ms });

describe("trim range (1-5 s inside the source)", () => {
  it("defaults to the first 5 s, or the whole clip when shorter; refuses clips under 1 s", () => {
    expect(defaultTrim(12_000)).toEqual(tr(0, 5000));
    expect(defaultTrim(3210.6)).toEqual(tr(0, 3210));
    expect(defaultTrim(999)).toBeNull();
    expect([TRIM_MIN_MS, TRIM_MAX_MS]).toEqual([1000, 5000]);
  });
  it("normalizes a saved trim that no longer fits", () => {
    expect(normalizeTrim(tr(0, 5000), 3000)).toEqual(tr(0, 3000));
    expect(normalizeTrim(tr(9000, 4000), 10_000)).toEqual(tr(6000, 4000));
    expect(normalizeTrim(tr(-5, 200), 10_000)).toEqual(tr(0, 1000));
  });
  it("moves the start handle with the end fixed, within 1-5 s", () => {
    expect(setTrimStart(tr(2000, 3000), 1000, 10_000)).toEqual(tr(1000, 4000));
    expect(setTrimStart(tr(2000, 3000), 0, 10_000)).toEqual(tr(0, 5000));
    expect(setTrimStart(tr(2000, 3000), 4900, 10_000)).toEqual(tr(4000, 1000));
  });
  it("moves the end handle with the start fixed, never past the source", () => {
    expect(setTrimEnd(tr(2000, 3000), 6000, 10_000)).toEqual(tr(2000, 4000));
    expect(setTrimEnd(tr(2000, 3000), 9999, 10_000)).toEqual(tr(2000, 5000));
    expect(setTrimEnd(tr(8000, 1500), 9999, 9800)).toEqual(tr(8000, 1800));
    expect(setTrimEnd(tr(2000, 3000), 2100, 10_000)).toEqual(tr(2000, 1000));
  });
  it("slides the whole range and sets the length from a number field", () => {
    expect(moveTrim(tr(2000, 3000), 8000, 10_000)).toEqual(tr(7000, 3000));
    expect(moveTrim(tr(2000, 3000), -50, 10_000)).toEqual(tr(0, 3000));
    expect(setTrimDuration(tr(2000, 3000), 7000, 10_000)).toEqual(tr(2000, 5000));
    expect(setTrimDuration(tr(8500, 1000), 3000, 10_000)).toEqual(tr(7000, 3000));
    expect(setTrimDuration(tr(0, 3000), 10, 10_000)).toEqual(tr(0, 1000));
  });
});

describe("defaultAspect", () => {
  it("follows the displayed (rotated) orientation", () => {
    expect(defaultAspect(1280, 720)).toBe("16:9");
    expect(defaultAspect(720, 1280)).toBe("9:16");
    expect(defaultAspect(1000, 1000)).toBe("16:9");
  });
});
