"use client";

/**
 * Render actions (spec §2, §12; shared §7, §9): "Render Geometry" (`camera.guide`, CPU) and
 * "Generate Cinematic" (`camera.cinematic` on the newest succeeded, non-stale guide). Nothing runs
 * without an explicit click; Cinematic always shows the estimate first, Geometry asks for a confirmation
 * when the approved path has warnings (holes > 8 %, target visibility < 90 %, compiler warnings).
 */
import { TERMINAL_JOB_STATES, type CameraSketchConfig, type Estimate, type Job } from "@oc/contracts";
import { Badge, Button } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { Box, CircleAlert, Loader2, Sparkles, TriangleAlert, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import { useRenderGate, type CameraPathState } from "../hooks/cameraPath";
import { useI18n } from "../i18n";
import type { Dict } from "../i18n/en";
import { cinematicGuide, geometryHash, geometryWarnings, progressPercent, renderError, stageKey, type GuideReason } from "../model/results";
import { reasonKey } from "../model/sketch";
import { useEditor } from "../state/editorStore";
import { followJob } from "../state/jobs";

type T = (k: keyof Dict, vars?: Record<string, string | number>) => string;

/** Modal on the native `<dialog>` (focus trap, Escape closes). */
export function Modal({ open, title, onClose, children, actions }: {
  open: boolean; title: string; onClose: () => void; children: ReactNode; actions: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} onClose={onClose} onCancel={(e) => { e.preventDefault(); onClose(); }} aria-labelledby="modal-title"
      className="w-[min(440px,calc(100vw-32px))] rounded-panel border border-border bg-panel p-5 text-ink shadow-lg backdrop:bg-black/60">
      {open && (
        <>
          <h2 id="modal-title" className="mb-3 text-[15px] font-semibold">{title}</h2>
          <div className="space-y-2 text-[13px]">{children}</div>
          <div className="mt-5 flex flex-wrap justify-end gap-2">{actions}</div>
        </>
      )}
    </dialog>
  );
}

