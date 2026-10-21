import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { VfxPalette } from "@oc/contracts";
import { describe, expect, it } from "vitest";
import { anchorAt, planPreview, type PreviewEffect } from "../src/model/preview";
import { auraEnvelope, PALETTES, PLATE_GAIN, PLATE_SIDE, plateEnvelope, rampColor } from "../src/model/procedural";

/** Python reference output (`make_fixtures.py --parity`); the preview is held to radius/alpha only. */
interface Parity {
  impact: {
    window: [number, number, number];
    short_side: number;
    scale: number;
    intensity: number;
    samples: { frame: number; curve: [number, number] | null; glow: [number, number, number] | null }[];
  }[];
  envelopes: { window: [number, number, number]; samples: { frame: number; aura: number; plate: number }[] }[];
}
const read = (rel: string) => JSON.parse(readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8"));
const P = read("../../../fixtures/pocket-vfx/parity.v1.json") as Parity;
const PRESETS = read("../../../nodes/open_creative/open_creative/pocket_vfx/presets.v1.json") as { palettes: Record<VfxPalette, unknown> };

const near = (a: number, b: number) => expect(Math.abs(a - b)).toBeLessThanOrEqual(1e-9 * Math.max(1, Math.abs(b)));
const effect = (scale: number, intensity: number, over: Partial<PreviewEffect> = {}): PreviewEffect => ({
  preset_id: "impact_wave_v1", scale, intensity, squash: 0.5, rotation_deg: 0, palette: "violet", layer_order: "behind_subject", ...over,
});
const win = ([start, peak, end]: [number, number, number]) => ({ start, peak, end });

describe("client preview curves equal the Python reference (parity.v1.json)", () => {
  it("Impact Wave ring radius/alpha and anchor glow radius/alpha", () => {
    for (const c of P.impact) {
      for (const s of c.samples) {
        // portrait canvas: the short side is the width
        const plan = planPreview({ window: win(c.window), frame: s.frame, effect: effect(c.scale, c.intensity), width: c.short_side,
          height: c.short_side * 2, anchor: [10, 20], mask: false, plate: null });
        if (s.curve === null) expect(plan.ring).toBeNull();
        else {
          near(plan.ring!.radius, s.curve[0]);
          near(plan.ring!.alpha, s.curve[1]);
        }
        if (s.glow === null) expect(plan.glow).toBeNull();
        else {
          near(plan.glow!.radius, s.glow[0]);
          near(plan.glow!.alpha, s.glow[1]);
        }
      }
    }
  });
  it("ring geometry follows the procedural layer: width 0.04 * short * scale, squash, clockwise rotation", () => {
    const plan = planPreview({ window: win([34, 43, 82]), frame: 50, effect: effect(1.5, 0.8, { squash: 0.3, rotation_deg: 30 }),
      width: 640, height: 360, anchor: [320, 200], mask: false, plate: null });
    near(plan.ring!.width, 0.04 * 360 * 1.5);
    expect(plan.ring!.squash).toBe(0.3);
    near(plan.ring!.rotation, Math.PI / 6);
    expect([plan.ring!.x, plan.ring!.y]).toEqual([320, 200]);
  });
  it("Energy Aura and plate envelopes", () => {
    for (const c of P.envelopes) {
      for (const s of c.samples) {
        near(auraEnvelope(win(c.window), s.frame), s.aura);
        near(plateEnvelope(win(c.window), s.frame), s.plate);
      }
    }
  });
  it("Energy Aura anchor glow alpha = intensity * envelope, radius 0.12 * short * scale", () => {
    const c = P.envelopes[0];
    for (const s of c.samples) {
      const plan = planPreview({ window: win(c.window), frame: s.frame, effect: effect(0.8, 0.6, { preset_id: "energy_aura_v1" }),
        width: 640, height: 360, anchor: [300, 100], mask: true, plate: null });
      if (s.aura <= 0) expect(plan.aura).toBeNull();
      else {
        near(plan.aura!.alpha, 0.6 * s.aura);
        near(plan.aura!.radius, 0.12 * 360 * 0.8);
        expect(plan.aura!.band).toBe(true);
      }
    }
  });
  it("places the plate preview around the anchor: side 0.9 * short * scale, gain intensity * envelope * 0.9", () => {
    const plan = planPreview({ window: win([34, 43, 82]), frame: 43, effect: effect(1, 0.5), width: 640, height: 360,
      anchor: [100, 120], mask: false, plate: { frames: 49 } });
    near(plan.plate!.side, PLATE_SIDE * 360);
    near(plan.plate!.gain, 0.5 * 1 * PLATE_GAIN);
    expect(plan.plate!.index).toBe(9); // window frame 9 of 49 -> trimmed plate frame 9
    expect(planPreview({ window: win([34, 43, 82]), frame: 30, effect: effect(1, 0.5), width: 640, height: 360,
      anchor: [100, 120], mask: false, plate: { frames: 49 } }).plate).toBeNull();
  });
  it("occludes behind the subject only when a mask exists; split glow adds a front glow", () => {
    const at = (layer_order: PreviewEffect["layer_order"], mask: boolean) => planPreview({ window: win([34, 43, 82]), frame: 45,
      effect: effect(1, 1, { layer_order }), width: 640, height: 360, anchor: [1, 1], mask, plate: null });
    expect(at("behind_subject", true).occlude).toBe(true);
    expect(at("behind_subject", false).occlude).toBe(false);
    expect(at("in_front", true).occlude).toBe(false);
    expect(at("split_glow", true)).toMatchObject({ occlude: true, frontGlow: 0.35 });
    expect(at("behind_subject", true).frontGlow).toBe(0);
  });
  it("hides the effect without an anchor (invalid track frame under the hide policy)", () => {
    const plan = planPreview({ window: win([34, 43, 82]), frame: 45, effect: effect(1, 1), width: 640, height: 360, anchor: null, mask: false, plate: null });
    expect(plan.ring).toBeNull();
    expect(plan.glow).toBeNull();
  });
});

describe("palettes and anchors", () => {
  it("palettes are the preset file's linear palettes; the ramp is sRGB 0..255", () => {
    expect(PALETTES).toEqual(PRESETS.palettes);
    const [r, g, b] = rampColor("violet", 1);
    expect([r, g, b].every((v) => v >= 0 && v <= 255 && Number.isInteger(v))).toBe(true);
    expect(b).toBe(255);
  });
  it("anchorAt ports track.anchor_positions: fixed anchor, valid point, hide / freeze on invalid frames", () => {
    const track = { points: [
      { frame: 0, x: null, y: null, valid: false, confidence: 0 },
      { frame: 1, x: 0.2, y: 0.3, valid: true, confidence: 0.9 },
      { frame: 2, x: null, y: null, valid: false, confidence: 0 },
    ] };
    const anchor = { x: 0.5, y: 0.7, follow: true };
    expect(anchorAt(null, anchor, 2, "hide")).toEqual([0.5, 0.7]);
    expect(anchorAt(track, { ...anchor, follow: false }, 2, "hide")).toEqual([0.5, 0.7]);
    expect(anchorAt(track, anchor, 1, "hide")).toEqual([0.2, 0.3]);
    expect(anchorAt(track, anchor, 2, "hide")).toBeNull();
    expect(anchorAt(track, anchor, 2, "freeze")).toEqual([0.2, 0.3]);
    expect(anchorAt(track, anchor, 0, "freeze")).toBeNull();
  });
});
