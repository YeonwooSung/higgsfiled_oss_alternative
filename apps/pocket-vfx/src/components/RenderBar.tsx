"use client";

/**
 * Render action (spec §3, §11): `vfx.render` with the newest fresh track and the render profile of the selected plate
 * ("Quick" = procedural + bundled plate, never labelled AI generated; "Generated plate" only with a generated plate
 * selected). Disabled buttons always list their reasons in words. Also the shared pieces of the job buttons: the
 * native-dialog Modal, job progress with Cancel, and reason text.
 */
import type { Job } from "@oc/contracts";
import { Badge, Button } from "@oc/ui";
import { Clapperboard, Loader2, RotateCcw } from "lucide-react";
import { useState } from "react";
import { activeJob, errorReason, renderGate, startJob } from "../hooks/jobs";
import { revalidate, useCurrentValidation } from "../hooks/validate";
import { useI18n } from "../i18n";
import { trackStatus, useEditor } from "../state/editorStore";
import { usePresets } from "./EffectCards";
import { ErrorLine, JobProgress, jobFailure, Reasons, reasonText } from "./JobUi";

export function RenderBar({ onDone }: { onDone?: (job: Job) => void }) {
  const { t } = useI18n();
  const config = useEditor((s) => s.config)!;
  const saveState = useEditor((s) => s.saveState);
  const jobs = useEditor((s) => s.jobs);
  const basis = useEditor((s) => s.trackBasis);
  const presets = usePresets();
  const validation = useCurrentValidation();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const revision = useEditor((s) => s.revision);
  /** revision of the last finished render: the note goes away once the settings change */
  const [doneRev, setDoneRev] = useState<number | null>(null);
  const preset = presets.find((p) => p.id === config.effect.preset_id);
  const gate = renderGate({ config, saveState, validation, trackStatus: trackStatus(config, basis), jobs, preset });
  const running = activeJob(jobs, "vfx.render");
  const generated = gate.renderProfile === "generated";
  const warnings = validation?.result?.warnings ?? [];

  const render = async () => {
    setError(null);
    setDoneRev(null);
    setPending(true);
    let finished: Job;
    try {
      finished = await startJob("vfx.render", { track_job_id: gate.trackJobId, render_profile: gate.renderProfile });
    } catch (e) {
      setError(reasonText(errorReason(e), t));
      revalidate(); // the refusal may come from server state the last check did not see
      return;
    } finally {
      setPending(false);
    }
    const failure = jobFailure(finished, t("job_render"), t);
    if (failure) setError(failure);
    else if (finished.state === "succeeded") {
      setDoneRev(finished.input_revision);
      onDone?.(finished);
    }
  };

  return (
    <div data-region="render-bar" className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span data-badge="render-profile" title={generated ? t("renderGeneratedHint") : t("renderQuickHint")}>
          <Badge tone="neutral">{generated ? t("renderGenerated") : t("renderQuick")}</Badge>
        </span>
        <Button variant="primary" className="ml-auto" disabled={!gate.ok || pending} onClick={() => void render()}
          icon={pending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Clapperboard className="h-4 w-4" aria-hidden />}>
          {t("render")}
        </Button>
      </div>
      <p className="text-xs text-muted">{generated ? t("renderGeneratedHint") : t("renderQuickHint")}</p>
      {running && <JobProgress job={running} label={t("job_render")} />}
      {!gate.ok && !running && <Reasons lead={t("renderBlocked")} reasons={gate.reasons.filter((r) => r.code !== "render_running")} />}
      {validation?.error && (
        <Button size="sm" variant="ghost" className="self-start" onClick={revalidate} icon={<RotateCcw className="h-3.5 w-3.5" aria-hidden />}>{t("retry")}</Button>
      )}
      {warnings.length > 0 && <Reasons lead={t("warnings")} tone="warning" reasons={warnings.map((w) => ({ code: w.code, message: w.message }))} />}
      <ErrorLine text={error} />
      {doneRev === revision && !error && <p role="status" className="text-xs text-tracking">{t("renderDone")}</p>}
    </div>
  );
}
