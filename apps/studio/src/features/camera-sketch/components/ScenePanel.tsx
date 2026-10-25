"use client";

import { TERMINAL_JOB_STATES, type Job } from "@oc/contracts";
import { Button } from "@oc/ui";
import { CheckCircle2, CircleAlert, Loader2, ScanSearch, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { analysisState, jobLabelKey, latestJob } from "../flow/scene";
import { useI18n } from "../i18n";
import { en, type Dict } from "../i18n/en";
import { useEditor } from "../state/editorStore";
import { startJob } from "../state/jobs";
import { ProvenanceBadge } from "../../../shell/ProvenanceBadge";
import { errorText } from "./errors";

export function jobLabel(job: Job, t: (k: keyof Dict) => string): string {
  return t(jobLabelKey(job.state, (k) => k in en) as keyof Dict);
}

/**
 * Analyze scene: runs `camera.analyze` only (depth). No generation job is created from here. Shows where a handed-off
 * scene came from.
 */
export function AnalyzePanel() {
  const { t } = useI18n();
  const config = useEditor((s) => s.config)!;
  const jobs = useEditor((s) => s.jobs);
  const [error, setError] = useState<string | null>(null);
  const latest = latestJob(jobs, "camera.analyze");
  const state = analysisState(config, latest);
  const saveCode = useEditor((s) => s.saveCode);
  const handoff = useEditor((s) => s.project?.handoff);

  const run = async () => {
    setError(null);
    try {
      if (useEditor.getState().saveCode === "depth_stale") {
        // Drop the stale depth link locally, then re-run; startJob flushes the cleaned config first.
        useEditor.getState().link((c) => { c.depth_asset_id = null; });
        useEditor.getState().clearSaveError();
      }
      await startJob("camera.analyze");
    } catch (e) {
      setError(errorText(e, t));
    }
  };

  return (
    <div className="space-y-3">
      {/* the scene came from another tool's result */}
      <ProvenanceBadge handoff={handoff} />
      <Button variant="primary" className="w-full" onClick={run} disabled={state === "no_scene" || state === "running"}
        icon={state === "running" ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <ScanSearch className="h-4 w-4" aria-hidden />}>
        {state === "running" ? t("analyzing") : state === "failed" ? t("retry") : t("analyzeScene")}
      </Button>
      <p className="text-xs text-muted">{t("analyzeNoCost")}</p>
      <div role="status" aria-live="polite" className="text-xs">
        {state === "done" && <span className="flex items-center gap-1 text-accent"><CheckCircle2 className="h-3.5 w-3.5" aria-hidden />{t("analyzeDone")}</span>}
        {state === "failed" && <span className="flex items-center gap-1 text-danger"><CircleAlert className="h-3.5 w-3.5" aria-hidden />{t("analyzeFailed")}</span>}
      </div>
      {saveCode === "depth_stale" && (
        <div role="alert" className="space-y-2 text-xs text-danger">
          <p className="flex items-start gap-1.5"><CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /><span>{t("errorLabel")}: {t("depthStale")}</span></p>
          <Button size="sm" variant="secondary" onClick={run} disabled={state === "running"}>{t("reanalyze")}</Button>
        </div>
      )}
      {error && <p role="alert" className="flex items-start gap-1.5 text-xs text-danger"><CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /><span>{t("errorLabel")}: {error}</span></p>}
    </div>
  );
}

export function JobStatusList() {
  const { t } = useI18n();
  const [cancelError, setCancelError] = useState<string | null>(null);
  const jobs = useEditor((s) => s.jobs);
  const list = Object.values(jobs).filter((j) => j.kind !== "asset.inspect").sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).slice(0, 5);
  return (
    <section aria-label={t("jobStatus")}>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{t("jobStatus")}</h2>
      {!list.length && <p className="text-xs text-muted">{t("noJobs")}</p>}
      <ul className="space-y-1.5">
        {list.map((j) => {
          const active = !TERMINAL_JOB_STATES.includes(j.state) && j.state !== "cancelled";
          return (
            <li key={j.id} className="flex items-center justify-between gap-2 rounded-button border border-border px-2 py-1.5 text-xs">
              <div>
                <p className="font-medium">{j.kind}</p>
                <p className="flex items-center gap-1 text-muted">
                  {j.state === "failed" ? <CircleAlert className="h-3 w-3 text-danger" aria-hidden /> : j.state === "succeeded" ? <CheckCircle2 className="h-3 w-3 text-accent" aria-hidden /> : null}
                  {jobLabel(j, t)}
                  {/* percent only when the provider reports real progress; never 100% before success */}
                  {active && j.progress !== null && ` · ${Math.min(99, Math.round(j.progress * 100))}%`}
                </p>
              </div>
              {active && j.state !== "cancel_requested" && (
                <Button size="sm" variant="ghost" aria-label={t("cancelJob")} icon={<X className="h-3.5 w-3.5" />}
                  onClick={async () => {
                    setCancelError(null);
                    try {
                      useEditor.getState().trackJob(await api.cancelJob(j.id));
                    } catch (e) {
                      setCancelError(errorText(e, t));
                    }
                  }} />
              )}
            </li>
          );
        })}
      </ul>
      {cancelError && <p role="alert" className="mt-2 flex items-start gap-1.5 text-xs text-danger"><CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /><span>{t("errorLabel")}: {cancelError}</span></p>}
    </section>
  );
}
