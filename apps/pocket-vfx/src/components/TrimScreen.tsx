"use client";

import { TERMINAL_JOB_STATES, type Aspect, type Asset, type Job, type PocketVfxProject, type VfxCameraMotionStatus, type VfxTrim } from "@oc/contracts";
import { Badge, Button, cx, Segmented } from "@oc/ui";
import { Check, CircleAlert, Clapperboard, Loader2, Pause, Play, Volume2, VolumeX } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { applyClipSettings, clipDurationMs, clipPhase, clipSource, clipToLink, derivedAsset, latestJob, linkClip, workToLink } from "../flow/clip";
import { useAssetUrl } from "../hooks/assets";
import { mergeJobs, useConfigSaver, useJobs, useProject, type SaveState } from "../hooks/project";
import { useI18n } from "../i18n";
import type { Dict } from "../i18n/en";
import { setTrimDuration, moveTrim } from "../model/trim";
import { errorText } from "./errors";
import { TopBar } from "./TopBar";
import { TrimBar } from "./TrimBar";

type T = (key: keyof Dict, vars?: Record<string, string | number>) => string;
const ANALYZE_STAGES = ["normalize", "proxy", "filmstrip", "audio", "motion"] as const;
const secs = (ms: number) => (ms / 1000).toFixed(2);

interface Settings {
  trim: VfxTrim;
  aspect: Aspect;
}
const sameSettings = (a: Settings, b: Settings) =>
  a.aspect === b.aspect && a.trim.source_start_ms === b.trim.source_start_ms && a.trim.duration_ms === b.trim.duration_ms;

/**
 * Screen A, step 2 (spec §3): after `asset.inspect`, the whole-clip preview (rotation applied) with a filmstrip trim
 * range of 1-5 s, the output aspect (default: the clip's orientation) and Keep original sound (on; disabled for a
 * silent clip). "Prepare clip" saves trim + aspect and runs `vfx.analyze` (no generation); its work clip is then
 * PATCHed into the config, and the prepared first frame + filmstrip are shown.
 */
