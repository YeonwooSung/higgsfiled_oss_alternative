import { describe, expect, it } from "vitest";
import { outputSize, placeProduct, pyRound, visible } from "../src/model/geometry";

describe("geometry parity with open_creative.product_lock.geometry", () => {
  it("uses Python's round-half-to-even", () => {
    expect(pyRound(230.5)).toBe(230);
    expect(pyRound(231.5)).toBe(232);
    expect(pyRound(-0.5)).toBe(-0);
    expect(pyRound(259.2)).toBe(259);
  });

  it("output sizes", () => {
    expect(outputSize("9:16")).toEqual([720, 1280]);
    expect(outputSize("16:9", true)).toEqual([640, 360]);
  });

  it("places a product exactly like the backend", () => {
    // Same case as nodes/open_creative/tests/test_geometry.py
    const p = placeProduct(1000, 2000, 720, 1280, 0.5, 0.55, 0.36);
    expect(p).toEqual({ x0: 230, y0: 445, w: 259, h: 518 });
  });

  it("rejects out-of-range width", () => {
    expect(() => placeProduct(10, 10, 720, 1280, 0.5, 0.5, 0.8)).toThrow();
  });

  it("clips to the frame", () => {
    const p = placeProduct(400, 400, 720, 1280, 0.98, 0.5, 0.5);
    const v = visible(p, 720, 1280);
    expect(v.frame.x1).toBe(720);
    expect(v.frame.x1 - v.frame.x0).toBe(v.product.x1 - v.product.x0);
  });
});
