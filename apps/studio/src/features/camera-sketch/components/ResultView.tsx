"use client";

/**
 * Result screen (spec §4): tabs Source / Geometry / Depth guide / Cinematic over synced players,
 * header with the Geometry hole ratio and the Cinematic QA status (unknown is never a pass), mode
 * badges, Mock and "Previous version" markers, path overlay toggle (preview only), downloads and
 * share links. Mobile stacks every tab's player at full width.
 */
import type { CameraCinematicResult, CameraGuideResult, Job } from "@oc/contracts";
import { Badge, cx } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { CircleCheck, CircleHelp, History, TriangleAlert } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAssetUrl } from "../hooks/assets";
import { useI18n } from "../i18n";
import type { Dict } from "../i18n/en";
import { outputSize } from "../model/preview";
import { guideWarningCodes, isPreviousVersion, qaStatus, targetTrajectory } from "../model/results";
import { reasonKey } from "../model/sketch";
import { assetById, useEditor } from "../state/editorStore";
import { ModeBadge } from "./badges";
import { ExportMenu } from "./ExportMenu";
import { ShareLink } from "./ShareView";
import { SyncedPlayers, type PlayerSource } from "./SyncedPlayers";

type Tab = "source" | "geometry" | "depth" | "cinematic";
const TABS: Tab[] = ["source", "geometry", "depth", "cinematic"];
const TAB_KEY: Record<Tab, keyof Dict> = { source: "tabSource", geometry: "tabGeometry", depth: "tabDepth", cinematic: "tabCinematic" };

const newest = (a: Job, b: Job) => (a.created_at < b.created_at ? 1 : -1);
const pct = (x: number | null | undefined, d = 1) => (typeof x === "number" && Number.isFinite(x) ? (x * 100).toFixed(d) : "—");

/** Guides and cinematics that succeeded, newest first, and the default pair to show. */
export function resultSets(jobs: Record<string, Job>) {
  const all = Object.values(jobs).filter((j) => j.state === "succeeded").sort(newest);
  const guides = all.filter((j) => j.kind === "camera.guide");
  const cinematics = all.filter((j) => j.kind === "camera.cinematic");
  const latest = all.find((j) => j.kind === "camera.guide" || j.kind === "camera.cinematic");
  const defaultGuideId = latest?.kind === "camera.cinematic"
    ? (latest.result as CameraCinematicResult | null)?.guide_job_id ?? guides[0]?.id
    : latest?.id;
  return { guides, cinematics, defaultGuideId: defaultGuideId ?? null };
}

function QaHeader({ job }: { job: Job | undefined }) {
  const { t } = useI18n();
  if (!job) return <p className="text-muted">{t("noCinematicYet")}</p>;
  const qa = qaStatus((job.result as CameraCinematicResult | null)?.qa);
  const Icon = qa.kind === "pass" ? CircleCheck : qa.kind === "check" ? TriangleAlert : CircleHelp;
  return (
    <div data-qa={qa.kind}>
      <p className={cx("flex items-center gap-1.5 font-medium", qa.kind === "pass" ? "text-accent" : qa.kind === "check" ? "text-target" : "text-muted")}>
        <Icon className="h-3.5 w-3.5" aria-hidden />{t(qa.titleKey)}
      </p>
      {qa.kind !== "pass" && qa.reasonKeys.length > 0 && (
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted">{qa.reasonKeys.map((k) => <li key={k}>{t(k)}</li>)}</ul>
      )}
    </div>
  );
}