export function TrimScreen({ projectId }: { projectId: string }) {
  const { t } = useI18n();
  const query = useProject(projectId);
  const project = query.data;
  const { save, state: saveState } = useConfigSaver(projectId);
  const { jobs, follow } = useJobs(projectId);
  const phase = project ? clipPhase(project.assets) : "none";

  // Poll while the upload is being inspected (asset.inspect makes the preview and filmstrip).
  const refetch = query.refetch;
  useEffect(() => {
    if (phase !== "uploading" && phase !== "inspecting") return;
    const id = setInterval(() => void refetch(), 1500);
    return () => clearInterval(id);
  }, [phase, refetch]);

  // Link the inspected clip once: default trim, aspect from the displayed orientation.
  const clipId = project ? clipToLink(project.assets, project.config) : null;
  useEffect(() => {
    if (!clipId) return;
    void save((c, p) => {
      const clip = p.assets.find((a) => a.id === clipId);
      return clip && clipToLink(p.assets, c) === clipId ? linkClip(c, clip) : c;
    }).catch(() => {});
  }, [clipId, save]);

  const allJobs = project ? mergeJobs(project.recent_jobs, jobs) : [];
  const analyze = latestJob(allJobs, "vfx.analyze");
  const analyzeRunning = !!analyze && !TERMINAL_JOB_STATES.includes(analyze.state);
  // Resume following an analysis that was running when the page loaded.
  useEffect(() => {
    if (analyze && analyzeRunning) follow(analyze);
  }, [analyze?.id, analyzeRunning]); // eslint-disable-line react-hooks/exhaustive-deps

  // The executor never writes the config: link the work clip of a succeeded analysis while it still matches.
  const workId = project ? workToLink(project.assets, project.config, analyze) : null;
  const linked = useRef(new Set<string>());
  useEffect(() => {
    if (!workId || !analyze || linked.current.has(workId)) return;
    linked.current.add(workId);
    void save((c, p) => (workToLink(p.assets, c, analyze) === workId ? { ...c, work_asset_id: workId } : c)).catch(() => {});
  }, [workId, analyze, save]);

  const status = saveStatus(saveState, t);
  if (query.isError) return <Shell status={status}><Message icon={<CircleAlert className="h-6 w-6 text-danger" aria-hidden />} title={t("projectError")} action /></Shell>;
  if (!project) return <Shell status={status}><Message icon={<Loader2 className="h-6 w-6 animate-spin text-muted" aria-hidden />} title={t("loadingProject")} /></Shell>;
  const shell = (body: ReactNode) => <Shell project={project} status={status}>{body}</Shell>;
  const clip = project.assets.find((a) => a.id === project.config.video_asset_id);

  if (phase === "rejected") {
    const rejected = [...project.assets].reverse().find((a) => a.role === "clip" && a.state === "rejected");
    const code = `reject_${rejected?.error_code ?? "unknown"}` as keyof Dict;
    return shell(<Message icon={<CircleAlert className="h-6 w-6 text-danger" aria-hidden />} title={t("rejectedTitle")}
      hint={t(code in REJECT_KEYS ? code : "reject_unknown")} action />);
  }
  if (phase !== "ready" || !clip) {
    return shell(<Message icon={<Loader2 className="h-6 w-6 animate-spin text-accent" aria-hidden />} title={t("inspecting")} hint={t("inspectingHint")} />);
  }
  const sourceMs = clipDurationMs(clip) ?? 0;
  if (sourceMs < 1000) return shell(<Message icon={<CircleAlert className="h-6 w-6 text-danger" aria-hidden />} title={t("rejectedTitle")} hint={t("clipTooShort")} action />);
  return shell(<TrimEditor project={project} clip={clip} sourceMs={sourceMs} analyze={analyze} save={save} follow={follow} />);
}

const REJECT_KEYS = { reject_unsupported_codec: 1, reject_multiple_video_streams: 1, reject_too_large_dims: 1, reject_too_long: 1 };

function saveStatus(state: SaveState, t: T): ReactNode {
  if (state === "saving") return <><Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />{t("saving")}</>;
  if (state === "error") return <><CircleAlert className="h-3.5 w-3.5 text-danger" aria-hidden />{t("saveFailed")}</>;
  if (state === "conflict") return <><CircleAlert className="h-3.5 w-3.5 text-warning" aria-hidden />{t("conflict")}</>;
  return null;
}

function Shell({ project, status, children }: { project?: PocketVfxProject; status: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col md:h-screen">
      <TopBar title={project?.title} modelProfile={project?.config.model_profile} status={status} />
      {children}
    </div>
  );
}

function Message({ icon, title, hint, action }: { icon: ReactNode; title: string; hint?: string; action?: boolean }) {
  const { t } = useI18n();
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-3 bg-stage px-4 py-16 text-center" role={action ? "alert" : "status"}>
      {icon}
      <h1>{title}</h1>
      {hint && <p className="max-w-md text-muted">{hint}</p>}
      {action && <Link href="/" className="mt-2 inline-flex min-h-[44px] items-center rounded-button border border-border bg-panel px-4 font-medium hover:bg-app">{t("uploadAnother")}</Link>}
    </main>
  );
}

function Steps() {
  const { t } = useI18n();
  const steps: [string, "done" | "current" | "next"][] = [[t("stepUpload"), "done"], [t("stepTrim"), "current"], [t("stepEdit"), "next"]];
  return (
    <ol aria-label={t("steps")} className="flex items-center gap-2 text-xs">
      {steps.map(([label, s], i) => (
        <li key={label} className="flex items-center gap-2" aria-current={s === "current" ? "step" : undefined}>
          {i > 0 && <span className="h-px w-6 bg-border" aria-hidden />}
          <span className={cx("flex h-5 w-5 items-center justify-center rounded-full border text-[11px] tabular",
            s === "done" ? "border-tracking bg-tracking/15 text-tracking" : s === "current" ? "border-accent bg-accent text-app" : "border-border text-muted")}>
            {s === "done" ? <Check className="h-3 w-3" aria-hidden /> : i + 1}
          </span>
          <span className={s === "current" ? "font-medium text-ink" : "text-muted"}>{label}</span>
        </li>
      ))}
    </ol>
  );
}

