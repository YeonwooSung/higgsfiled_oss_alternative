import { describe, expect, it } from "vitest";
import { en } from "../src/i18n/en";
import { ko } from "../src/i18n/ko";

describe("i18n", () => {
  it("Korean resources cover every English key", () => {
    expect(Object.keys(ko).sort()).toEqual(Object.keys(en).sort());
    for (const v of Object.values(ko)) expect(v.length).toBeGreaterThan(0);
  });
});
