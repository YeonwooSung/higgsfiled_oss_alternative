/**
 * Frame <-> media time of the 24 fps work proxy, and per-frame track data. Every mapping goes through
 * `timeline.frameIndex` (the single frame rule): a presented frame's PTS (`mediaTime`, i/24 s) maps with it directly;
 * a seek lands on frame i at its centre `(i + 0.5) / 24` s, and `currentTime` (a seek target, not a PTS) maps back
 * after removing that half frame.
 */
import type { VfxAnchor } from "@oc/contracts";
import { FPS, frameIndex } from "./timeline";

export function clampFrame(frame: number, n: number): number {
  return Math.min(Math.max(Math.floor(frame), 0), Math.max(n - 1, 0));
}

/** Seek target (s) of frame `i`: its centre, so decoders never land on the neighbour. */
export function seekTime(frame: number): number {
  return (frame + 0.5) / FPS;
}

/** Frame of a presented video frame (`requestVideoFrameCallback` metadata.mediaTime = its PTS, s). */
export function frameOfMediaTime(seconds: number, n: number): number {
  return clampFrame(frameIndex(seconds * 1000), n);
}

/** Frame shown at `video.currentTime` (the frame whose PTS is at or before it; fallback without rVFC). */
export function frameOfCurrentTime(seconds: number, n: number): number {
  return clampFrame(frameIndex(seconds * 1000 - 500 / FPS), n);
}

/** Integer ms of frame `i` (its PTS, rounded); `frameIndex(frameMs(i)) === i`. Effect timing is stored in these. */
export function frameMs(frame: number): number {
  return Math.round((frame * 1000) / FPS);
}

/** Frames covered by inclusive `[first, last]` invalid intervals, limited to 0..N-1. */
export function invalidFrames(intervals: readonly (readonly [number, number])[], n: number): Set<number> {
  const out = new Set<number>();
  for (const [a, b] of intervals) for (let f = Math.max(a, 0); f <= Math.min(b, n - 1); f++) out.add(f);
  return out;
}

export function firstInvalidFrame(intervals: readonly (readonly [number, number])[]): number | null {
  return intervals.length ? Math.min(...intervals.map(([a]) => a)) : null;
}

/**
 * The tracker ignores a correction keyframe farther than `track_window_frames` from the anchor frame (Ruling R12):
 * the editor keeps it but says so.
 */
export function outsideTrackWindow(anchor: Pick<VfxAnchor, "frame" | "track_window_frames">, frame: number): boolean {
  const w = anchor.track_window_frames;
  return w !== null && Math.abs(frame - anchor.frame) > w;
}
