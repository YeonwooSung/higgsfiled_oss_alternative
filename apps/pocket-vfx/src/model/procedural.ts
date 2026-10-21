/**
 * TS port of the procedural effect parameters the client proxy preview draws (open_creative/pocket_vfx/procedural.py
 * and plate.py). `tests/procedural.test.ts` holds radius / alpha / envelopes equal to the Python reference; the
 * shapes themselves (ring profile, glow falloff, palette ramp) are drawn approximately with Canvas 2D.
 */
import type { VfxPalette } from "@oc/contracts";
import { smoothstep } from "./curves";
import type { EffectWindow } from "./timeline";

export { impactCurve, impactGlow, smoothstep } from "./curves";

/** plate.PLATE_SIDE / PLATE_GAIN: the plate square is scale * 0.9 * short side, at 0.9 gain. */
export const PLATE_SIDE = 0.9;
export const PLATE_GAIN = 0.9;
/** R22: the split-glow front glow is the effect layer at alpha x 0.35, drawn over the subject. */
export const SPLIT_GLOW_ALPHA = 0.35;

/** Energy Aura strength over the window (`procedural._aura_envelope`). */
export function auraEnvelope(w: EffectWindow, frame: number): number {
  if (frame < w.start || frame > w.end) return 0;
  if (frame < w.peak) return 0.8 * smoothstep((frame - w.start + 1) / (w.peak - w.start + 1));
  const s = (frame - w.peak) / Math.max(w.end - w.peak, 1);
  const surge = 0.8 + 0.2 * Math.exp(-(frame - w.peak) / 4);
  return surge * (1 - smoothstep((s - 0.65) / 0.35));
}

/** Plate strength over the window (`plate.plate_envelope`): ramps in to the peak, fades over the last 40 %. */
export function plateEnvelope(w: EffectWindow, frame: number): number {
  if (frame < w.start || frame > w.end) return 0;
  if (frame < w.peak) return smoothstep((frame - w.start + 1) / (w.peak - w.start + 1));
  const s = (frame - w.peak) / Math.max(w.end - w.peak, 1);
  return 1 - smoothstep((s - 0.6) / 0.4);
}

type Rgb = [number, number, number];

/** Linear-light palettes of presets.v1.json (edge -> mid -> core). */
export const PALETTES: Record<VfxPalette, { core: Rgb; mid: Rgb; edge: Rgb }> = {
  violet: { core: [0.9047, 0.8308, 1.0], mid: [0.7231, 0.2582, 1.0], edge: [0.0423, 0.0048, 0.1946] },
  amber: { core: [1.0, 0.9047, 0.7157], mid: [1.0, 0.4508, 0.063], edge: [0.1946, 0.0232, 0.0018] },
  cyan: { core: [0.7913, 0.9823, 1.0], mid: [0.1144, 0.7682, 1.0], edge: [0.0018, 0.0513, 0.1946] },
};

const toSrgb8 = (v: number) => {
  const c = Math.min(Math.max(v, 0), 1);
  return Math.round(255 * (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055));
};

/** Palette colour at `heat` in [0, 1] (`procedural._ramp_lut`: edge -> mid over 0..0.35, mid -> core above), sRGB 8-bit. */
export function rampColor(palette: VfxPalette, heat: number): Rgb {
  const p = PALETTES[palette];
  const h = Math.min(Math.max(heat, 0), 1);
  const t1 = Math.min(h / 0.35, 1);
  const t2 = Math.min(Math.max((h - 0.35) / 0.65, 0), 1);
  return [0, 1, 2].map((i) => toSrgb8(p.edge[i] + (p.mid[i] - p.edge[i]) * t1 + (p.core[i] - p.mid[i]) * t2)) as Rgb;
}
