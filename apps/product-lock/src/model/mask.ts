import type { Box } from "@oc/contracts";

export const BRUSH_MIN = 2;
export const BRUSH_MAX = 80;

/** 8-bit product mask in normalized-product (source) pixel space. */
export class MaskBuffer {
  readonly data: Uint8ClampedArray;
  constructor(readonly width: number, readonly height: number, data?: Uint8ClampedArray) {
    this.data = data ?? new Uint8ClampedArray(width * height);
  }

  static fromAlpha(width: number, height: number, rgba: Uint8ClampedArray): MaskBuffer {
    const m = new MaskBuffer(width, height);
    for (let i = 0; i < width * height; i++) m.data[i] = rgba[i * 4 + 3];
    return m;
  }

  static fromLuma(width: number, height: number, rgba: Uint8ClampedArray): MaskBuffer {
    const m = new MaskBuffer(width, height);
    for (let i = 0; i < width * height; i++) m.data[i] = rgba[i * 4];
    return m;
  }

  get(x: number, y: number): number {
    return this.data[y * this.width + x];
  }

  clone(): MaskBuffer {
    return new MaskBuffer(this.width, this.height, new Uint8ClampedArray(this.data));
  }

  toRGBA(): Uint8ClampedArray {
    const out = new Uint8ClampedArray(this.width * this.height * 4);
    for (let i = 0; i < this.data.length; i++) {
      const v = this.data[i];
      out[i * 4] = v;
      out[i * 4 + 1] = v;
      out[i * 4 + 2] = v;
      out[i * 4 + 3] = 255;
    }
    return out;
  }
}

export function clampBrush(size: number): number {
  return Math.min(BRUSH_MAX, Math.max(BRUSH_MIN, Math.round(size)));
}

function stamp(m: MaskBuffer, cx: number, cy: number, r: number, value: number): void {
  const r2 = r * r;
  const x0 = Math.max(0, Math.floor(cx - r));
  const x1 = Math.min(m.width - 1, Math.ceil(cx + r));
  const y0 = Math.max(0, Math.floor(cy - r));
  const y1 = Math.min(m.height - 1, Math.ceil(cy + r));
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      const dx = x - cx;
      const dy = y - cy;
      if (dx * dx + dy * dy <= r2) m.data[y * m.width + x] = value;
    }
  }
}

/** Paint (value 255) or erase (0) a round brush of `size` px diameter from (x0,y0) to (x1,y1). */
export function stampLine(m: MaskBuffer, x0: number, y0: number, x1: number, y1: number, size: number, value: number): void {
  const r = clampBrush(size) / 2;
  const dist = Math.hypot(x1 - x0, y1 - y0);
  const steps = Math.max(1, Math.ceil(dist / Math.max(1, r / 2)));
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    stamp(m, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, r, value);
  }
}

export function coverage(m: MaskBuffer): number {
  let n = 0;
  for (const v of m.data) if (v >= 128) n++;
  return n / m.data.length;
}

const r4 = (v: number) => Math.round(v * 1e4) / 1e4;

/** Clip a normalized box to [0,1]². Returns null when nothing remains. */
export function clipBox(b: Box): { box: Box | null; clipped: boolean } {
  const x0 = Math.max(0, b.x);
  const y0 = Math.max(0, b.y);
  const x1 = Math.min(1, b.x + b.w);
  const y1 = Math.min(1, b.y + b.h);
  const clipped = x0 !== b.x || y0 !== b.y || x1 !== b.x + b.w || y1 !== b.y + b.h;
  if (x1 <= x0 || y1 <= y0) return { box: null, clipped: true };
  return { box: { x: r4(x0), y: r4(y0), w: r4(x1 - x0), h: r4(y1 - y0) }, clipped };
}
