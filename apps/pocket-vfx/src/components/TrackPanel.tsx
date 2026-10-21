"use client";

/**
 * Tracking (spec §3 Inspector, §13): status, confidence timeline (a heuristic, never a percent), lost stretches and
 * mask checks that jump to their frame (mask checks open the Mask view), and the actions: Track (`vfx.track`),
 * "Re-track from here" (a correction on the current frame at the shown position, then `vfx.track`) and Reset (removes
 * the corrections, undoable; the anchor stays).
 */
import type { Job, TrackJson, VfxMaskDiagnostic, VfxTrackResult } from "@oc/contracts";
import { Badge, Button } from "@oc/ui";
import { CircleAlert, Crosshair, Loader2, RotateCcw, ScanSearch } from "lucide-react";
import { useState } from "react";
import { activeJob, errorReason, resetCorrections, retrackFrom, startJob, trackGate, type Reason } from "../hooks/jobs";
import { useValidation } from "../hooks/validate";
import { useI18n } from "../i18n";
import { anchorAt } from "../model/preview";
import { useEditor, type TrackStatus } from "../state/editorStore";
import { ErrorLine, JobProgress, jobFailure, Reasons, reasonText } from "./RenderBar";
import { confidenceColor } from "./Timeline";

const STATUS_TONE: Record<TrackStatus, "neutral" | "success" | "warning"> = { none: "neutral", fresh: "success", stale: "warning" };

function jumpTo(frame: number, mask = false) {
  const s = useEditor.getState();
  s.setPlaying(false);
  s.setFrame(frame);
  if (mask) s.setView("mask");
}

export function TrackPanel({ status, result, track, n, policy }: {
  status: TrackStatus;
  result: VfxTrackResult | null;
  track: TrackJson | null;
  n: number;
  policy: "hide" | "freeze";
}) {
  const { t } = useI18n();
  const jobs = useEditor((s) => s.jobs);
  const frame = useEditor((s) => s.frame);
  const anchor = useEditor((s) => s.config!.anchor);
  const corrections = useEditor((s) => s.config!.corrections);
  const basis = useEditor((s) => s.trackBasis);
  const last = useValidation((s) => s.last);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const gate = trackGate({ validation: last, jobs });
  const running = activeJob(jobs, "vfx.track");
  const fresh = status === "fresh";

  // "Re-track from here": the position shown on this frame (its correction, else the fresh tracked position)
  const here = corrections.find((k) => k.frame === frame);
  const shown: [number, number] | null = here ? [here.x, here.y] : fresh && track ? anchorAt(track, anchor, frame, policy) : null;
  const retrackWhy: Reason[] = !gate.ok ? gate.reasons
    : basis === null ? [{ code: "no_track" }]
    : !anchor.follow ? [{ code: "follow_off" }]
    : !shown ? [{ code: status === "stale" ? "track_stale" : "lost_here" }]
    : [];

  const run = async (start: () => Promise<Job> | null) => {
    setError(null);
    setPending(true);
    try {
      const p = start();
      if (!p) {
        setError(reasonText({ code: "corrections_full" }, t));
        return;
      }
      const done = await p;
      setError(jobFailure(done, t("job_track"), t));
    } catch (e) {
      setError(reasonText(errorReason(e), t));
    } finally {
      setPending(false);
    }
  };

  const points = track?.points ?? [];
  const valid = points.filter((p) => p.valid).length;
  const lost = result?.invalid_intervals ?? track?.invalid_intervals ?? [];
  const diagnostics: VfxMaskDiagnostic[] = result?.masks?.diagnostics ?? [];
  const busy = pending || !!running;

  return (
    <section className="flex flex-col gap-3" data-region="track-panel" aria-label={t("trackStatus")}>
      <div className="flex items-center gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{t("trackStatus")}</h2>
        <Badge tone={STATUS_TONE[status]}>{t(`track_${status}`)}</Badge>
      </div>
      <p className="text-xs text-muted">{t(`trackHint_${status}`)}</p>

      {track && (
        <div className="flex flex-col gap-1">
          <div className={fresh ? "" : "opacity-40"} aria-label={fresh ? t("confidence") : t("confidenceStale")} role="img" data-region="track-confidence">
            <div className="flex h-2 overflow-hidden rounded-full bg-app">
              {Array.from({ length: n }, (_, f) => {
                const p = points[f]?.frame === f ? points[f] : points.find((q) => q.frame === f);
                return <span key={f} className="h-full flex-1" style={{ background: confidenceColor(p) }} />;
              })}
            </div>
          </div>
          <p className="tabular text-xs text-muted">{t("trackedFrames", { valid, total: n })}</p>
        </div>
      )}

      {lost.length > 0 && (
        <div className="flex flex-col gap-1">
          <h3 className="flex items-center gap-1 text-xs font-medium text-warning"><CircleAlert className="h-3.5 w-3.5" aria-hidden />{t("trackLostList")}</h3>
          <ul className="flex flex-wrap gap-1">
            {lost.map(([a, b]) => (
              <li key={`${a}-${b}`}>
                <button type="button" onClick={() => jumpTo(a)} title={t("trackLostAt", { first: a + 1, last: b + 1 })}
                  className="min-h-[44px] rounded-button border border-border px-2 text-xs text-ink hover:bg-app md:min-h-7">
                  {t("lostStretch", { first: a + 1, last: b + 1 })}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {diagnostics.length > 0 && (
        <div className="flex flex-col gap-1" data-region="mask-diagnostics">
          <h3 className="text-xs font-medium text-warning">{fresh ? t("maskChecks") : t("maskChecksStale")}</h3>
          <ul className="flex flex-col gap-1">
            {diagnostics.map((d) => (
              <li key={`${d.code}-${d.frame}`}>
                <button type="button" onClick={() => jumpTo(d.frame, true)} title={t("diagGo", { frame: d.frame + 1 })}
                  className="flex min-h-[44px] w-full items-center gap-1.5 rounded-button border border-border px-2 text-left text-xs text-ink hover:bg-app md:min-h-7">
                  <ScanSearch className="h-3.5 w-3.5 shrink-0 text-warning" aria-hidden />
                  {t("diagItem", { issue: t(`diag_${d.code}`), frame: d.frame + 1 })}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <Button variant="primary" disabled={!gate.ok || busy} onClick={() => void run(() => startJob("vfx.track"))}
          icon={pending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Crosshair className="h-4 w-4" aria-hidden />}>
          {status === "none" ? t("trackRun") : t("trackAgain")}
        </Button>
        {running && <JobProgress job={running} label={t("job_track")} />}
        {!gate.ok && !running && <Reasons reasons={gate.reasons} />}
        <p className="text-[11px] text-muted">{t("trackGpuNote")}</p>

        <Button disabled={retrackWhy.length > 0 || busy} onClick={() => void run(() => retrackFrom(shown!))} title={t("retrackHint")}
          icon={<Crosshair className="h-4 w-4 text-tracking" aria-hidden />}>
          {t("retrackHere")}
        </Button>
        {retrackWhy.length > 0 && gate.ok ? <Reasons reasons={retrackWhy} /> : <p className="text-[11px] text-muted">{t("retrackHint")}</p>}

        <Button variant="ghost" disabled={!corrections.length} onClick={resetCorrections} title={t("resetHint")}
          icon={<RotateCcw className="h-4 w-4" aria-hidden />}>
          {t("resetCorrections")}
        </Button>
        {corrections.length ? <p className="text-[11px] text-muted">{t("resetHint")}</p> : <Reasons reasons={[{ code: "no_corrections" }]} />}
        <ErrorLine text={error} />
      </div>
    </section>
  );
}
