"use client";

/**
 * 160 px timeline: the work filmstrip with the playhead, effect start / peak / end handles (frame-snapped, kept in
 * order by the store), the tracking confidence strip (green -> warning by confidence, red where invalid; stale tracks
 * fade) and the invalid intervals as buttons that jump to their first frame. Space plays; arrows step frames.
 */
import type { TrackJson } from "@oc/contracts";
import { cx } from "@oc/ui";
import { Pause, Play } from "lucide-react";
import { useRef } from "react";
import { useAssetUrl } from "../hooks/assets";
import { useI18n } from "../i18n";
import { FPS, effectWindow } from "../model/timeline";
import { assetById, useEditor, type TimingHandle } from "../state/editorStore";

const pct = (f: number, n: number) => `${((f + 0.5) / n) * 100}%`;
const mix = (a: [number, number, number], b: [number, number, number], k: number) =>
  `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * k)).join(",")})`;
const WARNING: [number, number, number] = [0xf2, 0xc8, 0x6b];
const GREEN: [number, number, number] = [0x69, 0xd6, 0xa5];

/** Strip colour of one track point: red when invalid, warning -> green by confidence (a heuristic, not a probability). */
export function confidenceColor(p: TrackJson["points"][number] | undefined): string {
  return !p ? "transparent" : !p.valid ? "#FF6B6B" : mix(WARNING, GREEN, Math.min(Math.max(p.confidence, 0), 1));
}

