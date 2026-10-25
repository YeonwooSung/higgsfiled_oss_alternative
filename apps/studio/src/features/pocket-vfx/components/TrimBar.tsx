"use client";

import type { VfxTrim } from "@oc/contracts";
import { cx } from "@oc/ui";
import { useRef, type KeyboardEvent, type PointerEvent, type RefObject } from "react";
import { FPS } from "../model/timeline";
import { moveTrim, setTrimEnd, setTrimStart } from "../model/trim";

type Drag = { kind: "start" | "end" | "move"; offsetMs: number };

const FRAME_MS = 1000 / FPS;
const pct = (ms: number, total: number) => `${(100 * ms) / total}%`;

/**
 * Filmstrip of the whole source with the selected trim range. Handles (44 px touch targets) and the range drag clamp
 * to 1-5 s inside the source; a click outside the range only scrubs the preview. Handles are keyboard sliders
 * (arrows = 1 frame, Shift = 1 s, Home/End).
 */
export function TrimBar({ sourceMs, trim, onChange, onScrub, filmstripUrl, thumbs, disabled, playheadRef, labels }: {
  sourceMs: number;
  trim: VfxTrim;
  onChange: (trim: VfxTrim) => void;
  onScrub: (ms: number) => void;
  filmstripUrl?: string;
  thumbs: number;
  disabled?: boolean;
  playheadRef: RefObject<HTMLDivElement | null>;
  labels: { bar: string; start: string; end: string; startValue: string; endValue: string };
}) {
  const bar = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const start = trim.source_start_ms;
  const end = start + trim.duration_ms;

  const msAt = (clientX: number) => {
    const r = bar.current!.getBoundingClientRect();
    return Math.min(Math.max((clientX - r.left) / r.width, 0), 1) * sourceMs;
  };
  const apply = (kind: Drag["kind"], ms: number) => {
    const next = kind === "start" ? setTrimStart(trim, ms, sourceMs) : kind === "end" ? setTrimEnd(trim, ms, sourceMs) : moveTrim(trim, ms, sourceMs);
    onChange(next);
    onScrub(kind === "end" ? next.source_start_ms + next.duration_ms : next.source_start_ms);
  };
  const down = (kind: Drag["kind"]) => (e: PointerEvent<HTMLElement>) => {
    if (disabled) return;
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { kind, offsetMs: kind === "move" ? msAt(e.clientX) - start : 0 };
  };
  const move = (e: PointerEvent<HTMLElement>) => {
    const d = drag.current;
    if (d) apply(d.kind, msAt(e.clientX) - d.offsetMs);
  };
  const up = () => { drag.current = null; };
  const key = (kind: "start" | "end") => (e: KeyboardEvent<HTMLElement>) => {
    if (disabled) return;
    const at = kind === "start" ? start : end;
    const step = e.shiftKey ? 1000 : FRAME_MS;
    const to = { ArrowLeft: at - step, ArrowDown: at - step, ArrowRight: at + step, ArrowUp: at + step, Home: 0, End: sourceMs }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    apply(kind, to);
  };
  const handle = (kind: "start" | "end", at: number, label: string, value: string) => (
    <div role="slider" tabIndex={disabled ? -1 : 0} aria-label={label} aria-valuemin={0} aria-valuemax={Math.round(sourceMs)}
      aria-valuenow={Math.round(at)} aria-valuetext={value} aria-disabled={disabled || undefined}
      onPointerDown={down(kind)} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onKeyDown={key(kind)}
      className={cx("group absolute inset-y-0 z-20 flex w-[44px] -translate-x-1/2 touch-none items-center justify-center outline-none",
        disabled ? "cursor-not-allowed" : "cursor-ew-resize")}
      style={{ left: pct(at, sourceMs) }}>
      <span className={cx("flex h-full w-3 items-center justify-center rounded-[4px] bg-accent transition-transform duration-150",
        "group-focus-visible:ring-2 group-focus-visible:ring-ink group-hover:scale-x-125", disabled && "bg-muted")}>
        <span className="h-5 w-0.5 rounded bg-app/70" aria-hidden />
      </span>
    </div>
  );

  return (
    <div className="px-5">
      <div ref={bar} aria-label={labels.bar} role="group" onPointerDown={(e) => { if (!disabled) onScrub(msAt(e.clientX)); }}
        className="relative h-16 touch-none select-none rounded-card bg-panel">
        <div className="absolute inset-0 flex overflow-hidden rounded-card" aria-hidden>
          {filmstripUrl && Array.from({ length: thumbs }, (_, k) => (
            <div key={k} className="relative h-full flex-1 overflow-hidden">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              {/* the strip scaled so one thumbnail spans one tile; tile k shows thumbnail k, vertically centered */}
              <img src={filmstripUrl} alt="" draggable={false} className="absolute left-0 top-1/2 h-auto max-w-none"
                style={{ width: `${thumbs * 100}%`, transform: `translate(-${(100 * k) / thumbs}%, -50%)` }} />
            </div>
          ))}
        </div>
        <div className="absolute inset-y-0 left-0 rounded-l-card bg-black/65" style={{ width: pct(start, sourceMs) }} aria-hidden />
        <div className="absolute inset-y-0 right-0 rounded-r-card bg-black/65" style={{ width: pct(sourceMs - end, sourceMs) }} aria-hidden />
        <div aria-hidden onPointerDown={down("move")} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
          className={cx("absolute inset-y-0 z-10 border-y-2 border-accent", disabled ? "cursor-not-allowed" : "cursor-grab active:cursor-grabbing")}
          style={{ left: pct(start, sourceMs), width: pct(end - start, sourceMs) }} />
        <div ref={playheadRef} aria-hidden className="pointer-events-none absolute -inset-y-1 z-30 w-0.5 -translate-x-1/2 rounded bg-ink shadow"
          style={{ left: pct(start, sourceMs) }} />
        {handle("start", start, labels.start, labels.startValue)}
        {handle("end", end, labels.end, labels.endValue)}
      </div>
    </div>
  );
}
