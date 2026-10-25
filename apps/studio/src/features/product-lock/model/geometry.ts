// Mirrors open_creative/product_lock/geometry.py so the editor preview places the
// product on exactly the same pixels as the server render.
import type { Aspect } from "@oc/contracts";

export const WIDTH_RATIO_MIN = 0.15;
export const WIDTH_RATIO_MAX = 0.7;

const OUTPUT: Record<Aspect, [number, number]> = { "16:9": [1280, 720], "9:16": [720, 1280] };
const DRAFT: Record<Aspect, [number, number]> = { "16:9": [640, 360], "9:16": [360, 640] };

export interface Placement {
  x0: number;
  y0: number;
  w: number;
  h: number;
}

/** Python's round(): halves go to the nearest even integer. */
export function pyRound(x: number): number {
  const r = Math.round(x);
  if (Math.abs(x % 1) === 0.5 && r % 2 !== 0) return r - 1;
  return r;
}

export function outputSize(aspect: Aspect, draft = false): [number, number] {
  return (draft ? DRAFT : OUTPUT)[aspect];
}

export function placeProduct(
  srcW: number,
  srcH: number,
  outW: number,
  outH: number,
  centerX: number,
  centerY: number,
  widthRatio: number,
): Placement {
  if (widthRatio < WIDTH_RATIO_MIN || widthRatio > WIDTH_RATIO_MAX) throw new RangeError("width ratio out of range");
  const w = Math.max(1, pyRound(widthRatio * outW));
  const h = Math.max(1, pyRound((w * srcH) / srcW));
  return { x0: pyRound(centerX * outW - w / 2), y0: pyRound(centerY * outH - h / 2), w, h };
}

export interface Span {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export function visible(p: Placement, outW: number, outH: number): { frame: Span; product: Span } {
  const fx0 = Math.max(p.x0, 0);
  const fy0 = Math.max(p.y0, 0);
  const fx1 = Math.max(Math.min(p.x0 + p.w, outW), fx0);
  const fy1 = Math.max(Math.min(p.y0 + p.h, outH), fy0);
  return {
    frame: { x0: fx0, y0: fy0, x1: fx1, y1: fy1 },
    product: { x0: fx0 - p.x0, y0: fy0 - p.y0, x1: fx1 - p.x0, y1: fy1 - p.y0 },
  };
}
