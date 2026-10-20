"use client";

/**
 * 140 px timeline: 0–5 s ruler, scrubbable playhead, numbered keyframe markers (from the compiled
 * keyframes), easing preset, play/pause (Space). Playback steps in whole 24 fps frames.
 */
import { cx, Segmented } from "@oc/ui";
import { Pause, Play } from "lucide-react";
import { useEffect, useRef } from "react";
import type { CameraPathState } from "../hooks/cameraPath";
import { useI18n } from "../i18n";
import { ease } from "../model/camera";
import { keyframeTimeMs, LAST_FRAME_MS } from "../model/sketch";
import { useEditor } from "../state/editorStore";

const DURATION_MS = 5000;
const FRAME_MS = 1000 / 24;
const snap = (ms: number) => Math.min(LAST_FRAME_MS, Math.max(0, Math.round(ms / FRAME_MS) * FRAME_MS));

/** Advances the playhead while `playing` (whole frames, stops on the last frame). */
export function usePlayback() {
  const playing = useEditor((s) => s.playing);
  useEffect(() => {
    if (!playing) return;
    const s = useEditor.getState();
    const start = s.playheadMs >= LAST_FRAME_MS ? 0 : s.playheadMs;
    const t0 = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const ms = start + (now - t0);
      if (ms >= LAST_FRAME_MS) {
        useEditor.setState({ playheadMs: LAST_FRAME_MS, playing: false });
        return;
      }
      const f = Math.floor(ms / FRAME_MS) * FRAME_MS;
      if (f !== useEditor.getState().playheadMs) useEditor.getState().setPlayheadMs(f);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);
}

function EasingCurve({ kind }: { kind: "linear" | "ease_in_out" }) {
  const pts = Array.from({ length: 21 }, (_, i) => `${(i / 20) * 24},${16 - ease(i / 20, kind) * 16}`).join(" ");
  return (
    <svg width={24} height={16} viewBox="0 0 24 16" aria-hidden className="shrink-0">
      <polyline points={pts} fill="none" stroke="#66DCF2" strokeWidth={1.5} />
    </svg>
  );
}

export function Timeline({ cam, compact }: { cam: CameraPathState; compact?: boolean }) {
  const { t } = useI18n();
  const playheadMs = useEditor((s) => s.playheadMs);
  const playing = useEditor((s) => s.playing);
  const easing = useEditor((s) => s.config!.path.easing);
  const tool = useEditor((s) => s.tool);
  const update = useEditor((s) => s.update);
  const track = useRef<HTMLDivElement>(null);
  const keyframes = cam.path?.keyframes ?? [];

  const seekFromX = (clientX: number) => {
    const r = track.current!.getBoundingClientRect();
    const f = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    useEditor.setState({ playing: false });
    useEditor.getState().setPlayheadMs(snap(f * DURATION_MS));
  };
  const x = (ms: number) => `${(ms / DURATION_MS) * 100}%`;
  const frame = Math.round(playheadMs / FRAME_MS);

  return (
    <section data-region="timeline" aria-label={t("panelTimeline")}
      className={cx("flex flex-col gap-2 bg-panel px-3 py-2", compact ? "" : "h-[140px] border-t border-border")}>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={() => useEditor.setState({ playing: !playing })} aria-label={playing ? t("pause") : t("play")}
          title={`${playing ? t("pause") : t("play")} (Space)`}
          className="flex h-11 w-11 items-center justify-center rounded-button border border-border text-ink hover:bg-app md:h-8 md:w-8">
          {playing ? <Pause className="h-4 w-4" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
        </button>
        <span className="tabular text-xs text-muted" aria-live="off">{(playheadMs / 1000).toFixed(2)} s · {frame + 1}/120</span>
        <div className="ml-auto flex items-center gap-2">
          <EasingCurve kind={easing} />
          <div className="w-[220px]">
            <Segmented label={t("easing")} value={easing} onChange={(e) => update((c) => { c.path.easing = e; })}
              options={[{ value: "linear", label: t("easingLinear") }, { value: "ease_in_out", label: t("easingEase") }]} />
          </div>
        </div>
      </div>
      <div className="relative flex-1 select-none px-2">
        {/* keyframe markers (click to jump there) */}
        <div className="relative h-6">
          {keyframes.map((k, i) => (
            <button key={i} type="button" onClick={() => { useEditor.setState({ playing: false }); useEditor.getState().setPlayheadMs(keyframeTimeMs(k.t)); }}
              aria-label={`${t("keyframeN", { n: i + 1 })} · ${(keyframeTimeMs(k.t) / 1000).toFixed(2)} s`} title={t("cpTimeHint")}
              className={cx("absolute top-0 flex h-6 w-6 -translate-x-1/2 items-center justify-center rounded-full border text-[11px] font-semibold",
                tool === "keyframe" ? "border-accent bg-app text-accent" : "border-path/60 bg-stage text-path")}
              style={{ left: x(keyframeTimeMs(k.t)) }}>
              {i + 1}
            </button>
          ))}
        </div>
        {/* ruler + scrub track */}
        <div ref={track} role="slider" tabIndex={0} aria-label={t("playhead")} aria-valuemin={0} aria-valuemax={5} aria-valuenow={Number((playheadMs / 1000).toFixed(2))}
          aria-valuetext={`${(playheadMs / 1000).toFixed(2)} s`}
          onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); seekFromX(e.clientX); }}
          onPointerMove={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) seekFromX(e.clientX); }}
          onKeyDown={(e) => {
            const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
            if (!step) return;
            e.preventDefault();
            useEditor.getState().setPlayheadMs(snap(playheadMs + step * (e.shiftKey ? 24 : 1) * FRAME_MS));
          }}
          className="relative h-9 cursor-ew-resize touch-none rounded-button bg-stage ring-1 ring-border focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
          {Array.from({ length: 11 }, (_, i) => (
            <div key={i} className="absolute bottom-0 border-l border-border" style={{ left: x(i * 500), height: i % 2 ? 8 : 14 }}>
              {i % 2 === 0 && <span className="absolute left-1 top-[-2px] text-[10px] text-muted tabular">{i / 2}s</span>}
            </div>
          ))}
          {keyframes.map((k, i) => <div key={i} className="absolute inset-y-0 border-l border-dashed border-path/50" style={{ left: x(keyframeTimeMs(k.t)) }} />)}
          <div className="pointer-events-none absolute inset-y-[-4px] w-0.5 -translate-x-1/2 bg-ink" style={{ left: x(playheadMs) }} />
        </div>
      </div>
    </section>
  );
}
