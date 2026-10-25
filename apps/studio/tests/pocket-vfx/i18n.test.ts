import { describe, expect, it } from "vitest";
import { en } from "../../src/features/pocket-vfx/i18n/en";
import { ko } from "../../src/features/pocket-vfx/i18n/ko";

describe("i18n", () => {
  it("Korean resources cover every English key and nothing else", () => {
    expect(Object.keys(ko).sort()).toEqual(Object.keys(en).sort());
    for (const v of Object.values(ko)) expect(v.length).toBeGreaterThan(0);
  });
  it("keeps the same {placeholders} in both languages", () => {
    const ph = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join();
    for (const k of Object.keys(en) as (keyof typeof en)[]) expect(ph(ko[k])).toBe(ph(en[k]));
  });
  it("Blink Out copy names its condition and credits the user's photo, never a restored background", () => {
    expect(en.blinkNeeds).toBe("Needs a fixed camera and an empty background frame");
    expect(en.hintBlink.toLowerCase()).toContain("your own empty-background photo");
    expect(en.blinkPhotoNote.toLowerCase()).toContain("nothing is restored");
    expect(en.why_clean_plate_mismatch.toLowerCase()).toContain("fixed camera");
  });
  it("states that preparing the clip spends no generation", () => {
    expect(en.prepareNoCost.toLowerCase()).toContain("no generation");
    expect(en.sampleSilent.toLowerCase()).toContain("silent");
  });
});
