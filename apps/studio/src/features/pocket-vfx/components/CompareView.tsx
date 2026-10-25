"use client";

/**
 * Before / after compare (spec §3 화면 C): the work clip (before) and the render (after) stacked in one box and kept at
 * the same timestamp by the shared sync rule (`@oc/web-core` sync: the render leads, the clip mirrors play/pause/rate
 * and is re-seeked past one frame of drift). Split shows before on the left of a draggable divider and after on the
 * right; Before / After show one side. Both videos are muted and the render autoplays (shared §9).
 */
import { cx } from "@oc/ui";
import { Pause, Play } from "lucide-react";
import { useEffect, useId, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { FRAME_S, mirrorFollower, shouldPublishTime } from "@oc/web-core";
import { useI18n } from "../i18n";

export type CompareMode = "split" | "before" | "after";
const MODES: CompareMode[] = ["before", "split", "after"];
const MODE_KEY = { before: "compareBefore", split: "compareSplit", after: "compareAfter" } as const;

export function CompareView({ beforeUrl, afterUrl, size }: { beforeUrl?: string; afterUrl?: string; size: [number, number] }) {
  const { t } = useI18n();
  const box = useRef<HTMLDivElement>(null);
  const before = useRef<HTMLVideoElement>(null);
  const after = useRef<HTMLVideoElement>(null);
  const [mode, setMode] = useState<CompareMode>("split");
  const [split, setSplit] = useState(0.5);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(true);
  /** last leader time: a refreshed signed URL reloads the video, which then continues from here */
  const lastT = useRef(0);
  const seekId = useId();
  const splitId = useId();

  // every animation frame: the clip follows the render; React time is published ~10 Hz while playing
  useEffect(() => {
    let raf = 0;
    let at = 0;
    let shown = -1;
    let wasPlaying: boolean | null = null;
    const tick = () => {
      const l = after.current;
      const f = before.current;
      if (l && l.readyState >= 1) {
        if (f) mirrorFollower(l, f);
        lastT.current = l.currentTime;
        const now = performance.now();
        if (shouldPublishTime(at, now, l.paused, Math.abs(shown - l.currentTime) > 1e-3)) {
          at = now;
          shown = l.currentTime;
          setTime(l.currentTime);
        }
        if (wasPlaying !== !l.paused) {
          wasPlaying = !l.paused;
          setPlaying(!l.paused);
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const toggle = () => {
    const l = after.current;
    if (!l) return;
    if (l.paused) void l.play().catch(() => {});
    else l.pause();
  };
  const seek = (s: number) => {
    const l = after.current;
    if (!l) return;
    l.currentTime = s;
    setTime(s);
  };
  const dragTo = (e: ReactPointerEvent<HTMLDivElement>) => {
    const r = box.current?.getBoundingClientRect();
    if (!r || r.width <= 0) return;
    setSplit(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)));
  };
  // after covers the right of the divider; Before / After show one video
  const clip = mode === "before" ? "inset(0 0 0 100%)" : mode === "after" ? "none" : `inset(0 0 0 ${(split * 100).toFixed(2)}%)`;
  const portrait = size[1] > size[0];
  const dur = duration > 0 ? duration : 0;

  return (
    <div className="flex flex-col gap-2" data-region="compare">
      <div role="radiogroup" aria-label={t("compareMode")} className="flex self-start rounded-button border border-border bg-app p-0.5">
        {MODES.map((m) => (
          <button key={m} type="button" role="radio" aria-checked={mode === m} onClick={() => setMode(m)}
            className={cx("min-h-[44px] min-w-[64px] rounded-[6px] px-3 text-[13px] md:min-h-8",
              mode === m ? "bg-panel font-medium text-ink" : "text-muted hover:text-ink")}>
            {t(MODE_KEY[m])}
          </button>
        ))}
      </div>
      <div ref={box} data-mode={mode}
        className={cx("relative mx-auto w-full touch-none select-none overflow-hidden rounded-panel border border-border bg-stage",
          portrait && "max-w-[min(100%,calc(70vh*9/16))]")}
        style={{ aspectRatio: `${size[0]} / ${size[1]}` }}
        onPointerDown={(e) => {
          if (mode !== "split") return;
          e.currentTarget.setPointerCapture(e.pointerId);
          dragTo(e);
        }}
        onPointerMove={(e) => { if (mode === "split" && e.currentTarget.hasPointerCapture(e.pointerId)) dragTo(e); }}>
        {beforeUrl && (
          <video ref={before} src={beforeUrl} muted playsInline loop preload="auto" aria-label={t("compareBefore")}
            className="absolute inset-0 h-full w-full object-contain" />
        )}
        {afterUrl ? (
          <video ref={after} src={afterUrl} muted playsInline loop autoPlay preload="auto" aria-label={t("compareAfter")}
            onLoadedMetadata={(e) => {
              const v = e.currentTarget;
              setDuration(v.duration);
              if (Math.abs(v.currentTime - lastT.current) > FRAME_S) v.currentTime = Math.min(lastT.current, v.duration || lastT.current);
            }}
            className="absolute inset-0 h-full w-full object-contain" style={{ clipPath: clip }} />
        ) : <p className="absolute inset-0 flex items-center justify-center text-xs text-muted">{t("compareLoading")}</p>}
        {mode === "split" && (
          <div aria-hidden className="pointer-events-none absolute inset-y-0 w-0.5 -translate-x-1/2 bg-ink/90 shadow"
            style={{ left: `${split * 100}%` }}>
            <span className="absolute left-1/2 top-1/2 h-8 w-8 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-ink bg-stage/70" />
          </div>
        )}
        {mode !== "after" && (
          <span className="pointer-events-none absolute left-2 top-2 rounded bg-stage/75 px-1.5 py-0.5 text-[11px] text-ink">{t("compareBefore")}</span>
        )}
        {mode !== "before" && (
          <span className="pointer-events-none absolute right-2 top-2 rounded bg-stage/75 px-1.5 py-0.5 text-[11px] text-effect">{t("compareAfter")}</span>
        )}
      </div>
      {mode === "split" && (
        <div className="flex items-center gap-2">
          <label htmlFor={splitId} className="shrink-0 text-xs text-muted">{t("splitPosition")}</label>
          <input id={splitId} type="range" min={0} max={100} step={1} value={Math.round(split * 100)}
            onChange={(e) => setSplit(Number(e.target.value) / 100)} className="h-[44px] min-w-0 flex-1 accent-[#DD8BFF] md:h-6" />
        </div>
      )}
      <div className="flex items-center gap-3 rounded-panel border border-border bg-panel px-2 py-1" data-region="transport">
        <button type="button" onClick={toggle} aria-label={playing ? t("pause") : t("playResult")}
          className="flex h-[44px] w-[44px] shrink-0 items-center justify-center rounded-button text-ink hover:bg-app">
          {playing ? <Pause className="h-4 w-4" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
        </button>
        <label htmlFor={seekId} className="sr-only">{t("seek")}</label>
        <input id={seekId} type="range" min={0} max={dur || 1} step={FRAME_S} value={Math.min(time, dur || 1)} disabled={!dur}
          onChange={(e) => seek(Number(e.target.value))} className="h-[44px] min-w-0 flex-1 accent-[#FF9A52]" />
        <span className="tabular shrink-0 text-xs text-muted">{time.toFixed(2)} / {dur.toFixed(2)} s</span>
      </div>
    </div>
  );
}
