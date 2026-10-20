"use client";

import type { CameraAnalyzeResult } from "@oc/contracts";
import { cx } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api/client";
import { depthToLink, latestJob, sceneToLink } from "../flow/scene";
import { useAssetUrl } from "../hooks/assets";
import { useIsDesktop } from "../hooks/media";
import { useCameraPath, type CameraPathState } from "../hooks/cameraPath";
import { usePreviewScene, useSceneBitmap } from "../hooks/previewScene";
import { useEditorShortcuts } from "../hooks/shortcuts";
import { useI18n } from "../i18n";
import { poseAtTime } from "../model/preview";
import { moveControlPoint, outputToSourceUv, sketchAtTarget, sourceToOutputUv, targetOutputUv } from "../model/sketch";
import { assetById, useEditor } from "../state/editorStore";
import { CameraGizmo } from "./CameraGizmo";
import { Inspector, MobileEdit } from "./Inspector";
import { LeftPanel, ToolPicker } from "./LeftPanel";
import { PreviewStage } from "./PreviewStage";
import { AnalyzePanel, JobStatusList } from "./ScenePanel";
import { Timeline, usePlayback } from "./Timeline";
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

const SketchOverlay = dynamic(() => import("./SketchOverlay"), { ssr: false });

function CameraStage({ cam, freehand }: { cam: CameraPathState; freehand: boolean }) {
  const { t } = useI18n();
  const project = useEditor((s) => s.project)!;
  const config = useEditor((s) => s.config)!;
  const timeMs = useEditor((s) => s.playheadMs);
  const tool = useEditor((s) => s.tool);
  const sceneAsset = assetById(project, config.scene_asset_id);
  const depth = assetById(project, config.depth_asset_id);
  const image = useSceneBitmap(sceneAsset);
  const { scene, error } = usePreviewScene(depth, config);
  const desktop = useIsDesktop();
  const { path, info, outW, outH, srcW, srcH } = cam;
  const targetUv = useMemo(() => (path && info ? targetOutputUv(path.K, poseAtTime(path.samples, timeMs), info.target_world, outW, outH) : null),
    [path, info, timeMs, outW, outH]);
  const targetStartUv = useMemo(() => (info && srcW && srcH ? sourceToOutputUv(info.target_uv01, srcW, srcH, outW, outH) : null), [info, srcW, srcH, outW, outH]);
  if (!path) return <SceneStage />;
  const { update, commitSketch } = useEditor.getState();
  const overlay = (size: { w: number; h: number }) => (
    <SketchOverlay w={size.w} h={size.h} tool={freehand ? tool : "none"} points={config.path.sketch_points}
      targetUv={targetUv} targetStartUv={targetStartUv} labels={{ sketch: t("sketch"), target: t("target") }}
      onTarget={(uvOut) => {
        if (!srcW || !srcH) return;
        const [u, v] = outputToSourceUv(uvOut, srcW, srcH, outW, outH);
        // New target: depth correction back to auto (the depth-map median under the target).
        update((c) => { c.target.u = u; c.target.v = v; c.target.depth_scale = 1; });
      }}
      onStroke={commitSketch}
      onMovePoint={(i, u, v, implicit) => update((c) => {
        const pts = implicit && targetStartUv ? sketchAtTarget(targetStartUv) : c.path.sketch_points;
        if (pts[i]) c.path.sketch_points = moveControlPoint(pts, i, u, v);
      }, `drag:${i}`)} />
  );
  return (
    <div data-region="stage" className="flex min-h-0 flex-1 flex-col gap-3 bg-stage p-3">
      <PreviewStage bitmap={image.bitmap} scene={scene} path={path} timeMs={timeMs} outW={outW} outH={outH} overlay={overlay}
        showHoles={config.scene.show_holes} provisional={cam.provisional} error={error || image.error} className="md:flex-1" />
      {/* Mobile: the gizmo stacks under the stage (desktop renders it in the right column instead). */}
      {!desktop && info && <CameraGizmo path={path} comparePath={cam.suggestedPath} targetWorld={info.target_world} timeMs={timeMs} />}
    </div>
  );
}