function TrimEditor({ project, clip, sourceMs, analyze, save, follow }: {
  project: PocketVfxProject;
  clip: Asset;
  sourceMs: number;
  analyze: Job | undefined;
  save: ReturnType<typeof useConfigSaver>["save"];
  follow: (job: Job) => void;
}) {
  const { t } = useI18n();
  const config = project.config;
  const saved: Settings = { trim: config.trim, aspect: config.output.aspect };
  const [draft, setDraft] = useState<Settings | null>(null);
  const settings = draft ?? saved;
  const dirty = !sameSettings(settings, saved);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const running = starting || (!!analyze && !TERMINAL_JOB_STATES.includes(analyze.state));
  const work = config.work_asset_id ? project.assets.find((a) => a.id === config.work_asset_id) : undefined;
  const prepared = !!work && !dirty;
  const failed = !running && !prepared && !dirty && analyze?.state === "failed" && analyze.input_revision === project.current_revision;

  const source = clipSource(clip);
  const hasAudio = source?.has_audio ?? false;
  const previewUrl = useAssetUrl(derivedAsset(project.assets, "clip_preview", clip.id));
  const stripAsset = derivedAsset(project.assets, "clip_filmstrip", clip.id);
  const stripUrl = useAssetUrl(stripAsset);
  const thumbs = ((stripAsset?.meta as { frame_indices?: number[] } | undefined)?.frame_indices ?? []).length || 12;

  // Preview playback loops inside the selected range; the playhead follows without re-rendering the screen.
  const video = useRef<HTMLVideoElement>(null);
  const playhead = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false);
  const range = useRef(settings.trim);
  range.current = settings.trim;
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    let raf = 0;
    const tick = () => {
      const { source_start_ms: s, duration_ms: d } = range.current;
      const ms = v.currentTime * 1000;
      if (!v.paused && (ms >= s + d || ms < s - 50)) v.currentTime = s / 1000;
      if (playhead.current) playhead.current.style.left = `${(100 * Math.min(ms, sourceMs)) / sourceMs}%`;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [sourceMs, previewUrl]);
  const scrub = (ms: number) => {
    const v = video.current;
    if (!v) return;
    v.pause();
    v.currentTime = ms / 1000;
  };
  const togglePlay = () => {
    const v = video.current;
    if (!v) return;
    if (v.paused) {
      const ms = v.currentTime * 1000;
      const { source_start_ms: s, duration_ms: d } = settings.trim;
      if (ms < s || ms >= s + d - 20) v.currentTime = s / 1000;
      void v.play();
    } else v.pause();
  };

  const edit = (next: Partial<Settings>) => {
    setError(null);
    setDraft({ ...settings, ...next });
  };
  const prepare = async () => {
    setError(null);
    setStarting(true);
    try {
      const chosen = settings;
      const p = await save((c, proj) => applyClipSettings(c, chosen, proj.assets));
      setDraft(null);
      if (p.config.work_asset_id) return; // these settings were prepared before: relinked, no new job
      follow(await api.createJob(p.id, "vfx.analyze", p.current_revision, {}, crypto.randomUUID()));
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setStarting(false);
    }
  };
  const end = settings.trim.source_start_ms + settings.trim.duration_ms;
  const rotation = source?.rotation ?? 0;
  const ratio = `${source?.display_w ?? clip.width ?? 16} / ${source?.display_h ?? clip.height ?? 9}`;

  return (
    <main className="grid min-h-0 flex-1 md:grid-cols-[minmax(0,1fr)_300px]">
      <section aria-label={t("trimTitle")} className="flex min-h-0 flex-col gap-4 overflow-y-auto bg-stage p-3 md:p-6">
        <Steps />
        <div className="flex min-h-[220px] flex-1 items-center justify-center">
          <div className="relative flex h-full max-h-[48vh] w-full items-center justify-center md:max-h-none">
            {previewUrl ? (
              <video ref={video} src={previewUrl} muted playsInline preload="auto"
                aria-label={t("preview")} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onClick={togglePlay}
                onLoadedMetadata={() => scrub(settings.trim.source_start_ms)}
                className="max-h-full max-w-full rounded-panel bg-black" style={{ aspectRatio: ratio }} />
            ) : (
              <p className="flex items-center gap-2 text-muted" role="status"><Loader2 className="h-4 w-4 animate-spin" aria-hidden />{t("previewLoading")}</p>
            )}
          </div>
        </div>
        <div className="flex items-center justify-between gap-3">
          <Button size="sm" variant="secondary" onClick={togglePlay} disabled={!previewUrl}
            icon={playing ? <Pause className="h-4 w-4" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}>
            {playing ? t("pause") : t("play")}
          </Button>
          <p className="tabular text-xs text-muted">
            <span className="text-ink">{secs(settings.trim.source_start_ms)}</span> – <span className="text-ink">{secs(end)}</span> {t("seconds")}
            {" · "}{t("trimLength")} <span className="font-medium text-accent">{secs(settings.trim.duration_ms)} {t("seconds")}</span>
          </p>
        </div>
        <div>
          <h2 className="mb-1 font-semibold">{t("trimTitle")}</h2>
          <p className="mb-3 text-xs text-muted">{t("trimHint")}</p>
          <TrimBar sourceMs={sourceMs} trim={settings.trim} onChange={(trim) => edit({ trim })} onScrub={scrub}
            filmstripUrl={stripUrl} thumbs={thumbs} disabled={running} playheadRef={playhead}
            labels={{ bar: t("trimBar"), start: t("trimStart"), end: t("trimEnd"),
              startValue: t("trimStartAt", { s: secs(settings.trim.source_start_ms) }), endValue: t("trimEndAt", { s: secs(end) }) }} />
          <div className="mt-1 flex justify-between px-5 text-[11px] text-muted tabular" aria-hidden>
            <span>0 {t("seconds")}</span><span>{secs(sourceMs)} {t("seconds")}</span>
          </div>
        </div>
        {prepared && work && <PreparedStrip project={project} work={work} />}
      </section>

      <aside className="scroll-panel flex flex-col gap-5 border-t border-border bg-panel p-4 md:border-l md:border-t-0">
        <Panel title={t("clipInfo")}>
          <p className="truncate text-[13px] font-medium" title={clip.filename ?? undefined}>{clip.filename ?? project.title}</p>
          <p className="tabular text-xs text-muted">
            {t("clipSize", { w: source?.display_w ?? clip.width ?? "?", h: source?.display_h ?? clip.height ?? "?" })}
            {" · "}{t("clipDuration", { s: secs(sourceMs) })}
            {source && <>{" · "}{source.is_vfr ? t("clipVfr") : t("clipFps", { fps: Math.round(source.avg_fps * 100) / 100 })}</>}
          </p>
          <p className="flex items-center gap-1.5 text-xs text-muted">
            {hasAudio ? <Volume2 className="h-3.5 w-3.5" aria-hidden /> : <VolumeX className="h-3.5 w-3.5" aria-hidden />}
            {hasAudio ? t("withSound") : t("silent")}
          </p>
          {rotation !== 0 && <p className="text-xs text-muted">{t("clipRotated", { deg: rotation })}</p>}
        </Panel>

        <Panel title={t("trimTitle")}>
          <div className="grid grid-cols-2 gap-2">
            <SecondsField label={t("trimStart")} ms={settings.trim.source_start_ms} disabled={running}
              onCommit={(ms) => edit({ trim: moveTrim(settings.trim, ms, sourceMs) })} />
            <SecondsField label={`${t("trimLength")} (${t("lengthRange")})`} ms={settings.trim.duration_ms} disabled={running}
              onCommit={(ms) => edit({ trim: setTrimDuration(settings.trim, ms, sourceMs) })} />
          </div>
        </Panel>

        <Panel title={t("frameTitle")}>
          <Segmented<Aspect> label={t("aspect")} value={settings.aspect} onChange={(aspect) => !running && edit({ aspect })}
            options={[{ value: "16:9", label: t("aspectLandscape"), disabled: running }, { value: "9:16", label: t("aspectPortrait"), disabled: running }]} />
          <p className="text-xs text-muted">{t("aspectHint")}</p>
        </Panel>

        <Panel title={t("soundTitle")}>
          <label className={cx("flex min-h-[44px] items-center justify-between gap-3 text-[13px] md:min-h-8", !hasAudio && "text-muted")}>
            {t("keepSound")}
            <input type="checkbox" role="switch" className="h-5 w-9 cursor-pointer accent-[#FF9A52] disabled:cursor-not-allowed"
              checked={hasAudio && config.audio.keep_original} disabled={!hasAudio}
              onChange={(e) => {
                const on = e.target.checked;
                void save((c) => ({ ...c, audio: { ...c.audio, keep_original: on } })).catch(() => {});
              }} />
          </label>
          <p className="text-xs text-muted">{hasAudio ? t("keepSoundHint") : t("noSound")}</p>
        </Panel>

        <div className="mt-auto flex flex-col gap-3 border-t border-border pt-4" data-region="prepare">
          {work && dirty && <p className="flex gap-2 text-xs text-warning"><CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />{t("settingsChanged")}</p>}
          {running && <AnalyzeProgress job={analyze} />}
          {prepared ? <PreparedSummary project={project} work={work!} /> : (
            <Button variant="primary" onClick={() => void prepare()} disabled={running} className="w-full"
              icon={running ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Clapperboard className="h-4 w-4" aria-hidden />}>
              {running ? t("preparing") : work || failed ? t("prepareAgain") : t("prepare")}
            </Button>
          )}
          {!prepared && <p className="text-xs text-muted">{t("prepareNoCost")}</p>}
          {failed && !error && <ErrorLine text={`${t("prepareFailed")}${analyze?.error?.message ? ` ${analyze.error.message}` : ""}`} />}
          {error && <ErrorLine text={error} />}
        </div>
      </aside>
    </main>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h2>
      {children}
    </section>
  );
}

