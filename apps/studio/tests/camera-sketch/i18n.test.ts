import { describe, expect, it } from "vitest";
import { en } from "../../src/features/camera-sketch/i18n/en";
import { ko } from "../../src/features/camera-sketch/i18n/ko";

describe("i18n", () => {
  it("Korean resources cover every English key and nothing else", () => {
    expect(Object.keys(ko).sort()).toEqual(Object.keys(en).sort());
    for (const v of Object.values(ko)) expect(v.length).toBeGreaterThan(0);
  });
  it("keeps the same {placeholders} in both languages", () => {
    const ph = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join();
    for (const k of Object.keys(en) as (keyof typeof en)[]) expect(ph(ko[k])).toBe(ph(en[k]));
  });
  it("labels the editor hole ratio as a preview estimate and the guide result as measured", () => {
    expect(en.holeRatio.startsWith("Estimated holes (preview)")).toBe(true);
    expect(en.holeDeferred).toContain("Estimated holes (preview)");
    expect(en.holeRatioResult.startsWith("Measured holes")).toBe(true);
    expect(en.estimateHoleMax.startsWith("Measured")).toBe(true);
    expect(ko.holeRatio.startsWith("예상 구멍(미리보기)")).toBe(true);
    expect(ko.holeDeferred).toContain("예상 구멍(미리보기)");
    expect(ko.holeRatioResult.startsWith("측정된 구멍")).toBe(true);
    expect(ko.estimateHoleMax.startsWith("측정된")).toBe(true);
  });
  it("states that analysis spends no video generation cost", () => {
    expect(en.analyzeNoCost.toLowerCase()).toContain("no video generation");
  });
});
