"use client";

import { TERMINAL_JOB_STATES, type Job } from "@oc/contracts";
import { Button } from "@oc/ui";
import { Loader2, X } from "lucide-react";
import { api } from "../api/client";
import { useI18n } from "../i18n";
import type { Dict } from "../i18n/en";
import { useEditor } from "../state/editorStore";

export function jobLabel(job: Job, t: (k: keyof Dict) => string): string {
  if (job.state === "cancel_requested") return t("cancelRequested");
  if (job.state === "queued") return t("jobQueued");
  const key = `stage_${job.state}` as keyof Dict;
  return t(key) ?? t("jobRunning");
}

export function JobIndicator() {
  const { t } = useI18n();
  const jobs = useEditor((s) => s.jobs);
  const active = Object.values(jobs).filter((j) => !TERMINAL_JOB_STATES.includes(j.state) && j.kind !== "asset.inspect");
  if (!active.length) return null;
  return (
    <details className="relative">
      <summary className="flex h-8 cursor-pointer list-none items-center gap-1.5 rounded-button px-2 text-xs text-muted hover:bg-app">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" aria-hidden />
        {active.length}
      </summary>
      <div className="absolute right-0 z-20 mt-1 w-72 rounded-panel border border-border bg-panel p-2 shadow-lg">
        {active.map((j) => (
          <div key={j.id} className="flex items-center justify-between gap-2 px-2 py-1.5 text-xs">
            <div>
              <p className="font-medium">{j.kind.replace("product.", "")}</p>
              <p className="text-muted" aria-live="polite">
                {jobLabel(j, t)}
                {/* percent only when the provider reports real progress; never 100% before success */}
                {j.progress !== null && j.state !== "succeeded" && ` · ${Math.min(99, Math.round(j.progress * 100))}%`}
              </p>
            </div>
            {j.state !== "cancel_requested" && (
              <Button size="sm" variant="ghost" aria-label={t("cancelJob")} icon={<X className="h-3.5 w-3.5" />}
                onClick={async () => useEditor.getState().trackJob(await api.cancelJob(j.id))} />
            )}
          </div>
        ))}
      </div>
    </details>
  );
}
