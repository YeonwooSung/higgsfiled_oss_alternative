import { describe, expect, it } from "vitest";
import { en } from "../src/i18n/en";
import { ko } from "../src/i18n/ko";

describe("i18n", () => {
  it("Korean resources cover every English key and nothing else", () => {
    expect(Object.keys(ko).sort()).toEqual(Object.keys(en).sort());
    for (const v of Object.values(ko)) expect(v.length).toBeGreaterThan(0);
  });
  it("keeps the same {placeholders} in both languages", () => {
    const ph = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join();
    for (const k of Object.keys(en) as (keyof typeof en)[]) expect(ph(ko[k])).toBe(ph(en[k]));
  });
  it("states that preparing the clip spends no generation", () => {
    expect(en.prepareNoCost.toLowerCase()).toContain("no generation");
    expect(en.sampleSilent.toLowerCase()).toContain("silent");
  });
});
