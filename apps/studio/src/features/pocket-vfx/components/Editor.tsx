"use client";

/**
 * Screen B (spec §3): top bar (title, save state, undo / redo, export), effect cards and tools (left 220 px), the
 * stage with the draft preview, the inspector column (300 px: render action, then the inspector sections with tracking)
 * and the 160 px timeline. Below 768 px: stage + timeline with an Effect / Track / Adjust / Result bottom sheet (Track:
 * tools + tracking; Adjust: render action + the other inspector sections). Every saved revision is validated
 * (`useValidateLoop`). Screen C (results) replaces the stage and timeline: the Edit / Result switch on desktop, the
 * Result tab on mobile; Export and a finished render open it.
 *
 * Track data: the newest succeeded vfx.track gives the track JSON (trajectory, confidence, invalid intervals) and the
 * mask preview; the config of the revision it ran on is the staleness basis (`trackInputs`).
 */
import type { Job, PocketVfxConfig, PocketVfxProject, TrackJson, VfxTrackResult } from "@oc/contracts";
import { Button, cx } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { Brush, Crosshair, Download, Image as ImageIcon, Loader2, MousePointerClick, Redo2, Scissors, Undo2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { derivedAsset } from "../flow/clip";
import { useAssetUrl } from "../hooks/assets";
import { useIsDesktop } from "../hooks/media";
import { useEditorShortcuts } from "../hooks/shortcuts";
import { useFollowActiveJobs } from "../hooks/jobs";
import { useValidateLoop } from "../hooks/validate";
import { useDecodedFrames } from "../hooks/useFrames";
import { useI18n } from "../i18n";
import { renderResults } from "../model/results";
import { frameCount } from "../model/timeline";
import { assetById, trackInputs, trackStatus, useEditor, type Tool } from "../state/editorStore";
import { EffectCards, usePresets } from "./EffectCards";
import { Inspector } from "./Inspector";
import { RenderBar } from "./RenderBar";
import { ResultView } from "./ResultView";
import { Stage } from "./Stage";
import { Timeline } from "./Timeline";
import { TrackPanel } from "./TrackPanel";
import { TopBar } from "../../../shell/TopBar";
import { useVfxMock } from "../hooks/capabilities";
import { ProvenanceBadge } from "../../../shell/ProvenanceBadge";

type MobileTab = "effect" | "track" | "adjust" | "result";

const TOOLS: { id: Exclude<Tool, "none">; icon: typeof Crosshair; label: "toolAnchor" | "toolSubject" | "toolMask" | "toolCleanPlate";
  hint: "toolHintAnchor" | "toolHintSubject" | "toolHintMask" | "toolHintCleanPlate" }[] = [
  { id: "anchor", icon: Crosshair, label: "toolAnchor", hint: "toolHintAnchor" },
  { id: "subject", icon: MousePointerClick, label: "toolSubject", hint: "toolHintSubject" },
  { id: "mask", icon: Brush, label: "toolMask", hint: "toolHintMask" },
  { id: "clean_plate", icon: ImageIcon, label: "toolCleanPlate", hint: "toolHintCleanPlate" },
];

function ToolPicker() {
  const { t } = useI18n();
  const tool = useEditor((s) => s.tool);
  const blink = useEditor((s) => s.config!.effect.preset_id === "blink_out_v1" && !!s.config!.clean_plate_asset_id);
  const active = TOOLS.find((x) => x.id === tool);
  return (
    <section aria-label={t("tools")} className="flex flex-col gap-2" data-region="tools">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{t("tools")}</h2>
      <div role="radiogroup" aria-label={t("tools")} className="grid grid-cols-4 gap-1 md:grid-cols-2">
        {TOOLS.map(({ id, icon: Icon, label }) => {
          const disabled = id === "clean_plate" && !blink;
          return (
            <button key={id} type="button" role="radio" aria-checked={tool === id} disabled={disabled}
              title={disabled ? t("cleanPlateOnlyBlink") : t(label)}
              onClick={() => useEditor.getState().setTool(tool === id ? "none" : id)}
              className={cx("flex min-h-[52px] flex-col items-center justify-center gap-0.5 rounded-button border px-1 text-[11px] leading-tight",
                tool === id ? "border-accent bg-app text-accent" : "border-border text-muted hover:text-ink",
                "disabled:opacity-40 disabled:hover:text-muted")}>
              <Icon className="h-4 w-4" aria-hidden />
              {t(label)}
            </button>
          );
        })}
      </div>
      <p className="text-xs text-muted" aria-live="polite">{active ? t(active.hint) : t("toolNoneHint")}</p>
    </section>
  );
}

function Guide() {
  const { t } = useI18n();
  return (
    <section className="flex flex-col gap-2" aria-label={t("howTo")}>
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{t("howTo")}</h2>
      <ol className="list-decimal space-y-1 pl-4 text-xs text-muted">
        <li>{t("guide1")}</li>
        <li>{t("guide2")}</li>
        <li>{t("guide3")}</li>
        <li>{t("guide4")}</li>
      </ol>
    </section>
  );
}

const newest = <T extends { created_at: string }>(xs: T[]) => xs.reduce<T | undefined>((a, b) => (!a || b.created_at > a.created_at ? b : a), undefined);

/** Latest succeeded track: its JSON, mask preview frames, and the staleness basis (config of its revision). */
function useTrackData(project: PocketVfxProject) {
  const jobs = useEditor((s) => s.jobs);
  const job = newest(Object.values(jobs).filter((j) => j.kind === "vfx.track" && j.state === "succeeded"));
  const result = (job?.result ?? null) as VfxTrackResult | null;
  const rev = useQuery({
    queryKey: ["revision", project.id, job?.input_revision],
    queryFn: () => api.revision(project.id, job!.input_revision),
    enabled: !!job, staleTime: Infinity,
  });
  const basisConfig: PocketVfxConfig | undefined = job && rev.data?.revision === job.input_revision ? rev.data.config : undefined;
  useEffect(() => {
    useEditor.getState().setTrackBasis(basisConfig ? trackInputs(basisConfig) : null);
  }, [basisConfig]);
  const trackUrl = useAssetUrl(assetById(project, result?.track_asset_id));
  const json = useQuery({
    queryKey: ["track-json", result?.track_asset_id],
    queryFn: async () => (await (await fetch(trackUrl!)).json()) as TrackJson,
    enabled: !!trackUrl, staleTime: Infinity,
  });
  const masks = result?.masks ?? null;
  const maskUrl = useAssetUrl(assetById(project, masks?.mask_preview_asset_id));
  const mask = useDecodedFrames(maskUrl, masks?.frame_count ?? 0, 24, "mask", 320);
  return { result, track: json.data ?? null, mask };
}

function usePlatePreview(project: PocketVfxProject, config: PocketVfxConfig) {
  const plate = config.plate.mode === "generated" ? assetById(project, config.plate.asset_id) : undefined;
  const url = useAssetUrl(plate);
  const frames = plate?.frame_count ?? Number((plate?.meta as { frames?: number } | undefined)?.frames ?? 0);
  const fps = plate?.fps_num && plate.fps_den ? plate.fps_num / plate.fps_den : 24;
  const seq = useDecodedFrames(url, frames, fps, "plate", 256);
  return seq ? { seq, frames, loopable: false } : null;
}

export function Editor({ initial, onTrim }: { initial: PocketVfxProject; onTrim: () => Promise<void> }) {
  const { t } = useI18n();
  const desktop = useIsDesktop();
  const project = useEditor((s) => s.project);
  const config = useEditor((s) => s.config);
  const loaded = project?.id === initial.id && !!config;
  useEffect(() => {
    useEditor.getState().load(initial);
  }, [initial.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEditorShortcuts();

  // Uploads (mask edits, clean plate) are inspected by the worker: reload assets until they settle.
  const waiting = !!project?.assets.some((a) => a.state === "uploaded" || a.state === "pending_upload");
  useEffect(() => {
    if (!waiting) return;
    const id = setInterval(() => void useEditor.getState().refresh().catch(() => {}), 1500);
    return () => clearInterval(id);
  }, [waiting]);

  if (!loaded) {
    return <main className="flex flex-1 items-center justify-center bg-stage text-muted"><Loader2 className="h-5 w-5 animate-spin" aria-hidden /></main>;
  }
  return <EditorBody project={project!} config={config!} desktop={desktop} onTrim={onTrim} t={t} />;
}

function EditorBody({ project, config, desktop, onTrim, t }: {
  project: PocketVfxProject; config: PocketVfxConfig; desktop: boolean; onTrim: () => Promise<void>; t: ReturnType<typeof useI18n>["t"];
}) {
  const saveState = useEditor((s) => s.saveState);
  const canUndo = useEditor((s) => s.canUndo);
  const canRedo = useEditor((s) => s.canRedo);
  const basis = useEditor((s) => s.trackBasis);
  const [tab, setTab] = useState<MobileTab>("effect");
  /** desktop center column: the editor stage or the result screen */
  const [view, setView] = useState<"edit" | "result">("edit");
  /** render shown in the result screen (null = newest) */
  const [resultId, setResultId] = useState<string | null>(null);
  const jobs = useEditor((s) => s.jobs);
  const hasRender = useMemo(() => renderResults(jobs).length > 0, [jobs]);
  const [leaving, setLeaving] = useState(false);
  const presets = usePresets();
  const policy = presets.find((p) => p.id === config.effect.preset_id)?.invalid_policy ?? "hide";

  const work = assetById(project, config.work_asset_id);
  const proxy = derivedAsset(project.assets, "work_proxy", work?.id);
  const proxyUrl = useAssetUrl(proxy);
  const portrait = config.output.aspect === "9:16";
  const proxySize: [number, number] = proxy?.width && proxy.height ? [proxy.width, proxy.height] : portrait ? [360, 640] : [640, 360];
  const filmstrip = derivedAsset(project.assets, "filmstrip", work?.id);
  const n = frameCount(config.trim.duration_ms);
  const { result, track, mask } = useTrackData(project);
  const plate = usePlatePreview(project, config);
  const status = trackStatus(config, basis);
  const fresh = status === "fresh";
  useValidateLoop(project.id);
  useFollowActiveJobs();

  const back = async () => {
    setLeaving(true);
    try {
      await onTrim();
    } finally {
      setLeaving(false);
    }
  };
  const showResult = (id?: string) => {
    if (id) setResultId(id);
    useEditor.getState().setPlaying(false);
    setView("result");
    setTab("result");
  };
  const showEdit = () => {
    setView("edit");
    if (tab === "result") setTab("effect");
  };
  const resultScreen = desktop ? view === "result" : tab === "result";
  const { undo, redo } = useEditor.getState();
  const mock = useVfxMock(config.model_profile, project.upstream_mock);
  const topActions = (
    <>
      <Button size="sm" variant="ghost" onClick={() => void back()} disabled={leaving} title={t("backToTrim")}
        icon={<Scissors className="h-4 w-4" aria-hidden />} className="min-w-[44px] px-2 md:min-w-0"><span className="hidden lg:inline">{t("backToTrim")}</span></Button>
      <Button size="sm" variant="ghost" onClick={undo} disabled={!canUndo} aria-label={t("undo")} title={`${t("undo")} (⌘Z)`} className="min-w-[44px] px-2 md:min-w-0"
        icon={<Undo2 className="h-4 w-4" aria-hidden />} />
      <Button size="sm" variant="ghost" onClick={redo} disabled={!canRedo} aria-label={t("redo")} title={`${t("redo")} (⇧⌘Z)`} className="min-w-[44px] px-2 md:min-w-0"
        icon={<Redo2 className="h-4 w-4" aria-hidden />} />
      <Button size="sm" variant="primary" disabled={!hasRender} title={hasRender ? t("exportOpen") : t("exportAfterRender")}
        onClick={() => showResult()} className="hidden sm:inline-flex" icon={<Download className="h-4 w-4" aria-hidden />}>
        <span className="hidden sm:inline">{t("export")}</span>
      </Button>
    </>
  );

  const stage = (
    <Stage projectId={project.id} proxyUrl={proxyUrl} proxySize={proxySize} n={n} track={track} trackFresh={fresh}
      mask={mask} plate={plate} policy={policy} />
  );
  const timeline = <Timeline n={n} filmstripId={filmstrip?.id} track={track} trackFresh={fresh} compact={!desktop} />;
  const tracking = <TrackPanel status={status} result={result} track={track} n={n} policy={policy} />;
  const renderBar = <RenderBar onDone={(j: Job) => showResult(j.id)} />;
  const results = <ResultView selectedId={resultId} onSelect={setResultId} />;
  const viewSwitch = (
    <div role="tablist" aria-label={t("editorView")} className="flex h-11 shrink-0 items-center gap-1 border-b border-border bg-panel px-2" data-region="view-switch">
      {(["edit", "result"] as const).map((id) => (
        <button key={id} type="button" role="tab" aria-selected={view === id} onClick={() => (id === "edit" ? showEdit() : showResult())}
          className={cx("min-h-9 rounded-button px-3 text-[13px]", view === id ? "bg-app font-semibold text-ink" : "text-muted hover:text-ink")}>
          {t(id === "edit" ? "viewEdit" : "viewResult")}
        </button>
      ))}
      {project.handoff && <div className="ml-auto min-w-0"><ProvenanceBadge handoff={project.handoff} /></div>}
    </div>
  );

  return (
    <div className="flex min-h-[100dvh] flex-col md:h-[100dvh] md:min-h-[640px]">
      <TopBar title={project.title} saveState={saveState} conflictText={t("conflictEditor")} mock={mock} actions={topActions} />
      {desktop ? (
        <div className="grid min-h-0 flex-1 grid-cols-vfx-shell grid-rows-[minmax(0,1fr)_160px]">
          <aside data-region="left" aria-label={t("effects")} className="scroll-panel row-span-2 flex flex-col gap-5 border-r border-border bg-panel p-3">
            <EffectCards projectId={project.id} />
            <ToolPicker />
          </aside>
          <main className={cx("flex min-h-0 min-w-0 flex-col", resultScreen && "row-span-2")}>
            {viewSwitch}
            <div className={cx("flex min-h-0 flex-1 flex-col", resultScreen && "hidden")}>{stage}</div>
            {resultScreen && <div className="scroll-panel min-h-0 flex-1 bg-stage">{results}</div>}
          </main>
          <aside data-region="inspector" aria-label={t("inspector")} className="scroll-panel row-span-2 border-l border-border bg-panel">
            <div className="sticky top-0 z-10 border-b border-border bg-panel p-3">{renderBar}</div>
            <Inspector n={n} tracking={tracking} />
            <div className="p-4"><Guide /></div>
          </aside>
          <div className={cx("col-start-2 row-start-2 min-w-0", resultScreen && "hidden")}>{timeline}</div>
        </div>
      ) : (
        <>
          {project.handoff && <div className="border-b border-border bg-panel px-3 py-1.5"><ProvenanceBadge handoff={project.handoff} /></div>}
          <main className={cx("flex h-[52dvh] min-h-[240px] flex-col", resultScreen && "hidden")}>{stage}</main>
          <div className={cx(resultScreen && "hidden")}>{timeline}</div>
          <nav aria-label={t("editorPanels")} className="border-t border-border bg-panel" data-region="bottom-sheet">
            <div role="tablist" className="sticky top-0 z-10 flex border-b border-border bg-panel">
              {(["effect", "track", "adjust", "result"] as const).map((id) => (
                <button key={id} type="button" role="tab" aria-selected={tab === id}
                  onClick={() => { if (id === "result") useEditor.getState().setPlaying(false); setTab(id); }}
                  className={cx("min-h-[44px] flex-1 border-b-2 text-[13px]", tab === id ? "border-accent font-semibold text-accent" : "border-transparent text-muted")}>
                  {t(`tab_${id}`)}
                </button>
              ))}
            </div>
            <div role="tabpanel" className={cx("flex flex-col gap-5", tab === "result" ? "p-0" : "p-3")}>
              {tab === "effect" && <EffectCards projectId={project.id} />}
              {tab === "track" && <><ToolPicker />{tracking}</>}
              {tab === "adjust" && <>{renderBar}<div className="-mx-3"><Inspector n={n} /></div><Guide /></>}
              {tab === "result" && results}
            </div>
          </nav>
        </>
      )}
    </div>
  );
}
