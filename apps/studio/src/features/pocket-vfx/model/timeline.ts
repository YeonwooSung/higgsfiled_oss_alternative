/**
 * Frame mapping and effect timing: the single TS definition of `frameIndex` (port of
 * open_creative/pocket_vfx/timeline.py; `tests/parity.test.ts` holds it equal to the Python output). Editor times are
 * integer ms of the trimmed, normalized 24 fps work clip; frame N-1 is the last valid frame.
 */
import type { VfxEffect } from "@oc/contracts";

export const FPS = 24;

export type EffectTiming = Pick<VfxEffect, "start_ms" | "peak_ms" | "end_ms">;

export interface EffectWindow {
  start: number;
  peak: number;
  end: number;
}

/** Half-up rounding of `tMs` to a 24 fps frame index (same operation order as Python). */
export function frameIndex(tMs: number): number {
  return Math.floor((tMs * FPS) / 1000 + 0.5);
}

/** Frames of a clip of `durationMs` (N; the last valid frame is N-1). */
export function frameCount(durationMs: number): number {
  return frameIndex(durationMs);
}

/** Frame indices of start/peak/end, clamped to the last valid frame `N - 1`. */
export function effectWindow(effect: EffectTiming, durationMs: number): EffectWindow {
  const last = Math.max(frameCount(durationMs) - 1, 0);
  const clamp = (t: number) => Math.min(Math.max(frameIndex(t), 0), last);
  return { start: clamp(effect.start_ms), peak: clamp(effect.peak_ms), end: clamp(effect.end_ms) };
}

export function windowFrames(w: EffectWindow): number {
  return w.end - w.start + 1;
}

export interface TimingError {
  code: "timing_order" | "timing_range";
  field: "start_ms" | "peak_ms" | "end_ms";
}

/** Violations of `0 <= start <= peak <= end <= duration`, in the order the server reports them. */
export function timingErrors(effect: EffectTiming, durationMs: number): TimingError[] {
  const { start_ms: s, peak_ms: p, end_ms: e } = effect;
  const errors: TimingError[] = [];
  if (s > p) errors.push({ code: "timing_order", field: "start_ms" });
  if (p > e) errors.push({ code: "timing_order", field: "peak_ms" });
  if (s < 0) errors.push({ code: "timing_range", field: "start_ms" });
  if (e > durationMs) errors.push({ code: "timing_range", field: "end_ms" });
  return errors;
}

/**
 * Timing that still fits after the clip got shorter (a new trim): an effect running past the end is scaled into the
 * clip (order kept, end on the last ms) and the anchor frame is kept below N. Timing that fits is returned unchanged.
 */
export function fitTiming(timing: EffectTiming, anchorFrame: number, durationMs: number): { timing: EffectTiming; anchorFrame: number } {
  const n = frameCount(durationMs);
  let t = timing;
  if (t.end_ms > durationMs && t.end_ms > 0) {
    const k = durationMs / t.end_ms;
    const fit = (v: number) => Math.min(Math.max(Math.round(v * k), 0), durationMs);
    t = { start_ms: fit(t.start_ms), peak_ms: fit(t.peak_ms), end_ms: durationMs };
  }
  return { timing: t, anchorFrame: Math.min(anchorFrame, Math.max(n - 1, 0)) };
}

// ---- plate -> effect window mapping (port of matte.plate_indices, Ruling R3)

export const RETIME_MIN = 0.5;
export const RETIME_MAX = 2.0;

export class PlateLengthError extends Error {
  constructor(readonly plateFrames: number, readonly windowFrames: number, message?: string) {
    super(message ?? `plate of ${plateFrames} frames cannot cover a ${windowFrames}-frame window`);
    this.name = "PlateLengthError";
  }
}

function checkCounts(plateFrames: number, windowFrames: number) {
  if (plateFrames < 1 || windowFrames < 1) throw new RangeError("frame counts must be >= 1");
}

/** Nearest-frame indices stretching the whole plate over the window (factor within 0.5-2.0). */
export function retimeIndices(plateFrames: number, windowFrames: number): number[] {
  checkCounts(plateFrames, windowFrames);
  const f = plateFrames / windowFrames;
  if (f < RETIME_MIN || f > RETIME_MAX) {
    throw new PlateLengthError(plateFrames, windowFrames, `retime factor ${f.toFixed(3)} outside ${RETIME_MIN}-${RETIME_MAX}`);
  }
  if (windowFrames === 1) return [0];
  const step = (plateFrames - 1) / (windowFrames - 1);
  return Array.from({ length: windowFrames }, (_, k) => Math.floor(k * step + 0.5));
}

/** Plate frame index for each window frame: trim a long plate, retime a close one, loop only a seam-checked one. */
export function plateIndices(plateFrames: number, windowFrames: number, { loopable = false, seamOk = false } = {}): number[] {
  checkCounts(plateFrames, windowFrames);
  if (plateFrames > 2 * windowFrames) return Array.from({ length: windowFrames }, (_, k) => k);
  if (windowFrames <= 2 * plateFrames) return retimeIndices(plateFrames, windowFrames);
  if (loopable && seamOk) return Array.from({ length: windowFrames }, (_, k) => k % plateFrames);
  throw new PlateLengthError(plateFrames, windowFrames);
}