function ErrorLine({ text }: { text: string }) {
  const { t } = useI18n();
  return (
    <p role="alert" className="flex items-start gap-2 rounded-button border border-red-200 bg-red-50 px-3 py-2 text-xs text-danger">
      <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
      <span><span className="font-semibold">{t("errorLabel")}: </span>{text}</span>
    </p>
  );
}

/** Seconds number field (2 decimals): typing is free, the value is committed (clamped by the caller) on blur / Enter. */
function SecondsField({ label, ms, onCommit, disabled }: { label: string; ms: number; onCommit: (ms: number) => void; disabled?: boolean }) {
  const [text, setText] = useState(secs(ms));
  useEffect(() => setText(secs(ms)), [ms]);
  const commit = () => {
    const v = Number.parseFloat(text.replace(",", "."));
    if (Number.isFinite(v)) onCommit(Math.round(v * 1000));
    else setText(secs(ms));
  };
  return (
    <label className="flex flex-col gap-1 text-xs text-muted">
      {label}
      <input type="text" inputMode="decimal" value={text} disabled={disabled} onChange={(e) => setText(e.target.value)} onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") commit(); }}
        className="tabular min-h-[44px] rounded-button border border-border bg-app px-2 text-[13px] text-ink disabled:text-muted md:min-h-9" />
    </label>
  );
}

