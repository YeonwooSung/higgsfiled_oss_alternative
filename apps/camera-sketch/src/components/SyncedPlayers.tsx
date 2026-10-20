"use client";

/**
 * Result players kept at the same timestamp (spec §4): the first visible video leads; every other
 * video mirrors its play/pause/rate and is re-seeked when it drifts more than one frame (1/24 s,
 * `model/sync.ts`). One shared transport drives the leader. All videos are muted and do not
 * autoplay. The optional path overlay (intended target trajectory) is drawn over output-space
 * videos only and never reaches an export.
 */
import { cx } from "@oc/ui";
import { Pause, Play } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useI18n } from "../i18n";
import type { TrajectoryPoint } from "../model/results";
import { FRAME_S, shouldPublishTime, syncFollower } from "../model/sync";

export interface PlayerSource {
  id: string;
  label: string;
  kind: "video" | "image";
  url?: string;
  /** Badges / notes rendered with the player. */
  header?: ReactNode;
  footer?: ReactNode;
  /** Draw the path overlay on this player (output-space videos only). */
  overlay?: boolean;
}

const RATES = [0.25, 0.5, 1] as const;
const DURATION_S = 5;

function PathOverlay({ trajectory, timeS }: { trajectory: TrajectoryPoint[]; timeS: number }) {
  const pts = trajectory.filter((p) => p.uv) as { tMs: number; uv: [number, number] }[];
  if (pts.length < 1) return null;
  const now = pts.reduce((best, p) => (Math.abs(p.tMs / 1000 - timeS) < Math.abs(best.tMs / 1000 - timeS) ? p : best), pts[0]);
  return (
    <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden data-region="path-overlay">
      <polyline points={pts.map((p) => `${p.uv[0]},${p.uv[1]}`).join(" ")} fill="none" stroke="#66DCF2" strokeWidth={2}
        vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeDasharray="6 4" />
      <circle cx={pts[0].uv[0]} cy={pts[0].uv[1]} r={0.006} fill="#66DCF2" />
      <circle cx={now.uv[0]} cy={now.uv[1]} r={0.012} fill="none" stroke="#F0BA65" strokeWidth={2} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function SyncedPlayers({ sources, visibleIds, columns = 1, aspect, trajectory, showOverlay }: {
  sources: PlayerSource[];
  visibleIds: string[];
  columns?: 1 | 2;
  /** Output size [w, h]: each player box has this aspect so the overlay lines up with the video. */
  aspect: [number, number];
  trajectory: TrajectoryPoint[] | null;
  showOverlay: boolean;
}) {
  const { t } = useI18n();
  const refs = useRef(new Map<string, HTMLVideoElement>());
  const timeRef = useRef(0);
  const playingRef = useRef(false);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState<(typeof RATES)[number]>(1);
  const seekId = useId();
  const videos = sources.filter((s) => s.kind === "video" && s.url);
  const leaderId = (videos.find((s) => visibleIds.includes(s.id)) ?? videos[0])?.id ?? null;
  const leader = useCallback(() => (leaderId ? refs.current.get(leaderId) ?? null : null), [leaderId]);
  const visibleRef = useRef(new Set(visibleIds));
  visibleRef.current = new Set(visibleIds);

  // Sync loop: every animation frame, visible followers mirror the leader (seek only past one frame of
  // drift); hidden ones are paused and re-synced when shown. React state (time display, overlay) is
  // only published ~10 Hz while playing, so the tree does not re-render every frame.
  useEffect(() => {
    let raf = 0;
    let publishedAt = 0;
    let publishedT = -1;
    let publishedPlaying: boolean | null = null;
    const tick = () => {
      const l = leader();
      if (l && l.readyState >= 1) {
        for (const [id, v] of refs.current) if (id !== leaderId) syncFollower(l, v, visibleRef.current.has(id));
        timeRef.current = l.currentTime;
        playingRef.current = !l.paused;
        const now = performance.now();
        if (shouldPublishTime(publishedAt, now, l.paused, Math.abs(publishedT - l.currentTime) > 1e-3)) {
          publishedAt = now;
          publishedT = l.currentTime;
          setTime(l.currentTime);
        }
        if (publishedPlaying !== !l.paused) {
          publishedPlaying = !l.paused;
          setPlaying(!l.paused);
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [leader, leaderId]);

  // A new leader (tab switch) continues from the shared time and play state.
  useEffect(() => {
    const l = leader();
    if (!l) return;
    const take = () => {
      if (Math.abs(l.currentTime - timeRef.current) > FRAME_S) l.currentTime = Math.min(timeRef.current, Number.isFinite(l.duration) ? l.duration : timeRef.current);
      l.playbackRate = rate;
      if (playingRef.current && l.paused) void l.play().catch(() => {});
    };
    if (l.readyState >= 1) take();
    else l.addEventListener("loadedmetadata", take, { once: true });
    // other videos stop leading: the loop makes them follow
    return () => l.removeEventListener("loadedmetadata", take);
  }, [leader]); // eslint-disable-line react-hooks/exhaustive-deps

  const setRef = (id: string) => (el: HTMLVideoElement | null) => {
    if (el) refs.current.set(id, el);
    else refs.current.delete(id);
  };
  // A refreshed signed URL reloads the element; keep the shared time when that happens.
  const onLoaded = (id: string) => () => {
    const v = refs.current.get(id);
    if (!v) return;
    if (id === leaderId && Math.abs(v.currentTime - timeRef.current) > FRAME_S) v.currentTime = Math.min(timeRef.current, v.duration || timeRef.current);
    v.playbackRate = rate;
    if (id === leaderId && playingRef.current && v.paused) void v.play().catch(() => {});
  };

  const toggle = () => {
    const l = leader();
    if (!l) return;
    if (l.paused) void l.play().catch(() => {});
    else l.pause();
  };
  const seek = (s: number) => {
    const l = leader();
    if (!l) return;
    l.currentTime = s;
    timeRef.current = s;
    setTime(s);
  };
  const changeRate = (r: (typeof RATES)[number]) => {
    setRate(r);
    const l = leader();
    if (l) l.playbackRate = r;
  };
  const duration = (() => {
    const l = leader();
    return l && Number.isFinite(l.duration) && l.duration > 0 ? l.duration : DURATION_S;
  })();
  const portrait = aspect[1] > aspect[0];

  return (
    <div className="space-y-3" data-region="synced-players">
      <div className={cx("grid gap-3", columns === 2 ? "md:grid-cols-2" : "grid-cols-1")}>
        {sources.map((s) => {
          const visible = visibleIds.includes(s.id);
          return (
            <figure key={s.id} className={cx("min-w-0 space-y-1.5", !visible && "hidden")} data-player={s.id}>
              <figcaption className="flex flex-wrap items-center gap-2 text-xs font-medium text-muted">
                <span className="text-ink">{s.label}</span>
                {s.header}
              </figcaption>
              <div className={cx("relative mx-auto overflow-hidden rounded-panel border border-border bg-stage",
                portrait ? "max-h-[70vh] w-full max-w-[min(100%,calc(70vh*9/16))]" : "w-full")}
                style={{ aspectRatio: `${aspect[0]} / ${aspect[1]}` }}>
                {s.kind === "video" ? (
                  s.url ? (
                    <video ref={setRef(s.id)} src={s.url} muted playsInline loop preload="auto" onLoadedMetadata={onLoaded(s.id)}
                      aria-label={s.label} className="absolute inset-0 h-full w-full object-contain" />
                  ) : <p className="absolute inset-0 flex items-center justify-center text-xs text-muted">…</p>
                ) : s.url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={s.url} alt={s.label} className="absolute inset-0 h-full w-full object-contain" />
                ) : null}
                {showOverlay && s.overlay && trajectory && <PathOverlay trajectory={trajectory} timeS={time} />}
              </div>
              {s.footer}
            </figure>
          );
        })}
      </div>
      {videos.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-panel border border-border bg-panel px-3 py-2" data-region="transport">
          <button type="button" onClick={toggle} aria-label={playing ? t("pause") : t("play")}
            className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-button text-ink hover:bg-app md:min-h-8 md:min-w-8">
            {playing ? <Pause className="h-4 w-4" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
          </button>
          <label htmlFor={seekId} className="sr-only">{t("seek")}</label>
          <input id={seekId} type="range" min={0} max={duration} step={FRAME_S} value={Math.min(time, duration)}
            onChange={(e) => seek(Number(e.target.value))} className="min-w-[120px] flex-1 accent-[#66DCF2]" />
          <span className="tabular text-xs text-muted">{time.toFixed(2)} s / {duration.toFixed(2)} s</span>
          <div role="radiogroup" aria-label={t("playbackRate")} className="flex rounded-button border border-border">
            {RATES.map((r) => (
              <button key={r} type="button" role="radio" aria-checked={rate === r} onClick={() => changeRate(r)}
                className={cx("min-h-[44px] px-2 text-xs md:min-h-7", rate === r ? "bg-app font-semibold text-ink" : "text-muted")}>
                {r}×
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