/** sha256 of the current config's geometry (same canonical JSON as the API), null while computing. */
export function useGeometryHash(config: CameraSketchConfig | null): string | null {
  const [state, setState] = useState<{ key: string; hash: string } | null>(null);
  const key = config ? JSON.stringify(config) : "";
  useEffect(() => {
    if (!config) return;
    let live = true;
    void geometryHash(config).then((hash) => { if (live) setState({ key, hash }); }, () => {});
    return () => { live = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return state && state.key === key ? state.hash : null;
}

export const isActive = (j: Job) => !TERMINAL_JOB_STATES.includes(j.state);

/** Newest render job (guide or cinematic) that is still running, if any. */
export function activeRender(jobs: Record<string, Job>): Job | undefined {
  return Object.values(jobs).filter((j) => (j.kind === "camera.guide" || j.kind === "camera.cinematic") && isActive(j))
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0];
}

/** Stage name + percent (only when the API gives one) or a spinner, and Cancel while it runs. */
export function JobProgress({ job, label }: { job: Job; label?: string }) {
  const { t } = useI18n();
  const [error, setError] = useState<string | null>(null);
  const pct = progressPercent(job);
  const cancelling = job.state === "cancel_requested";
  return (
    <div className="flex min-w-0 items-center gap-2 text-xs" data-region="job-progress">
      <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" aria-hidden />
      <span className="min-w-0 truncate" role="status" aria-live="polite">
        {label && <span className="font-medium">{label} · </span>}
        {cancelling ? t("cancelRequested") : job.state === "queued" ? t("jobQueued") : t(stageKey(job.stage))}
        {pct !== null && <span className="tabular"> · {pct}%</span>}
      </span>
      {pct !== null && (
        <span className="hidden h-1.5 w-20 shrink-0 overflow-hidden rounded-full bg-border sm:block" aria-hidden>
          <span className="block h-full bg-accent" style={{ width: `${pct}%` }} />
        </span>
      )}
      {!cancelling && (
        <Button size="sm" variant="ghost" aria-label={t("cancelJob")} icon={<X className="h-3.5 w-3.5" />}
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

const GUIDE_REASON_KEY: Record<GuideReason, keyof Dict> = {
  guide_missing: "guide_missing", guide_running: "guide_running", guide_stale: "guide_stale",
  frame_count_mismatch: "guide_frame_count_mismatch", hash_pending: "guide_hash_pending",
};

function apiErrorText(e: unknown, t: T): string {
  if (e instanceof ApiError) {
    const k = renderError(e.code) as keyof Dict | null;
    return k ? t(k) : e.message;
  }
  return t("genericError");
}

function EstimateBody({ estimate, t }: { estimate: Estimate; t: T }) {
  const p = estimate.profile;
  return (
    <>
      <p className="flex flex-wrap items-center gap-2">
        <span className="text-muted">{t("estimateProfile")}:</span> {p.label}
        {p.is_mock && <Badge tone="mock" title={t("mockExplain")}>{t("mock")}</Badge>}
      </p>
      <p className="text-muted">{t("estimateStages", { value: estimate.stages.map((s) => t(stageKey(s))).join(" → ") })}</p>
      {estimate.uses_gpu
        ? <p className="tabular">{t("estimateGpuCap", { units: estimate.reserved_units, unit: estimate.unit.replace(/_/g, " ") })}</p>
        : <p>{t("estimateNoGpu")}</p>}
      <p>{estimate.price === null || estimate.price_status === "unknown" ? t("estimatePriceUnknown") : t("estimatePrice", { value: estimate.price })}</p>
      {!p.available && (
        <p role="alert" className="flex items-start gap-1.5 text-danger">
          <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>{t("estimateUnavailable", { reason: p.reason ?? t("err_profile_unavailable") })}</span>
        </p>
      )}
      <p className="text-xs text-muted">{t("estimateExplicit")}</p>
    </>
  );
}

export function RenderBar({ cam, onDone, compact }: { cam: CameraPathState; onDone?: (job: Job) => void; compact?: boolean }) {
  const { t } = useI18n();
  const config = useEditor((s) => s.config);
  const project = useEditor((s) => s.project);
  const jobs = useEditor((s) => s.jobs);
  const gate = useRenderGate();
  const hash = useGeometryHash(config);
  const caps = useQuery({ queryKey: ["caps"], queryFn: () => api.cameraCapabilities(), staleTime: 60_000 });
  const profile = caps.data?.profiles.find((p) => p.id === config?.model_profile);
  const [confirm, setConfirm] = useState<string[] | null>(null);
  const [estimate, setEstimate] = useState<{ estimate: Estimate; guideId: string } | null>(null);
  const [pending, setPending] = useState<"guide" | "cinematic" | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!config || !project) return null;

  const running = activeRender(jobs);
  const warnings = cam.provisional ? [] : geometryWarnings(cam.path, cam.holeRatio);
  const { guide, reason } = cinematicGuide(jobs, hash);
  const profileDown = profile && !profile.available ? profile : null;
  const canGeometry = gate.canRender && !running && !pending;
  const canCinematic = gate.canRender && !!guide && !running && !pending && !profileDown;

  const submit = async (kind: "camera.guide" | "camera.cinematic", options: Record<string, unknown> = {}) => {
    setError(null);
    setPending(kind === "camera.guide" ? "guide" : "cinematic");
    let job: Job;
    try {
      // Save pending edits first so the job pins the revision on screen (same as startJob).
      const revision = await useEditor.getState().flush();
      job = await api.createJob(project.id, kind, revision, options, crypto.randomUUID());
    } catch (e) {
      setError(apiErrorText(e, t));
      setPending(null);
      return;
    }
    setPending(null); // from here the store's job drives the progress row
    const done = await followJob(job);
    if (done.state === "succeeded") onDone?.(done);
    else if (done.state === "failed" || done.state === "submission_unknown") setError(done.error?.message ?? t("genericError"));
  };

  const onGeometry = () => (warnings.length ? setConfirm(warnings) : void submit("camera.guide"));
  const onCinematic = async () => {
    if (!guide) return;
    setError(null);
    setPending("cinematic");
    try {
      const revision = await useEditor.getState().flush();
      setEstimate({ estimate: await api.estimate(project.id, "camera.cinematic", revision, { guide_job_id: guide.id }), guideId: guide.id });
    } catch (e) {
      setError(apiErrorText(e, t));
    } finally {
      setPending(null);
    }
  };

  const cinematicHint = !gate.canRender ? null
    : profileDown ? t("estimateUnavailable", { reason: profileDown.reason ?? t("err_profile_unavailable") })
    : reason ? t(GUIDE_REASON_KEY[reason]) : null;

  return (
    <div data-region="render-bar" className={compact ? "space-y-2" : "flex flex-wrap items-center gap-x-3 gap-y-2"}>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" disabled={!canGeometry} onClick={onGeometry} title={t("renderGeometryHint")}
          icon={pending === "guide" ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <Box className="h-3.5 w-3.5 text-path" aria-hidden />}>
          {t("renderGeometry")}
        </Button>
        <Button size="sm" variant="secondary" disabled={!canCinematic} onClick={() => void onCinematic()} title={t("cinematicHint")}
          icon={pending === "cinematic" ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <Sparkles className="h-3.5 w-3.5 text-generated" aria-hidden />}>
          {t("generateCinematic")}
        </Button>
      </div>
      <div className="min-w-0 flex-1 text-xs text-muted">
        {running ? <JobProgress job={running} label={running.kind === "camera.guide" ? t("tabGeometry") : t("tabCinematic")} />
          : !gate.canRender ? <p role="status">{t("renderBlocked")} {gate.blockReasons.map((r) => t(reasonKey(r))).join(" ")}</p>
          : cinematicHint ? <p role="status" className={profileDown ? "text-danger" : undefined}>{cinematicHint}</p>
          : <p>{t("renderGeometryHint")}</p>}
        {error && <p role="alert" className="mt-1 flex items-start gap-1.5 text-danger"><CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /><span>{t("errorLabel")}: {error}</span></p>}
      </div>

      <Modal open={!!confirm} title={t("warnConfirmTitle")} onClose={() => setConfirm(null)}
        actions={<>
          <Button variant="ghost" onClick={() => setConfirm(null)}>{t("cancel")}</Button>
          <Button variant="primary" onClick={() => { setConfirm(null); void submit("camera.guide"); }}>{t("renderAnyway")}</Button>
        </>}>
        <p>{t("warnConfirmBody")}</p>
        <ul className="space-y-1">
          {(confirm ?? []).map((r) => (
            <li key={r} className="flex items-start gap-1.5 text-target"><TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />{t(reasonKey(r))}</li>
          ))}
        </ul>
      </Modal>

      <Modal open={!!estimate} title={t("estimateTitle")} onClose={() => setEstimate(null)}
        actions={<>
          <Button variant="ghost" onClick={() => setEstimate(null)}>{t("cancel")}</Button>
          <Button variant="primary" disabled={!estimate || (estimate.estimate.uses_gpu && !estimate.estimate.profile.available)}
            icon={<Sparkles className="h-4 w-4" aria-hidden />}
            onClick={() => { const e = estimate!; setEstimate(null); void submit("camera.cinematic", { guide_job_id: e.guideId }); }}>
            {t("generate")}
          </Button>
        </>}>
        {estimate && <EstimateBody estimate={estimate.estimate} t={t} />}
      </Modal>
    </div>
  );
}