function CameraViews({ cam }: { cam: CameraPathState }) {
  const { t } = useI18n();
  const timeMs = useEditor((s) => s.playheadMs);
  const desktop = useIsDesktop();
  if (!desktop) return null; // the right column is hidden below md; the stage hosts the gizmo there
  if (!cam.path || !cam.info) {
    return (
      <div className="flex flex-wrap gap-3">
        <Placeholder region="view-top" title={t("viewTop")} hint={t("noDepth")} className="h-[160px] w-[220px] shrink-0" />
        <Placeholder region="view-side" title={t("viewSide")} hint={t("noDepth")} className="h-[160px] w-[220px] shrink-0" />
      </div>
    );
  }
  return <CameraGizmo path={cam.path} comparePath={cam.suggestedPath} targetWorld={cam.info.target_world} timeMs={timeMs} />;
}

export function ProjectView({ projectId }: { projectId: string }) {
  const { t } = useI18n();
  const load = useEditor((s) => s.load);
  const project = useEditor((s) => s.project);
  const config = useEditor((s) => s.config);
  const jobs = useEditor((s) => s.jobs);
  const [mobileTab, setMobileTab] = useState<MobileTab>("upload");
  // Mobile: freehand sketch is off by default (preset + strength slider + keyframe numbers instead).
  const [mobileFreehand, setMobileFreehand] = useState(false);
  const desktop = useIsDesktop();
  const cam = useCameraPath();
  useEditorShortcuts();
  usePlayback();

  // The target tool maps clicks back to the source image through the t = 0 (source) view; leaving
  // t = 0 (scrub, play) ends it.
  const tool = useEditor((s) => s.tool);
  const playheadMs = useEditor((s) => s.playheadMs);
  useEffect(() => {
    if (tool === "target" && playheadMs !== 0) useEditor.getState().setTool("none");
  }, [tool, playheadMs]);
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
  const hasDepth = !!config.depth_asset_id;
  const freehand = desktop || mobileFreehand;

  return (
    // Desktop: fixed app shell (panels scroll on their own), never shorter than 640px.
    // Mobile (<768px): stage on top, bottom sheet with Upload / Edit / Result tabs; the page scrolls.
    <div className="flex min-h-[100dvh] flex-col md:h-[100dvh] md:min-h-[640px]">
      <TopBar />
      <div className="flex min-h-0 flex-1 flex-col md:grid md:grid-cols-[240px_minmax(0,1fr)_320px] md:grid-rows-[minmax(0,1fr)_140px]">
        <aside data-region="left" aria-label={t("panelLeft")} className="scroll-panel hidden border-r border-border bg-panel md:col-start-1 md:row-span-2 md:row-start-1 md:block">
          {desktop ? <LeftPanel /> : null}
        </aside>
        <main className="flex min-h-0 min-w-0 flex-col md:col-start-2 md:row-start-1">
          {hasDepth ? <CameraStage cam={cam} freehand={freehand} /> : <SceneStage />}
        </main>
        <aside data-region="right" aria-label={t("panelInspector")} className="scroll-panel hidden flex-col gap-3 border-l border-border bg-panel p-3 md:col-start-3 md:row-span-2 md:row-start-1 md:flex">
          <CameraViews cam={cam} />
          {/* desktop only: the mobile Edit sheet hosts its own controls (no duplicate, hidden inputs) */}
          {desktop && (hasDepth && cam.path ? <Inspector cam={cam} /> : <p className="text-xs text-muted">{t("noDepth")}</p>)}
        </aside>
        <div className="hidden md:col-start-2 md:row-start-2 md:block">
          {desktop && hasDepth && cam.path ? <Timeline cam={cam} /> : <div className="h-[140px] border-t border-border bg-panel" />}
        </div>
        {!desktop && hasDepth && cam.path && <Timeline cam={cam} compact />}
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
          {mobileTab === "edit" && (
            <div className="space-y-4 p-3" data-region="inspector-mobile">
              {hasDepth && cam.path ? (
                <>
                  <MobileEdit cam={cam} />
                  <div className="space-y-2 border-t border-border pt-3">
                    <label className="flex min-h-[44px] items-center justify-between text-[13px]">
                      {t("freehandToggle")}
                      <input type="checkbox" checked={mobileFreehand} className="h-5 w-5 accent-[#66DCF2]"
                        onChange={(e) => { setMobileFreehand(e.target.checked); if (!e.target.checked) useEditor.getState().setTool("none"); }} />
                    </label>
                    {mobileFreehand ? <ToolPicker /> : <p className="text-xs text-muted">{t("sketchDesktopOnly")}</p>}
                  </div>
                </>
              ) : <p className="text-xs text-muted">{t("noDepth")}</p>}
            </div>
          )}
          {mobileTab === "result" && <p className="p-3 text-xs text-muted" data-region="result-mobile">{t("resultLater")}</p>}
        </div>
      </nav>
    </div>
  );
}
