"use client";

/** Shared pieces of the job buttons: native-dialog Modal, job progress with Cancel, reasons in words, error lines. */
import type { Job } from "@oc/contracts";
import { Button, cx } from "@oc/ui";
import { CircleAlert, Loader2, TriangleAlert, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import type { Reason } from "../hooks/jobs";
import { useI18n } from "../i18n";
import { en, type Dict } from "../i18n/en";
import { useEditor } from "../state/editorStore";

export type T = (k: keyof Dict, vars?: Record<string, string | number>) => string;

const has = (k: string): k is keyof Dict => Object.prototype.hasOwnProperty.call(en, k);

/** Words for a reason code; the server's text for codes the UI does not know. */
export function reasonText(r: Reason, t: T): string {
  const k = `why_${r.code}`;
  return has(k) ? t(k) : r.message ?? t("why_generic");
}

export function Reasons({ reasons, lead, tone = "muted" }: { reasons: Reason[]; lead?: string; tone?: "muted" | "warning" }) {
  const { t } = useI18n();
  if (!reasons.length) return null;
  return (
    <div role="status" className={cx("text-xs", tone === "warning" ? "text-warning" : "text-muted")} data-region="reasons">
      {lead && <p className="font-medium">{lead}</p>}
      <ul className="mt-0.5 space-y-0.5">
        {reasons.map((r) => (
          <li key={r.code + (r.message ?? "")} className="flex gap-1.5">
            <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
            <span>{reasonText(r, t)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Modal on the native `<dialog>` (focus trap, Escape closes). */
export function Modal({ open, title, onClose, children, actions }: {
  open: boolean; title: string; onClose: () => void; children: ReactNode; actions: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} onClose={onClose} onCancel={(e) => { e.preventDefault(); onClose(); }} aria-labelledby={titleId}
      className="w-[min(440px,calc(100vw-32px))] rounded-panel border border-border bg-panel p-5 text-ink shadow-lg backdrop:bg-black/60">
      {open && (
        <>
          <h2 id={titleId} className="mb-3 text-[15px] font-semibold">{title}</h2>
          <div className="space-y-2 text-[13px]">{children}</div>
          <div className="mt-5 flex flex-wrap justify-end gap-2">{actions}</div>
        </>
      )}
    </dialog>
  );
}

const STAGE_KEYS = new Set(["track", "masks", "plate", "matte", "layers", "composite", "encode"]);

export function stageText(job: Job, t: T): string {
  if (job.state === "cancel_requested") return t("cancelRequested");
  if (job.state === "queued") return t("jobQueued");
  return job.stage && STAGE_KEYS.has(job.stage) ? t(`stage_${job.stage}` as keyof Dict) : t("jobRunning");
}

/** Stage name, percent when the API gives one, and Cancel while it runs. */
export function JobProgress({ job, label }: { job: Job; label: string }) {
  const { t } = useI18n();
  const [error, setError] = useState<string | null>(null);
  const pct = job.progress !== null && Number.isFinite(job.progress) ? Math.max(0, Math.min(99, Math.round(job.progress * 100))) : null;
  const cancelling = job.state === "cancel_requested";
  return (
    <div className="flex min-w-0 items-center gap-2 text-xs" data-region="job-progress">
      <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" aria-hidden />
      <span className="min-w-0 flex-1 truncate" role="status" aria-live="polite">
        <span className="font-medium">{label} · </span>{stageText(job, t)}
        {pct !== null && <span className="tabular"> · {pct}%</span>}
      </span>
      {!cancelling && (
        <Button size="sm" variant="ghost" aria-label={t("cancelJob")} title={t("cancelJob")} className="min-w-[44px] px-2 md:min-w-0"
          icon={<X className="h-3.5 w-3.5" aria-hidden />}
          onClick={async () => {
            setError(null);
            try {
              useEditor.getState().trackJob(await api.cancelJob(job.id));
            } catch (e) {
              setError(e instanceof ApiError ? e.message : t("genericError"));
            }
          }} />
      )}
      {error && <span role="alert" className="text-danger">{error}</span>}
    </div>
  );
}

export function ErrorLine({ text }: { text: string | null }) {
  if (!text) return null;
  return <p role="alert" className="flex items-start gap-1.5 text-xs text-danger"><CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /><span>{text}</span></p>;
}

/** Text of a finished job that did not succeed (null when it succeeded). */
export function jobFailure(job: Job, name: string, t: T): string | null {
  if (job.state === "succeeded" || job.state === "cancelled") return null;
  return t("jobFailed", { name, message: job.error?.message ?? t("genericError") });
}
