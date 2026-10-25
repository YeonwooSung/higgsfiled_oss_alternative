import { describe, expect, it } from "vitest";
import { MaskBuffer, clampBrush, clipBox, coverage, stampLine } from "../../src/features/product-lock/model/mask";

describe("mask editing", () => {
  it("brush size is clamped to 2..80 source px", () => {
    expect(clampBrush(1)).toBe(2);
    expect(clampBrush(200)).toBe(80);
  });

  it("stamps a round brush along a line and can erase", () => {
    const m = new MaskBuffer(40, 20);
    stampLine(m, 5, 10, 35, 10, 6, 255);
    expect(m.get(5, 10)).toBe(255);
    expect(m.get(20, 10)).toBe(255);
    expect(m.get(20, 13)).toBe(255);
    expect(m.get(20, 16)).toBe(0);
    stampLine(m, 20, 10, 20, 10, 4, 0);
    expect(m.get(20, 10)).toBe(0);
    expect(coverage(m)).toBeGreaterThan(0);
  });

  it("stamps are clipped at the image edge", () => {
    const m = new MaskBuffer(10, 10);
    stampLine(m, 0, 0, 0, 0, 10, 255);
    expect(m.get(0, 0)).toBe(255);
  });

  it("clips protect boxes to the image and reports it", () => {
    expect(clipBox({ x: 0.9, y: -0.1, w: 0.5, h: 0.5 })).toEqual({ box: { x: 0.9, y: 0, w: 0.1, h: 0.4 }, clipped: true });
    expect(clipBox({ x: 0.1, y: 0.1, w: 0.2, h: 0.2 }).clipped).toBe(false);
    expect(clipBox({ x: 1.2, y: 0.1, w: 0.2, h: 0.2 }).box).toBeNull();
  });

  it("builds from alpha values", () => {
    const m = MaskBuffer.fromAlpha(2, 1, new Uint8ClampedArray([0, 0, 0, 0, 0, 0, 0, 200]));
    expect([m.get(0, 0), m.get(1, 0)]).toEqual([0, 200]);
  });
});
