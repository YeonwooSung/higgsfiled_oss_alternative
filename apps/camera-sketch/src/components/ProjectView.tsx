"use client";

import type { CameraAnalyzeResult } from "@oc/contracts";
import { cx } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api/client";
import { depthToLink, latestJob, sceneToLink } from "../flow/scene";
import { useAssetUrl } from "../hooks/assets";
import { usePreviewScene } from "../hooks/previewScene";
import { useI18n } from "../i18n";
import { compilePathProvisional, sceneInfoFromTarget } from "../model/path";
import { outputSize } from "../model/preview";
import { assetById, currentServerPath, useEditor } from "../state/editorStore";
import { CameraGizmo } from "./CameraGizmo";
import { PreviewStage } from "./PreviewStage";
import { AnalyzePanel, JobStatusList } from "./ScenePanel";
import { TopBar } from "./TopBar";

type MobileTab = "upload" | "edit" | "result";

function Placeholder({ region, title, hint, className }: { region: string; title: string; hint: string; className?: string }) {
  return (
    <section data-region={region} aria-label={title}
      className={cx("flex flex-col items-center justify-center gap-1 rounded-panel border border-dashed border-border p-3 text-center", className)}>
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h2>
      <p className="text-xs text-muted">{hint}</p>
    </section>
  );
}

function SceneStage() {
  const { t } = useI18n();
  const project = useEditor((s) => s.project)!;
  const config = useEditor((s) => s.config)!;
  const scene = assetById(project, config.scene_asset_id) ?? project.assets.find((a) => a.role === "scene");
  const jobs = useEditor((s) => s.jobs);
  const analyze = latestJob(jobs, "camera.analyze");
  const previewId = config.depth_asset_id
    ? (analyze?.result as Partial<CameraAnalyzeResult> | null)?.depth_preview_asset_id
    ?? project.assets.find((a) => a.role === "depth_preview" && a.source_asset_id === config.depth_asset_id)?.id
    : undefined;
  const sceneUrl = useAssetUrl(scene);
  const previewUrl = useAssetUrl(assetById(project, previewId));
  return (
    <div data-region="stage" className="grid min-h-0 flex-1 gap-3 bg-stage p-3 md:grid-cols-2">
      <figure className="flex min-h-[160px] flex-col gap-1">
        <figcaption className="text-xs font-medium text-muted">{t("sceneImage")}</figcaption>
        <div className="flex min-h-0 flex-1 items-center justify-center rounded-panel border border-border">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {sceneUrl ? <img src={sceneUrl} alt={t("sceneImage")} className="max-h-[50vh] max-w-full object-contain md:max-h-full" /> : <p className="text-xs text-muted">{t("noScene")}</p>}
        </div>
      </figure>
      <figure className="flex min-h-[160px] flex-col gap-1">
        <figcaption className="text-xs font-medium text-muted">{t("depthPreview")} <span className="font-normal">· {t("depthPreviewHint")}</span></figcaption>
        <div className="flex min-h-0 flex-1 items-center justify-center rounded-panel border border-border">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {previewUrl ? <img src={previewUrl} alt={t("depthPreview")} className="max-h-[50vh] max-w-full object-contain md:max-h-full" /> : <p className="text-xs text-muted">{t("noDepth")}</p>}
        </div>
      </figure>
    </div>
  );
}

/**
 * The camera path shown in the stage and gizmo: the server compile for the current revision when it
 * has arrived, else the browser's provisional compile of the same config (badge "Provisional").
 */
function useCameraPath() {
  const project = useEditor((s) => s.project);
  const config = useEditor((s) => s.config);
  const serverPath = useEditor(currentServerPath);
  const scene = assetById(project, config?.scene_asset_id);
  const [outW, outH] = outputSize(config?.output.aspect ?? "16:9");
  const pathCfg = config?.path;
  const u = config?.target.u, v = config?.target.v, depthScale = config?.target.depth_scale;
  const focalMm = config?.camera.focal_length_mm_equiv, sensorWMm = config?.camera.sensor_width_mm_assumed;
  const srcW = scene?.width, srcH = scene?.height;
  const info = useMemo(() => (srcW && srcH && u !== undefined && v !== undefined && depthScale !== undefined && focalMm !== undefined
    ? sceneInfoFromTarget({ focalMm, srcW, srcH, u, v, depthScale, sensorWMm })
    : null), [srcW, srcH, u, v, depthScale, focalMm, sensorWMm]);
  const provisional = useMemo(() => (info && pathCfg ? compilePathProvisional(pathCfg, info, outW, outH) : null), [pathCfg, info, outW, outH]);
  return { info, path: serverPath ?? provisional, provisional: !serverPath, outW, outH };
}

function CameraStage({ cam }: { cam: ReturnType<typeof useCameraPath> }) {
  const project = useEditor((s) => s.project)!;
  const config = useEditor((s) => s.config)!;
  const timeMs = useEditor((s) => s.playheadMs);
  const sceneAsset = assetById(project, config.scene_asset_id);
  const depth = assetById(project, config.depth_asset_id);
  const imageUrl = useAssetUrl(sceneAsset);
  const { scene, loading, error } = usePreviewScene(depth, config);
  if (!cam.path) return <SceneStage />;
  return (
    <div data-region="stage" className="flex min-h-0 flex-1 flex-col gap-3 bg-stage p-3">
      <PreviewStage imageUrl={imageUrl} scene={scene} path={cam.path} timeMs={timeMs} outW={cam.outW} outH={cam.outH}
        showHoles={config.scene.show_holes} provisional={cam.provisional} loading={loading} error={error} className="md:flex-1" />
      {cam.info && <CameraGizmo path={cam.path} targetWorld={cam.info.target_world} timeMs={timeMs} className="md:hidden" />}
    </div>
  );
}

