/**
 * Trim of the uploaded clip (integer ms of the source): 1-5 s, inside the source (`source_start_ms + duration_ms <=
 * source duration`). Handle edits keep the opposite edge fixed and clamp instead of rejecting.
 */
import type { Aspect, VfxTrim } from "@oc/contracts";

export const TRIM_MIN_MS = 1000;
export const TRIM_MAX_MS = 5000;

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
const whole = (v: number) => Math.round(v);

/** The first 5 s, or the whole clip when shorter; null for a clip shorter than 1 s (it cannot be trimmed). */
export function defaultTrim(sourceMs: number): VfxTrim | null {
  const src = Math.floor(sourceMs);
  return src < TRIM_MIN_MS ? null : { source_start_ms: 0, duration_ms: Math.min(TRIM_MAX_MS, src) };
}

/** A saved trim made to fit the source (length first, then the start). `sourceMs` must be >= 1 s. */
export function normalizeTrim(trim: VfxTrim, sourceMs: number): VfxTrim {
  const src = Math.floor(sourceMs);
  const duration = clamp(whole(trim.duration_ms), TRIM_MIN_MS, Math.min(TRIM_MAX_MS, src));
  return { source_start_ms: clamp(whole(trim.source_start_ms), 0, src - duration), duration_ms: duration };
}

/** Start handle: the end stays where it is. */
export function setTrimStart(trim: VfxTrim, startMs: number, sourceMs: number): VfxTrim {
  const end = trim.source_start_ms + trim.duration_ms;
  const start = clamp(whole(startMs), Math.max(0, end - TRIM_MAX_MS), end - TRIM_MIN_MS);
  return normalizeTrim({ source_start_ms: start, duration_ms: end - start }, sourceMs);
}

/** End handle: the start stays where it is. */
export function setTrimEnd(trim: VfxTrim, endMs: number, sourceMs: number): VfxTrim {
  const start = trim.source_start_ms;
  const end = clamp(whole(endMs), start + TRIM_MIN_MS, Math.min(start + TRIM_MAX_MS, Math.floor(sourceMs)));
  return normalizeTrim({ source_start_ms: start, duration_ms: end - start }, sourceMs);
}

/** Slide the whole range to a new start (length kept). */
export function moveTrim(trim: VfxTrim, startMs: number, sourceMs: number): VfxTrim {
  return normalizeTrim({ source_start_ms: whole(startMs), duration_ms: trim.duration_ms }, sourceMs);
}

/** Length from a number field: the start stays unless the range would run past the source. */
export function setTrimDuration(trim: VfxTrim, durationMs: number, sourceMs: number): VfxTrim {
  return normalizeTrim({ source_start_ms: trim.source_start_ms, duration_ms: durationMs }, sourceMs);
}

/** Output canvas of the displayed (rotated) orientation: portrait -> 9:16, otherwise 16:9. */
export function defaultAspect(displayW: number, displayH: number): Aspect {
  return displayH > displayW ? "9:16" : "16:9";
}