export function ResultView({ mobile = false }: { mobile?: boolean }) {
  const { t } = useI18n();
  const project = useEditor((s) => s.project);
  const config = useEditor((s) => s.config);
  const revision = useEditor((s) => s.revision);
  const jobs = useEditor((s) => s.jobs);
  const [tab, setTab] = useState<Tab>("geometry");
  const [sideBySide, setSideBySide] = useState(false);
  const [overlay, setOverlay] = useState(false);
  const [pickedGuide, setPickedGuide] = useState<string | null>(null);
  const overlayId = useId();
  const tabsId = useId();
  const pickId = useId();
  const caps = useQuery({ queryKey: ["caps"], queryFn: () => api.cameraCapabilities(), staleTime: 60_000 });

  const { guides, cinematics, defaultGuideId } = useMemo(() => resultSets(jobs), [jobs]);
  const guide = guides.find((g) => g.id === (pickedGuide ?? defaultGuideId)) ?? guides[0];
  const gres = guide?.result as CameraGuideResult | null | undefined;
  const cinematic = guide ? cinematics.find((c) => (c.result as CameraCinematicResult | null)?.guide_job_id === guide.id) : undefined;
  const cres = cinematic?.result as CameraCinematicResult | null | undefined;

  const pathAsset = assetById(project, gres?.path_asset_id);
  const pathUrl = useAssetUrl(pathAsset);
  // With OC_STORAGE_BACKEND=s3 this fetch of the signed read URL is cross-origin; bucket CORS requirements:
  // README "Camera Sketch" > "S3 / MinIO: bucket CORS".
  const artifact = useQuery({
    queryKey: ["path-artifact", pathAsset?.id],
    queryFn: async () => (await (await fetch(pathUrl!)).json()) as { user_config?: { scene_asset_id?: string; output?: { aspect?: "16:9" | "9:16" } } },
    enabled: !!pathUrl,
    staleTime: Infinity,
  });
  const trajectory = useMemo(() => targetTrajectory(artifact.data), [artifact.data]);
  const sceneId = artifact.data?.user_config?.scene_asset_id ?? config?.scene_asset_id;
  const aspect = outputSize(artifact.data?.user_config?.output?.aspect ?? config?.output.aspect ?? "16:9");

  const sceneUrl = useAssetUrl(assetById(project, sceneId));
  const geoUrl = useAssetUrl(assetById(project, gres?.geometry_video_asset_id));
  const depthUrl = useAssetUrl(assetById(project, gres?.guide_depth_video_asset_id));
  const cineUrl = useAssetUrl(assetById(project, cres?.cinematic_video_asset_id));

  if (!project || !config) return null;
  if (!guide || !gres) {
    return <p className="p-4 text-center text-xs text-muted" data-region="results-empty">{t("noResultYet")}</p>;
  }

  const isMock = (j: Job | undefined, mock: boolean | undefined) =>
    !!j && (!!mock || !!caps.data?.profiles.find((p) => p.id === j.model_profile)?.is_mock);
  const geoMock = isMock(guide, gres.mock);
  const cineMock = isMock(cinematic, cres?.mock);
  const prev = (j: Job | undefined) => isPreviousVersion(j, revision) && (
    <Badge icon={<History className="h-3 w-3" aria-hidden />} title={t("previousVersionHint")}>{t("previousVersion")}</Badge>
  );
  const mockBadge = <Badge tone="mock" title={t("mockExplain")}>{t("mock")}</Badge>;
  const warnings = guideWarningCodes(gres.warnings);

  const sources: PlayerSource[] = [
    { id: "source", label: t("tabSource"), kind: "image", url: sceneUrl },
    { id: "geometry", label: t("tabGeometry"), kind: "video", url: geoUrl, overlay: true,
      header: <><ModeBadge mode="geometry" />{geoMock && mockBadge}{prev(guide)}</> },
    { id: "depth", label: t("tabDepth"), kind: "video", url: depthUrl, overlay: true,
      header: <><ModeBadge mode="geometry" />{geoMock && mockBadge}{prev(guide)}</> },
    ...(cinematic && cres ? [{
      id: "cinematic", label: t("tabCinematic"), kind: "video" as const, url: cineUrl, overlay: true,
      header: <><ModeBadge mode="cinematic" />{cineMock && mockBadge}{prev(cinematic)}</>,
      footer: <p className="text-xs text-muted" data-region="cinematic-note">{t("cinematicNote")}</p>,
    }] : []),
  ];
  const hasCine = !!cinematic;
  const visible: string[] = mobile ? sources.map((s) => s.id)
    : sideBySide ? (tab === "geometry" || tab === "source" ? ["geometry", hasCine ? "cinematic" : "depth"] : ["geometry", tab])
    : [tab === "cinematic" && !hasCine ? "geometry" : tab];

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4" data-region="results">
      {/* Header: Geometry diagnostics and Cinematic QA */}
      <div className="grid gap-3 rounded-panel border border-border bg-panel p-3 text-xs md:grid-cols-2" data-region="results-header">
        <div className="space-y-1">
          <p className="flex flex-wrap items-center gap-2 font-semibold uppercase tracking-wide text-muted">
            {t("geometryHeader")} <ModeBadge mode="geometry" />{geoMock && mockBadge}{prev(guide)}
          </p>
          <p className="tabular" data-region="hole-ratio">{t("holeRatioResult", { max: pct(gres.hole_ratio_max), mean: pct(gres.hole_ratio_mean) })}</p>
          <p className="tabular text-muted">{t("visibilityResult", { value: pct(gres.target_visibility_min, 0) })}</p>
          {warnings.length > 0 && (
            <ul className="space-y-0.5 text-target">{warnings.map((w) => (
              <li key={w} className="flex items-start gap-1.5"><TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />{t(reasonKey(w))}</li>
            ))}</ul>
          )}
          {isPreviousVersion(guide, revision) && <p className="text-muted">{t("previousVersionHint")}</p>}
        </div>
        <div className="space-y-1">
          <p className="flex flex-wrap items-center gap-2 font-semibold uppercase tracking-wide text-muted">
            {t("cinematicHeader")} {cinematic && <ModeBadge mode="cinematic" />}{cinematic && cineMock && mockBadge}{prev(cinematic)}
          </p>
          <QaHeader job={cinematic} />
          {cinematic && <p className="text-muted">{t("cinematicNote")}</p>}
        </div>
      </div>

      {/* Controls: version, tabs, side by side, overlay */}
      <div className="flex flex-wrap items-center gap-3 text-xs">
        {guides.length > 1 && (
          <label htmlFor={pickId} className="flex items-center gap-2 text-muted">
            {t("resultVersion")}
            <select id={pickId} value={guide.id} onChange={(e) => setPickedGuide(e.target.value)}
              className="min-h-[44px] rounded-button border border-border bg-app px-2 text-ink md:min-h-8">
              {guides.map((g) => (
                <option key={g.id} value={g.id}>{t("resultOption", { rev: g.input_revision, time: new Date(g.created_at).toLocaleTimeString() })}</option>
              ))}
            </select>
          </label>
        )}
        {!mobile && (
          <div role="tablist" aria-label={t("results")} className="flex rounded-button border border-border bg-app p-0.5">
            {TABS.map((k) => (
              <button key={k} role="tab" type="button" id={`${tabsId}-tab-${k}`} aria-controls={`${tabsId}-panel`} aria-selected={tab === k}
                tabIndex={tab === k ? 0 : -1} disabled={k === "cinematic" && !hasCine} onClick={() => setTab(k)}
                onKeyDown={(e) => {
                  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                  const enabled = TABS.filter((x) => x !== "cinematic" || hasCine);
                  const next = enabled[(enabled.indexOf(tab) + (e.key === "ArrowRight" ? 1 : enabled.length - 1)) % enabled.length];
                  setTab(next);
                  document.getElementById(`${tabsId}-tab-${next}`)?.focus();
                }}
                className={cx("min-h-8 rounded-[6px] px-3 text-[13px] disabled:opacity-40", tab === k ? "bg-panel font-medium text-ink" : "text-muted hover:text-ink")}>
                {t(TAB_KEY[k])}
              </button>
            ))}
          </div>
        )}
        {!mobile && (
          <label className="flex min-h-8 items-center gap-1.5 text-muted">
            <input type="checkbox" checked={sideBySide} onChange={(e) => setSideBySide(e.target.checked)} className="h-4 w-4 accent-[#66DCF2]" />
            {t("sideBySide")}
          </label>
        )}
        <label htmlFor={overlayId} className="flex min-h-[44px] items-center gap-1.5 text-muted md:min-h-8" title={t("pathOverlayHint")}>
          <input id={overlayId} type="checkbox" checked={overlay} disabled={!trajectory} onChange={(e) => setOverlay(e.target.checked)} className="h-4 w-4 accent-[#66DCF2]" />
          {t("pathOverlay")}
          <span className="hidden text-[11px] lg:inline">· {t("pathOverlayHint")}</span>
        </label>
      </div>

      <div id={`${tabsId}-panel`} {...(mobile ? {} : { role: "tabpanel", "aria-labelledby": `${tabsId}-tab-${tab}` })}>
        <SyncedPlayers sources={sources} visibleIds={visible} columns={!mobile && visible.length > 1 ? 2 : 1} aspect={aspect}
          trajectory={trajectory} showOverlay={overlay} />
      </div>

      {/* Downloads and share links */}
      <div className="grid gap-4 rounded-panel border border-border bg-panel p-3 md:grid-cols-2">
        <div className="space-y-3">
          <ExportMenu key={guide.id} job={guide} label={t("tabGeometry")} />
          <ShareLink key={`g-${gres.geometry_video_asset_id}`} assetId={gres.geometry_video_asset_id} label={t("tabGeometry")} />
        </div>
        {cinematic && cres && (
          <div className="space-y-3">
            <ExportMenu key={cinematic.id} job={cinematic} label={t("tabCinematic")} />
            <ShareLink key={`c-${cres.cinematic_video_asset_id}`} assetId={cres.cinematic_video_asset_id} label={t("tabCinematic")} />
          </div>
        )}
      </div>
    </div>
  );
}
