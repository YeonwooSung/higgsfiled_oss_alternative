"use client";

import { Segmented } from "@oc/ui";
import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import { cutoutCanvas, maskFromImage, useAssetUrl, useImage } from "../hooks/assets";
import { useI18n } from "../i18n";
import { MaskBuffer } from "../model/mask";
import { assetById, assetsByRole, useEditor, type View } from "../state/editorStore";
import { useMask } from "../state/maskStore";
import { MaskStage } from "./MaskStage";
import { ResultView } from "./ResultView";

const ComposeStage = dynamic(() => import("./ComposeStage"), { ssr: false });

function useBox() {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return { ref, size };
}

/** Background video that matches the chosen background, aspect and motion (reusable for renders). */
export function useMatchingBackgroundVideo() {
  const project = useEditor((s) => s.project);
  const config = useEditor((s) => s.config);
  return assetsByRole(project, "background_video").filter((v) =>
    v.source_asset_id === config?.scene.background_asset_id && v.meta.motion === config?.motion).at(-1);
}

export function StageArea() {
  const { t } = useI18n();
  const { project, config, view, setView, zoom, setZoom, showGrid, toggleGrid, update, setNotice, notice } = useEditor();
  const [showCore, setShowCore] = useState(true);
  const { ref, size } = useBox();
  const mask = useMask();

  const productAsset = assetById(project, config!.product_asset_id);
  const maskAsset = assetById(project, config!.mask_asset_id);
  const background = assetById(project, config!.scene.background_asset_id);
  const video = useMatchingBackgroundVideo();
  const productImg = useImage(useAssetUrl(productAsset));
  const maskImg = useImage(useAssetUrl(maskAsset));
  const bgImg = useImage(useAssetUrl(background));
  const videoUrl = useAssetUrl(video);

  // Load the confirmed mask into the editable buffer (never over unsaved strokes).
  useEffect(() => {
    if (mask.dirty) return;
    if (maskImg && maskAsset && mask.sourceAssetId !== maskAsset.id) mask.setBuffer(maskAsset.id, maskFromImage(maskImg));
    else if (!maskAsset && productImg && !mask.buffer)
      mask.setBuffer(null, new MaskBuffer(productImg.naturalWidth, productImg.naturalHeight));
  }, [maskImg, maskAsset, productImg, mask]);

  const cutout = useMemo(
    // Before a mask exists (and nothing was painted) the product shows with its own alpha.
    () => (productImg ? cutoutCanvas(productImg, maskAsset || mask.dirty ? mask.buffer : null) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [productImg, mask.buffer, mask.version, maskAsset?.id],
  );

  const [outW, outH] = config!.output.aspect === "9:16" ? [720, 1280] : [1280, 720];
  const pad = 32;
  const fit = Math.max(0.05, Math.min((size.w - pad) / outW, (size.h - pad) / outH));
  const scale = zoom === "fit" ? fit : zoom;

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-stage">
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-panel/70 px-3 py-2">
        <div className="w-60">
          <Segmented<View> label="view" value={view} onChange={setView} options={[
            { value: "compose", label: t("output") },
            { value: "mask", label: t("product") },
            { value: "result", label: t("result"), disabled: !useEditor.getState().selectedRenderJobId },
          ]} />
        </div>
        {view === "compose" && (
          <>
            <div className="w-48">
              <Segmented label="zoom" value={String(zoom)} onChange={(z) => setZoom(z === "fit" ? "fit" : (Number(z) as 1 | 2))}
                options={[{ value: "fit", label: t("fit") }, { value: "1", label: t("zoom100") }, { value: "2", label: t("zoom200") }]} />
            </div>
            <label className="flex items-center gap-1.5 text-xs text-muted">
              <input type="checkbox" checked={showCore} onChange={(e) => setShowCore(e.target.checked)} className="accent-[#18794E]" />
              {t("protectedLabel")}
            </label>
          </>
        )}
        {view === "mask" && (
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <input type="checkbox" checked={showGrid} onChange={toggleGrid} className="accent-[#18794E]" />
            {t("transparentGrid")}
          </label>
        )}
        {notice && (
          <button type="button" onClick={() => setNotice(null)} className="ml-auto rounded-button bg-amber-50 px-2 py-1 text-xs text-amber-900">
            {notice} ✕
          </button>
        )}
      </div>
      <div ref={ref} className={`relative min-h-0 flex-1 overflow-auto ${showGrid && view === "mask" ? "checker" : ""}`}>
        <div className="flex min-h-full min-w-full items-center justify-center p-4">
          {view === "result" ? (
            <ResultView />
          ) : view === "mask" ? (
            productImg ? (
              <MaskStage product={productImg} width={Math.max(100, size.w - pad)} height={Math.max(100, size.h - pad)}
                protectedLabel={t("protectedLabel")} onClipped={() => setNotice(t("boxClipped"))} />
            ) : <p className="text-muted">{t("inspecting")}</p>
          ) : (
            <div className="relative" style={{ width: outW * scale, height: outH * scale }}>
              {videoUrl && (
                <video data-stage-video src={videoUrl} muted loop playsInline autoPlay
                  className="absolute inset-0 h-full w-full object-cover" />
              )}
              <div className="absolute inset-0">
                <ComposeStage config={config!} cutout={cutout} background={bgImg} hasVideo={!!videoUrl} scale={scale}
                  showCore={showCore} draftLabel={t("draft")} protectedLabel={t("protectedLabel")}
                  onMove={(cx, cy) => update((c) => { c.transform.center_x = cx; c.transform.center_y = cy; })} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
