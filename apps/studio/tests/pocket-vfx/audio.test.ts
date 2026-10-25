import { describe, expect, it } from "vitest";
import { sfxFromPercent, sfxPercent } from "../../src/features/pocket-vfx/model/audio";

describe("SFX volume 0–100 % <-> −30…0 dB", () => {
  it("maps the ends and the middle", () => {
    expect(sfxFromPercent(100)).toEqual({ sfx_enabled: true, sfx_gain_db: 0 });
    expect(sfxFromPercent(50)).toEqual({ sfx_enabled: true, sfx_gain_db: -15 });
    expect(sfxPercent({ sfx_enabled: true, sfx_gain_db: -9 })).toBe(70);
  });
  it("0 % turns the SFX off; off reads as 0 %", () => {
    expect(sfxFromPercent(0)).toEqual({ sfx_enabled: false });
    expect(sfxPercent({ sfx_enabled: false, sfx_gain_db: -9 })).toBe(0);
    expect(sfxPercent({ sfx_enabled: true, sfx_gain_db: -30 })).toBe(1); // on is never shown as 0 %
  });
});
