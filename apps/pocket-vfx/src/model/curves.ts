/**
 * Impact Wave curves (port of open_creative/pocket_vfx/procedural.py `impact_curve` / `impact_glow`); the client
 * preview draws with them and `tests/parity.test.ts` holds them equal to the Python output (<= 1e-9).
 */
import type { EffectWindow } from "./timeline";

export interface CurveParams {
  scale: number;
  intensity: number;
}

export function smoothstep(x: number): number {
  const c = Math.min(Math.max(x, 0), 1);
  return c * c * (3 - 2 * c);
}

/** Ground ring `[radius_px, alpha]` at `frame`; null outside `[peak, end]`. */
export function impactCurve(w: EffectWindow, frame: number, p: CurveParams, shortSide: number): [number, number] | null {
  if (frame < w.peak || frame > w.end) return null;
  const s = Math.min((frame - w.peak) / Math.max(w.end - w.peak, 1), 1);
  const radius = p.scale * 0.35 * shortSide * (1 - (1 - s) ** 3);
  return [radius, p.intensity * (1 - s) ** 1.5];
}

/**
 * Anchor glow `[radius_px, alpha, heat]` at `frame`; null outside `[start, end]`. Before the peak a charge glow ramps
 * up from `start`; from the peak the flash (radius 0.13 * short * scale) decays with tau = clamp((end-peak)/4, 0.75, 3).
 */
export function impactGlow(w: EffectWindow, frame: number, p: CurveParams, shortSide: number): [number, number, number] | null {
  if (frame < w.start || frame > w.end) return null;
  const rFlash = 0.13 * shortSide * p.scale;
  if (frame < w.peak) {
    const t = smoothstep((frame - w.start + 1) / (w.peak - w.start + 1));
    return [rFlash * (0.35 + 0.65 * t), p.intensity * t, t];
  }
  const tau = Math.min(Math.max((w.end - w.peak) / 4, 0.75), 3);
  const k = Math.exp(-(frame - w.peak) / tau);
  return [rFlash, p.intensity * k, k];
}