function AnalyzeProgress({ job }: { job: Job | undefined }) {
  const { t } = useI18n();
  const current = job?.stage ? ANALYZE_STAGES.indexOf(job.stage as (typeof ANALYZE_STAGES)[number]) : -1;
  return (
    <ol className="flex flex-col gap-1 text-xs" aria-live="polite">
      {(!job || job.state === "queued") && <li className="text-muted">{t("jobQueued")}</li>}
      {ANALYZE_STAGES.map((s, i) => (
        <li key={s} className={cx("flex items-center gap-2", i < current ? "text-tracking" : i === current ? "text-ink" : "text-muted")}>
          {i < current ? <Check className="h-3.5 w-3.5" aria-hidden /> : i === current ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            : <span className="h-3.5 w-3.5 rounded-full border border-border" aria-hidden />}
          {t(`stage_${s}`)}
        </li>
      ))}
    </ol>
  );
}

function motionOf(project: PocketVfxProject, work: Asset): VfxCameraMotionStatus | null {
  const m = derivedAsset(project.assets, "motion_report", work.id);
  return ((m?.meta as { status?: VfxCameraMotionStatus } | undefined)?.status) ?? null;
}

function PreparedSummary({ project, work }: { project: PocketVfxProject; work: Asset }) {
  const { t } = useI18n();
  const first = useAssetUrl(derivedAsset(project.assets, "first_frame", work.id));
  const motion = motionOf(project, work);
  const aspect = project.config.output.aspect;
  return (
    <div className="flex flex-col gap-2" data-region="prepared" role="status">
      <p className="flex items-center gap-2 font-medium text-tracking"><Check className="h-4 w-4" aria-hidden />{t("prepared")}</p>
      <figure className="flex flex-col gap-1">
        <div className="flex items-center justify-center overflow-hidden rounded-card bg-stage" style={{ aspectRatio: aspect.replace(":", " / ") }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {first && <img src={first} alt={t("firstFrame")} className="h-full w-full object-contain" />}
        </div>
        <figcaption className="tabular text-xs text-muted">{t("firstFrame")} · {t("preparedSummary", { frames: work.frame_count ?? "?", aspect })}</figcaption>
      </figure>
      {motion && (motion === "fixed"
        ? <span><Badge tone="success" icon={<Check className="h-3 w-3" aria-hidden />}>{t("camera_fixed")}</Badge></span>
        : <p className="flex gap-2 text-xs text-warning"><CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />{t(`camera_${motion}`)}</p>)}
      <p className="text-xs text-muted">{t("nextStep")}</p>
    </div>
  );
}

/** Filmstrip of the prepared (normalized, trimmed, cropped) work clip with its frame numbers. */
function PreparedStrip({ project, work }: { project: PocketVfxProject; work: Asset }) {
  const { t } = useI18n();
  const strip = derivedAsset(project.assets, "filmstrip", work.id);
  const url = useAssetUrl(strip);
  const frames = (strip?.meta as { frame_indices?: number[] } | undefined)?.frame_indices ?? [];
  return (
    <figure className="flex flex-col gap-1" data-region="work-filmstrip">
      <figcaption className="text-xs font-medium text-muted">{t("filmstrip")} · {t("preparedSummary", { frames: work.frame_count ?? "?", aspect: project.config.output.aspect })}</figcaption>
      <div className="flex h-20 items-center justify-center overflow-hidden rounded-card border border-border bg-panel">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        {url && <img src={url} alt={t("filmstrip")} className="block h-full w-auto max-w-full object-contain" />}
      </div>
      {frames.length > 1 && <p className="tabular text-[11px] text-muted">{t("filmstripFrames", { first: frames[0], last: frames[frames.length - 1] })}</p>}
    </figure>
  );
}
