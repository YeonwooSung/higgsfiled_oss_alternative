"use client";

import type { Integrity } from "@oc/contracts";
import { Badge, Button, Segmented } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { Download, ScanSearch, ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { useAssetUrl } from "../hooks/assets";
import { useI18n } from "../i18n";
import { assetById, useEditor } from "../state/editorStore";
import { followJob, startJob } from "../state/jobs";
import { errorText } from "./errors";
import { ShareLink } from "./ShareView";

function ProtectionBadge({ integrity }: { integrity: Integrity }) {
  const { t } = useI18n();
  if (!integrity.core_valid) return <Badge tone="warning" icon={<ShieldAlert className="h-3.5 w-3.5" />}>{t("protectionInvalid")}</Badge>;
  return integrity.passed
    ? <Badge tone="success" icon={<ShieldCheck className="h-3.5 w-3.5" />}>{t("protectionVerified")}</Badge>
    : <Badge tone="danger" icon={<ShieldAlert className="h-3.5 w-3.5" />}>{t("protectionFailed")}</Badge>;
}

export function ResultView() {
  const { t } = useI18n();
  const { project, jobs, selectedRenderJobId, revision } = useEditor();
  const job = selectedRenderJobId ? jobs[selectedRenderJobId] : undefined;
  const [mode, setMode] = useState<"result" | "original">("result");
  const [inspection, setInspection] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const integrity = useQuery({ queryKey: ["integrity", job?.id], queryFn: () => api.integrity(job!.id), enabled: !!job });
  const video = assetById(project, job?.output_asset_ids[0]);
  const cover = assetById(project, job?.output_asset_ids[1]);
  const product = assetById(project, project?.config.product_asset_id);
  const videoUrl = useAssetUrl(video);
  const coverUrl = useAssetUrl(cover);
  const productUrl = useAssetUrl(product);
  const inspectionUrl = useAssetUrl(assetById(project, inspection));
  if (!job || !project) return null;

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(null);
    }
  };

  const download = (format: "mp4" | "cover_png" | "recipe_zip") => run(format, async () => {
    const exp = await followJob(await api.createExport(project.id, job.id, format));
    if (exp.state !== "succeeded") throw new Error(exp.error?.message);
    const asset = await api.getAsset(exp.output_asset_ids[0], true);
    if (asset.download_url) window.location.assign(asset.download_url);
  });

  const m = integrity.data?.postencode_metrics;
  return (
    // lg+: media column fills the stage height, details column scrolls by itself.
    // Narrower: stacked, and the stage area scrolls.
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 lg:h-full lg:flex-row">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col items-center gap-3">
        <div className="w-56 shrink-0"><Segmented label="compare" value={mode} onChange={setMode}
          options={[{ value: "result", label: t("result") }, { value: "original", label: t("original") }]} /></div>
        <div className="flex h-[min(56dvh,560px)] w-full min-h-0 items-center justify-center lg:h-auto lg:flex-1">
          {mode === "result"
            ? videoUrl && <video data-stage-video src={videoUrl} poster={coverUrl} controls muted autoPlay loop playsInline className="h-full max-h-full w-auto max-w-full rounded-[4px] object-contain shadow" />
            // eslint-disable-next-line @next/next/no-img-element
            : productUrl && <img src={productUrl} alt={t("original")} className="checker h-full max-h-full w-auto max-w-full rounded-[4px] object-contain" />}
        </div>
      </div>
      <aside className="scroll-panel w-full shrink-0 space-y-4 rounded-panel border border-border bg-panel p-4 lg:max-h-full lg:w-80">
        <div className="flex flex-wrap gap-2">
          {job.input_revision < revision && <Badge>{t("previousVersion")}</Badge>}
          {job.model_profile === "mock_v1" && <Badge tone="mock" title={t("mockExplain")}>{t("mock")}</Badge>}
        </div>
        {integrity.data && (
          <div className="space-y-2 text-xs">
            <ProtectionBadge integrity={integrity.data} />
            <p className="tabular-nums text-muted">
              {integrity.data.frames_checked} frames · max error {integrity.data.preencode_max_error ?? "—"} / {integrity.data.threshold}
            </p>
            <div className="border-t border-border pt-2">
              <Badge tone={integrity.data.output_check.ok ? "neutral" : "warning"}>{t("compressionCheck")}</Badge>
              <p className="mt-1 tabular-nums text-muted">
                SSIM {m?.ssim_mean?.toFixed(3) ?? "—"} · mean error {m?.mean_abs_error?.toFixed(2) ?? "—"}
              </p>
              <p className="text-muted">{t("compressionNote")}</p>
            </div>
          </div>
        )}
        <div className="space-y-2">
          <Button size="sm" variant="secondary" icon={<ScanSearch className="h-4 w-4" />} disabled={!!busy}
            onClick={() => run("inspect", async () => {
              const r = await startJob("product.inspect", { render_job_id: job.id });
              if (r.state === "succeeded") setInspection(r.output_asset_ids[0]);
            })}>{t("labelZoom")}</Button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {inspectionUrl && <img src={inspectionUrl} alt={t("inspectCore")} className="w-full rounded-button border border-border" />}
        </div>
        <div className="flex flex-wrap gap-2 border-t border-border pt-3">
          <Button size="sm" variant="primary" icon={<Download className="h-4 w-4" />} disabled={!!busy} onClick={() => download("mp4")}>{t("downloadMp4")}</Button>
          <Button size="sm" variant="secondary" disabled={!!busy} onClick={() => download("cover_png")}>{t("downloadCover")}</Button>
          <Button size="sm" variant="secondary" disabled={!!busy} onClick={() => download("recipe_zip")}>{t("downloadRecipe")}</Button>
        </div>
        <div className="border-t border-border pt-3">
          <ShareLink key={video?.id} assetId={video?.id} />
        </div>
        {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      </aside>
    </div>
  );
}