function CameraViews({ cam }: { cam: ReturnType<typeof useCameraPath> }) {
  const { t } = useI18n();
  const timeMs = useEditor((s) => s.playheadMs);
  if (!cam.path || !cam.info) {
    return (
      <div className="flex flex-wrap gap-3">
        <Placeholder region="view-top" title={t("viewTop")} hint={t("noDepth")} className="h-[160px] w-[220px] shrink-0" />
        <Placeholder region="view-side" title={t("viewSide")} hint={t("noDepth")} className="h-[160px] w-[220px] shrink-0" />
      </div>
    );
  }
  return <CameraGizmo path={cam.path} targetWorld={cam.info.target_world} timeMs={timeMs} />;
}

export function ProjectView({ projectId }: { projectId: string }) {
  const { t } = useI18n();
  const load = useEditor((s) => s.load);
  const project = useEditor((s) => s.project);
  const config = useEditor((s) => s.config);
  const jobs = useEditor((s) => s.jobs);
  const [mobileTab, setMobileTab] = useState<MobileTab>("upload");
  const cam = useCameraPath();
  const query = useQuery({
    queryKey: ["project", projectId],
    queryFn: async () => {
      try {
        return await api.getProject(projectId);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          await api.startSession();
          return api.getProject(projectId);
        }
        throw e;
      }
    },
    staleTime: Infinity,
  });

  useEffect(() => {
    if (query.data) load(query.data);
  }, [query.data, load]);

  // Poll while uploads are still being inspected or an analysis is in flight.
  const waiting = !!project?.assets.some((a) => a.state === "uploaded" || a.state === "pending_upload");
  useEffect(() => {
    if (!waiting) return;
    const id = setInterval(() => void useEditor.getState().refresh(), 1500);
    return () => clearInterval(id);
  }, [waiting]);

  // Link the uploaded scene into the config; clearing depth keeps the server lineage check satisfied.
  const sceneId = project && config ? sceneToLink(project.assets, config) : null;
  useEffect(() => {
    if (sceneId) useEditor.getState().link((c) => { c.scene_asset_id = sceneId; c.depth_asset_id = null; });
  }, [sceneId]);

  // The analyze executor does not write the config; the client PATCHes the depth asset.
  const depthId = project && config ? depthToLink(project.assets, config, latestJob(jobs, "camera.analyze")) : null;
  useEffect(() => {
    if (depthId) useEditor.getState().link((c) => { c.depth_asset_id = depthId; });
  }, [depthId]);

  if (query.isError) return <main className="p-8"><p role="alert" className="text-danger">{t("errorLabel")}: {t("loadFailed")}</p></main>;
  if (!project || !config) return <main className="p-8 text-muted">…</main>;

  const scenePanel = (
    <div className="space-y-5 p-3">
      <AnalyzePanel />
      <JobStatusList />
    </div>
  );

  return (
    // Desktop: fixed app shell (panels scroll on their own), never shorter than 640px.
    // Mobile (<768px): stage on top, bottom sheet with Upload / Edit / Result tabs; the page scrolls.
    <div className="flex min-h-[100dvh] flex-col md:h-[100dvh] md:min-h-[640px]">
      <TopBar />
      <div className="flex min-h-0 flex-1 flex-col md:grid md:grid-cols-[240px_minmax(0,1fr)_320px] md:grid-rows-[minmax(0,1fr)_140px]">
        <aside data-region="left" aria-label={t("panelLeft")} className="scroll-panel hidden border-r border-border bg-panel md:col-start-1 md:row-span-2 md:row-start-1 md:block">
          {scenePanel}
        </aside>
        <main className="flex min-h-0 min-w-0 flex-col md:col-start-2 md:row-start-1">
          {config.depth_asset_id ? <CameraStage cam={cam} /> : <SceneStage />}
        </main>
        <aside data-region="right" aria-label={t("panelInspector")} className="scroll-panel hidden flex-col gap-3 border-l border-border bg-panel p-3 md:col-start-3 md:row-span-2 md:row-start-1 md:flex">
          <CameraViews cam={cam} />
          <Placeholder region="inspector" title={t("panelInspector")} hint={t("placeholderInspector")} className="min-h-[200px] flex-1" />
        </aside>
        <Placeholder region="timeline" title={t("panelTimeline")} hint={t("placeholderTimeline")} className="hidden h-[140px] rounded-none border-x-0 border-b-0 bg-panel md:col-start-2 md:row-start-2 md:flex" />
      </div>
      <nav aria-label={t("panelLeft")} className="border-t border-border bg-panel md:hidden">
        <div role="tablist" className="sticky top-0 z-10 flex border-b border-border bg-panel">
          {(["upload", "edit", "result"] as const).map((tab) => (
            <button key={tab} role="tab" aria-selected={mobileTab === tab} onClick={() => setMobileTab(tab)}
              className={cx("min-h-[44px] flex-1 border-b-2 text-[13px]", mobileTab === tab ? "border-accent font-semibold text-accent" : "border-transparent text-muted")}>
              {t(tab)}
            </button>
          ))}
        </div>
        <div role="tabpanel">
          {mobileTab === "upload" && scenePanel}
          {mobileTab === "edit" && <div className="p-3"><Placeholder region="inspector-mobile" title={t("panelInspector")} hint={t("placeholderInspector")} className="min-h-[160px]" /></div>}
          {mobileTab === "result" && <div className="p-3"><Placeholder region="result-mobile" title={t("result")} hint={t("placeholderTimeline")} className="min-h-[160px]" /></div>}
        </div>
      </nav>
    </div>
  );
}