export function Timeline({ n, filmstripId, track, trackFresh, compact }: {
  n: number;
  filmstripId: string | undefined;
  track: TrackJson | null;
  trackFresh: boolean;
  compact?: boolean;
}) {
  const { t } = useI18n();
  const frame = useEditor((s) => s.frame);
  const playing = useEditor((s) => s.playing);
  const effect = useEditor((s) => s.config!.effect);
  const duration = useEditor((s) => s.config!.trim.duration_ms);
  const project = useEditor((s) => s.project);
  const stripUrl = useAssetUrl(assetById(project, filmstripId));
  const bar = useRef<HTMLDivElement>(null);
  const w = effectWindow(effect, duration);

  const frameAt = (clientX: number) => {
    const r = bar.current!.getBoundingClientRect();
    return Math.floor(((clientX - r.left) / r.width) * n);
  };
  const seek = (f: number) => {
    const s = useEditor.getState();
    s.setPlaying(false);
    s.setFrame(f);
  };
  const step = (e: React.KeyboardEvent, value: number, set: (f: number) => void) => {
    const d = e.key === "ArrowRight" || e.key === "ArrowUp" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -1 : 0;
    if (d) set(value + d * (e.shiftKey ? FPS : 1));
    else if (e.key === "Home") set(0);
    else if (e.key === "End") set(n - 1);
    else return;
    e.preventDefault();
  };

  const handle = (h: TimingHandle, f: number) => (
    <div key={h} role="slider" tabIndex={0} data-handle={h}
      aria-label={t(`handle_${h}`)} aria-valuemin={0} aria-valuemax={n - 1} aria-valuenow={f} aria-valuetext={t("frameOf", { frame: f + 1, total: n })}
      onPointerDown={(e) => { e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId); }}
      onPointerMove={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) useEditor.getState().dragTiming(h, frameAt(e.clientX), `drag:${h}`); }}
      onKeyDown={(e) => {
        e.stopPropagation(); // the playhead underneath must not also step
        step(e, f, (v) => useEditor.getState().dragTiming(h, v, `key:${h}`));
      }}
      title={t(`handle_${h}`)}
      className={cx("absolute top-0 z-10 flex h-full w-[44px] -translate-x-1/2 cursor-ew-resize touch-none items-start justify-center md:w-6",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent")}
      style={{ left: pct(f, n) }}>
      {h === "peak" ? (
        <span className="mt-[-6px] h-3 w-3 rotate-45 border border-app bg-effect shadow" aria-hidden />
      ) : (
        <span className="h-full w-1.5 rounded-full bg-effect shadow" aria-hidden />
      )}
    </div>
  );

  const points = track?.points ?? [];
  const invalid = track?.invalid_intervals ?? [];
  return (
    <section data-region="timeline" aria-label={t("timeline")}
      className={cx("flex shrink-0 flex-col gap-1.5 border-t border-border bg-panel px-3 py-2", compact ? "" : "h-[160px]")}>
      <div className="flex items-center gap-3">
        <button type="button" onClick={() => useEditor.getState().setPlaying(!playing)} aria-label={playing ? t("pause") : t("playClip")}
          title={`${playing ? t("pause") : t("playClip")} (Space)`}
          className="flex h-[44px] w-[44px] shrink-0 items-center justify-center rounded-button border border-border text-ink hover:bg-app md:h-8 md:w-8">
          {playing ? <Pause className="h-4 w-4" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
        </button>
        <span className="tabular text-xs text-muted" aria-live="off">
          {t("frameOf", { frame: frame + 1, total: n })} · {(frame / FPS).toFixed(2)} {t("seconds")}
        </span>
        <span className="ml-auto hidden items-center gap-3 text-[11px] text-muted sm:flex tabular">
          <span>{t("handle_start")} {w.start + 1}</span>
          <span className="text-effect">{t("handle_peak")} {w.peak + 1}</span>
          <span>{t("handle_end")} {w.end + 1}</span>
        </span>
      </div>

      {/* filmstrip + effect window + playhead */}
      <div ref={bar} role="slider" tabIndex={0} aria-label={t("playhead")} aria-valuemin={1} aria-valuemax={n} aria-valuenow={frame + 1}
        aria-valuetext={t("frameOf", { frame: frame + 1, total: n })}
        onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); seek(frameAt(e.clientX)); }}
        onPointerMove={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) seek(frameAt(e.clientX)); }}
        onKeyDown={(e) => {
          if (e.key === " ") {
            e.preventDefault();
            if (!e.repeat) useEditor.getState().setPlaying(!playing);
            return;
          }
          step(e, frame, seek);
        }}
        className="relative h-14 shrink-0 cursor-ew-resize touch-none select-none overflow-visible rounded-button bg-stage ring-1 ring-border md:h-12"
        data-region="filmstrip">
        {stripUrl && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={stripUrl} alt="" draggable={false} className="pointer-events-none absolute inset-0 h-full w-full rounded-button object-fill opacity-80" />
        )}
        <div className="pointer-events-none absolute inset-y-0 border-y-2 border-effect/70 bg-effect/15"
          style={{ left: `${(w.start / n) * 100}%`, width: `${((w.end - w.start + 1) / n) * 100}%` }} />
        {handle("start", w.start)}
        {handle("end", w.end)}
        {handle("peak", w.peak)}
        <div className="pointer-events-none absolute inset-y-[-4px] z-20 w-0.5 -translate-x-1/2 bg-ink shadow" style={{ left: pct(frame, n) }} />
      </div>

      {/* tracking confidence (a heuristic, never shown as a probability) */}
      <div className={cx("relative h-4 shrink-0", !trackFresh && "opacity-40")} data-region="confidence"
        aria-label={track ? (trackFresh ? t("confidence") : t("confidenceStale")) : t("noTrackYet")}>
        {track ? (
          <div className="absolute inset-x-0 top-1 flex h-2 overflow-hidden rounded-full bg-app">
            {Array.from({ length: n }, (_, f) => {
              const p = points[f]?.frame === f ? points[f] : points.find((q) => q.frame === f);
              const color = confidenceColor(p);
              return <span key={f} className="h-full flex-1" style={{ background: color }} />;
            })}
          </div>
        ) : (
          <p className="text-[11px] leading-4 text-muted">{t("noTrackYet")}</p>
        )}
        {invalid.map(([a, b]) => (
          <button key={`${a}-${b}`} type="button" onClick={() => seek(a)} data-invalid={`${a}-${b}`}
            aria-label={t("trackLostAt", { first: a + 1, last: b + 1 })} title={t("trackLostAt", { first: a + 1, last: b + 1 })}
            className="absolute top-0 h-4 min-w-[12px] rounded-sm bg-[#FF6B6B]/80 before:absolute before:-inset-y-[14px] before:inset-x-0 before:content-[''] hover:bg-[#FF6B6B]"
            style={{ left: `${(a / n) * 100}%`, width: `${((b - a + 1) / n) * 100}%` }} />
        ))}
      </div>

      <div className="relative h-3 shrink-0 text-[10px] text-muted tabular" aria-hidden>
        {Array.from({ length: Math.floor((n - 1) / FPS) + 1 }, (_, sIdx) => (
          <span key={sIdx} className="absolute -translate-x-1/2" style={{ left: pct(sIdx * FPS, n) }}>{sIdx} s</span>
        ))}
      </div>
    </section>
  );
}
