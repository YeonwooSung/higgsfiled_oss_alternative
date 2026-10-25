"use client";

/**
 * Downloads of one render (shared §6): `POST /projects/{id}/exports {job_id, format}` → follow the `vfx.export` job →
 * a short-lived signed download URL (filenames are app-slug prefixed server-side). MP4 and cover return the render's
 * own assets; the recipe ZIP and the layers ZIP are built by the export job. A previous-version render keeps its
 * downloads (shared §9).
 */
import type { Job, VfxExportFormat } from "@oc/contracts";
import { Button } from "@oc/ui";
import { CheckCircle2, Download, Loader2 } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import { followJob, isActive } from "../hooks/jobs";
import { useI18n } from "../i18n";
import { EXPORT_FORMATS } from "../model/results";
import { useEditor } from "../state/editorStore";
import { ErrorLine, JobProgress } from "./JobUi";

type Entry = { state: "working"; jobId: string | null } | { state: "ready"; assetId: string } | { state: "error"; message: string };

class NoUrl extends Error {}
/** The export job ended without a file; its message is the server's (user-facing) job error. */
class ExportFailed extends Error {}

/** Fresh signed download URL (they live 5 minutes), then the browser download. */
async function download(assetId: string) {
  const url = (await api.getAsset(assetId, true)).download_url;
  if (!url) throw new NoUrl();
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

export function ExportMenu({ job }: { job: Job }) {
  const { t } = useI18n();
  const projectId = useEditor((s) => s.project?.id);
  const jobs = useEditor((s) => s.jobs);
  const [entries, setEntries] = useState<Partial<Record<VfxExportFormat, Entry>>>({});
  const set = (f: VfxExportFormat, e: Entry) => setEntries((cur) => ({ ...cur, [f]: e }));
  if (!projectId || job.state !== "succeeded") return null;

  const failure = (e: unknown) =>
    e instanceof NoUrl ? t("downloadNoUrl") : e instanceof ApiError || e instanceof ExportFailed ? e.message : t("genericError");

  const run = async (f: VfxExportFormat) => {
    set(f, { state: "working", jobId: null });
    try {
      const exp = await api.createExport(projectId, job.id, f);
      set(f, { state: "working", jobId: exp.id });
      const done = await followJob(exp);
      const assetId = done.output_asset_ids[0];
      if (done.state !== "succeeded" || !assetId) throw new ExportFailed(done.error?.message ?? t("genericError"));
      set(f, { state: "ready", assetId });
      await download(assetId);
    } catch (e) {
      set(f, { state: "error", message: failure(e) });
    }
  };

  return (
    <section aria-labelledby={`dl-${job.id}`} className="flex flex-col gap-2" data-region="exports">
      <h3 id={`dl-${job.id}`} className="text-xs font-semibold uppercase tracking-wide text-muted">{t("downloads")}</h3>
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {EXPORT_FORMATS.map((f) => {
          const e = entries[f];
          const name = t(`fmt_${f}`);
          const exp = e?.state === "working" && e.jobId ? jobs[e.jobId] : undefined;
          return (
            <li key={f} className="flex flex-col gap-1 rounded-card border border-border bg-app p-2">
              <Button size="sm" variant="secondary" disabled={e?.state === "working"} data-format={f} className="min-h-[44px] justify-start"
                icon={e?.state === "working" ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  : e?.state === "ready" ? <CheckCircle2 className="h-3.5 w-3.5 text-tracking" aria-hidden /> : <Download className="h-3.5 w-3.5" aria-hidden />}
                aria-label={e?.state === "ready" ? t("downloadAgain", { format: name }) : name}
                onClick={() => (e?.state === "ready"
                  ? void download(e.assetId).catch((err) => set(f, { state: "error", message: failure(err) }))
                  : void run(f))}>
                {name}
              </Button>
              <p className="text-[11px] leading-snug text-muted">{t(`fmtHint_${f}`)}</p>
              {exp && isActive(exp) && <JobProgress job={exp} label={t("downloadPreparing", { format: name })} />}
              {e?.state === "error" && <ErrorLine text={`${t("downloadFailed", { format: name })} ${e.message}`} />}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
