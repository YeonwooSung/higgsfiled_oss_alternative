"use client";

/**
 * Downloads of one result job (shared §6): `POST /projects/{id}/exports {job_id, format}` → follow the
 * `camera.export` job → short-lived signed download URL (filenames are app-slug prefixed server-side).
 * Geometry: MP4 / path JSON / guides ZIP / recipe ZIP; Cinematic: MP4 / path JSON / recipe ZIP.
 */
import type { CameraExportFormat, Job } from "@oc/contracts";
import { Button } from "@oc/ui";
import { CheckCircle2, CircleAlert, Download, Loader2 } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import { useI18n } from "../i18n";
import { exportFormats } from "../model/results";
import { useEditor } from "../state/editorStore";
import { followJob } from "../state/jobs";
import { JobProgress } from "./RenderBar";

type Entry = { state: "working"; jobId: string | null } | { state: "ready"; assetId: string } | { state: "error"; message: string };

/** Fetch a fresh signed download URL (they live 5 minutes) and start the browser download. */
async function download(assetId: string) {
  const a = await api.getAsset(assetId, true);
  const url = a.download_url;
  if (!url) throw new Error("no url");
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener";
  // the signed URL carries Content-Disposition with the app-slug prefixed filename
  document.body.appendChild(link);
  link.click();
  link.remove();
}

export function ExportMenu({ job, label }: { job: Job; label: string }) {
  const { t } = useI18n();
  const projectId = useEditor((s) => s.project?.id);
  const jobs = useEditor((s) => s.jobs);
  const [entries, setEntries] = useState<Partial<Record<CameraExportFormat, Entry>>>({});
  const set = (f: CameraExportFormat, e: Entry) => setEntries((cur) => ({ ...cur, [f]: e }));
  const formats = exportFormats(job.kind);
  if (!projectId || job.state !== "succeeded" || !formats.length) return null;

  const run = async (f: CameraExportFormat) => {
    const name = t(`fmt_${f}`);
    set(f, { state: "working", jobId: null });
    try {
      const exp = await api.createExport(projectId, job.id, f);
      set(f, { state: "working", jobId: exp.id });
      const done = await followJob(exp);
      const assetId = done.output_asset_ids[0];
      if (done.state !== "succeeded" || !assetId) throw new Error(done.error?.message ?? t("downloadFailed", { format: name }));
      set(f, { state: "ready", assetId });
      await download(assetId);
    } catch (e) {
      set(f, { state: "error", message: e instanceof ApiError || e instanceof Error ? e.message : t("downloadFailed", { format: name }) });
    }
  };

  return (
    <section aria-label={`${t("downloads")} · ${label}`} className="space-y-2" data-region={`exports-${job.kind}`}>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">{t("downloads")} · {label}</h3>
      <div className="flex flex-wrap gap-2">
        {formats.map((f) => {
          const e = entries[f];
          const name = t(`fmt_${f}`);
          return (
            <Button key={f} size="sm" variant="secondary" disabled={e?.state === "working"} data-format={f}
              icon={e?.state === "working" ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                : e?.state === "ready" ? <CheckCircle2 className="h-3.5 w-3.5 text-accent" aria-hidden /> : <Download className="h-3.5 w-3.5" aria-hidden />}
              aria-label={e?.state === "ready" ? t("downloadAgain", { format: name }) : name}
              onClick={() => (e?.state === "ready" ? void download(e.assetId).catch(() => set(f, { state: "error", message: t("downloadFailed", { format: name }) })) : void run(f))}>
              {name}
            </Button>
          );
        })}
      </div>
      {formats.map((f) => {
        const e = entries[f];
        if (e?.state === "working" && e.jobId && jobs[e.jobId] && !["succeeded", "failed", "cancelled", "submission_unknown"].includes(jobs[e.jobId].state)) {
          return <JobProgress key={f} job={jobs[e.jobId]} label={t("downloadPreparing", { format: t(`fmt_${f}`) })} />;
        }
        if (e?.state === "error") {
          return (
            <p key={f} role="alert" className="flex items-start gap-1.5 text-xs text-danger">
              <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>{t("downloadFailed", { format: t(`fmt_${f}`) })} {e.message}</span>
            </p>
          );
        }
        return null;
      })}
    </section>
  );
}
