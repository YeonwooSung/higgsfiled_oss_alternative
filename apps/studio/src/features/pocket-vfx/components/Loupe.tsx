"use client";

/**
 * Touch loupe: a 2x magnified circle of the stage drawn above the finger, so the finger never hides the spot being
 * placed (spec §3 mobile). Reads the composite canvas every animation frame while shown.
 */
import { useEffect, useRef } from "react";

const SIZE = 112;
const GAP = 28;

export function Loupe({ source, uv, at, box }: {
  source: HTMLCanvasElement | null;
  /** point under the finger, uv01 of the frame */
  uv: [number, number];
  /** finger position in CSS px of the stage box */
  at: [number, number];
  /** stage box size in CSS px (the frame as displayed) */
  box: [number, number];
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const props = useRef({ source, uv, box });
  props.current = { source, uv, box };

  useEffect(() => {
    let raf = 0;
    const draw = () => {
      const c = ref.current;
      const { source: src, uv: [u, v], box: [bw] } = props.current;
      if (c && src) {
        const dpr = window.devicePixelRatio || 1;
        if (c.width !== SIZE * dpr) c.width = c.height = SIZE * dpr;
        const x = c.getContext("2d")!;
        // 2x of the displayed size: SIZE CSS px show SIZE / 2 displayed px = this many source px
        const span = (SIZE / 2) * (src.width / bw);
        x.fillStyle = "#080809";
        x.fillRect(0, 0, c.width, c.height);
        x.drawImage(src, u * src.width - span / 2, v * src.height - span / 2, span, span, 0, 0, c.width, c.height);
        const m = c.width / 2;
        x.strokeStyle = "#DD8BFF";
        x.lineWidth = 1.5 * dpr;
        x.beginPath();
        x.moveTo(m - 10 * dpr, m);
        x.lineTo(m - 3 * dpr, m);
        x.moveTo(m + 3 * dpr, m);
        x.lineTo(m + 10 * dpr, m);
        x.moveTo(m, m - 10 * dpr);
        x.lineTo(m, m - 3 * dpr);
        x.moveTo(m, m + 3 * dpr);
        x.lineTo(m, m + 10 * dpr);
        x.stroke();
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  const [fx, fy] = at;
  const above = fy - SIZE - GAP >= 0;
  const left = Math.min(Math.max(fx - SIZE / 2, 0), Math.max(box[0] - SIZE, 0));
  return (
    <canvas ref={ref} aria-hidden data-region="loupe"
      className="pointer-events-none absolute z-20 rounded-full border-2 border-effect shadow-lg"
      style={{ width: SIZE, height: SIZE, left, top: above ? fy - SIZE - GAP : fy + GAP }} />
  );
}
