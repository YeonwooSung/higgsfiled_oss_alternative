"use client";

/**
 * Screen C (spec §3 화면 C): a finished render next to the source at the same timestamp (split slider or toggle), its
 * badges (Quick vs Generated plate, Mock, Previous version), the checks of the render (outside-ROI integrity, subject
 * occlusion, tracking and mask warnings, sound), the four downloads and an explicit Share action.
 *
 * "Previous version": the settings now differ from the render's pinned revision in something the render reads
 * (`model/results.isPreviousVersion`); its downloads stay available.
 */
import type { Job } from "@oc/contracts";
import { cx } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { CircleCheck, CircleHelp, Music, TriangleAlert, VolumeX } from "lucide-react";
import { useId, useMemo, type ReactNode } from "react";
import { api } from "../api/client";
import { useAssetUrl } from "../hooks/assets";
import { activeJob } from "../hooks/jobs";
import { useI18n } from "../i18n";
import {
  audioSummary, isMockResult, isPreviousVersion, occlusionView, profileBadgeOf, renderResults, resultOf, roiQaView, trackingWarningItems,
} from "../model/results";
import { assetById, useEditor } from "../state/editorStore";
import { MockBadge, PreviousVersionBadge, ProfileBadge } from "./badges";
import { CompareView } from "./CompareView";
import { ExportMenu } from "./ExportMenu";
import { JobProgress } from "./JobUi";
import { ShareLink } from "./ShareView";
import { useCapabilities } from "../hooks/capabilities";

function Line({ tone, icon, children }: { tone: "ok" | "warn" | "muted"; icon: ReactNode; children: ReactNode }) {
  return (
    <li className={cx("flex items-start gap-1.5", tone === "ok" ? "text-tracking" : tone === "warn" ? "text-warning" : "text-muted")}>
      <span className="mt-0.5 shrink-0">{icon}</span><span>{children}</span>
    </li>
  );
}

const ok = <CircleCheck className="h-3.5 w-3.5" aria-hidden />;
const warn = <TriangleAlert className="h-3.5 w-3.5" aria-hidden />;
const unknown = <CircleHelp className="h-3.5 w-3.5" aria-hidden />;

/** `selectedId` null = the newest render. */
export function ResultView({ selectedId, onSelect }: { selectedId: string | null; onSelect: (id: string) => void }) {
  const { t, locale } = useI18n();
  const project = useEditor((s) => s.project)!;
  const config = useEditor((s) => s.config)!;
  const revision = useEditor((s) => s.revision);
  const jobs = useEditor((s) => s.jobs);
  const caps = useCapabilities();
  const pickId = useId();
  const renders = useMemo(() => renderResults(jobs), [jobs]);
  const job: Job | undefined = renders.find((j) => j.id === selectedId) ?? renders[0];
  const result = resultOf(job);
  const rev = useQuery({
    queryKey: ["revision", project.id, job?.input_revision],
    queryFn: () => api.revision(project.id, job!.input_revision),
    enabled: !!job, staleTime: Infinity,
  });
  const renderConfig = job && rev.data?.revision === job.input_revision ? rev.data.config : undefined;
  const previous = job ? isPreviousVersion({ renderRevision: job.input_revision, renderConfig, revision, config }) : false;
  // before = the work clip the render composited over (its revision's), at the render's size
  const workId = renderConfig?.work_asset_id ?? (job && job.input_revision === revision ? config.work_asset_id : null);
  const beforeUrl = useAssetUrl(assetById(project, workId));
  const afterUrl = useAssetUrl(assetById(project, result?.render_video_asset_id));
  const running = activeJob(jobs, "vfx.render");

  if (!job || !result) {
    return (
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-3 p-4 text-sm text-muted" data-region="results-empty">
        {running ? <JobProgress job={running} label={t("job_render")} /> : <p>{t("noRenderYet")}</p>}
      </div>
    );
  }

  const isMockProfile = (id: string) => !!caps.data?.profiles.find((p) => p.id === id)?.is_mock;
  // A render of a clip handed off from a Mock result is marked Mock too.
  const mock = isMockResult(result, job.model_profile, isMockProfile) || project.upstream_mock;
  const badge = profileBadgeOf(result);
  const roi = roiQaView(result.roi_qa);
  const occ = occlusionView(result, renderConfig?.effect.layer_order);
  const warnings = trackingWarningItems(result);
  const audio = audioSummary(result);
  const audioKey = { none: "audioNone", original: "audioOriginal", sfx: "audioSfx", original_sfx: "audioOriginalSfx" } as const;

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 p-3 md:p-4" data-region="results" data-render={job.id}>
      <div className="flex flex-wrap items-center gap-2" data-region="results-header">
        {renders.length > 1 ? (
          <label htmlFor={pickId} className="flex items-center gap-2 text-xs text-muted">
            {t("resultVersion")}
            <select id={pickId} value={job.id} onChange={(e) => onSelect(e.target.value)}
              className="min-h-[44px] rounded-button border border-border bg-app px-2 text-[13px] text-ink md:min-h-8">
              {renders.map((j) => (
                <option key={j.id} value={j.id}>{t("resultOption", { rev: j.input_revision, time: new Date(j.created_at).toLocaleTimeString(locale) })}</option>
              ))}
            </select>
          </label>
        ) : (
          <span className="text-xs text-muted">{t("resultOption", { rev: job.input_revision, time: new Date(job.created_at).toLocaleTimeString(locale) })}</span>
        )}
        <ProfileBadge kind={badge} />
        {mock && <MockBadge />}
        {previous && <PreviousVersionBadge />}
        {running && <div className="w-full"><JobProgress job={running} label={t("job_render")} /></div>}
      </div>
      <div className="flex flex-col gap-1 text-xs text-muted">
        <p>{badge === "generated" ? t("generatedNote") : t("quickNote")}</p>
        {previous && <p className="text-warning" data-region="previous-version">{t("previousVersionHint")}</p>}
      </div>

      <CompareView key={job.id} beforeUrl={beforeUrl} afterUrl={afterUrl} size={[result.width, result.height]} />

      <div className="grid gap-4 md:grid-cols-2">
        <section aria-labelledby={`${pickId}-checks`} className="flex flex-col gap-2 rounded-panel border border-border bg-panel p-3 text-xs" data-region="checks">
          <h3 id={`${pickId}-checks`} className="font-semibold uppercase tracking-wide text-muted">{t("checks")}</h3>
          <ul className="flex flex-col gap-1.5">
            {roi.kind === "pass" ? <Line tone="ok" icon={ok}>{t("roiPass", { err: roi.maxErr })}</Line>
              : roi.kind === "fail" ? <Line tone="warn" icon={warn}>{t("roiFail", { err: roi.maxErr ?? "?" })}</Line>
                : <Line tone="muted" icon={unknown}>{t("roiUnknown")}</Line>}
            {occ.kind === "in_front" && <Line tone="muted" icon={ok}>{t("occInFront")}</Line>}
            {occ.kind === "clear" && <Line tone="ok" icon={ok}>{t("occClear")}</Line>}
            {occ.kind === "touches" && <Line tone="warn" icon={warn}>{t("occTouches", { pct: occ.pct })}</Line>}
            {occ.kind === "split_glow" && <Line tone="muted" icon={ok}>{t("occSplitGlow", { pct: occ.pct })}</Line>}
            {occ.kind === "unknown" && <Line tone="muted" icon={unknown}>{t("occUnknown")}</Line>}
            <Line tone="muted" icon={audio === "none" ? <VolumeX className="h-3.5 w-3.5" aria-hidden /> : <Music className="h-3.5 w-3.5" aria-hidden />}>
              {t(audioKey[audio])}
            </Line>
          </ul>
          <ul className="flex flex-col gap-1.5 border-t border-border pt-2" data-region="result-warnings">
            {warnings.length === 0 ? <Line tone="ok" icon={ok}>{t("noWarnings")}</Line>
              : warnings.map((w) => <Line key={w.key + JSON.stringify(w.vars)} tone="warn" icon={warn}>{t(w.key, w.vars)}</Line>)}
          </ul>
        </section>
        <div className="flex flex-col gap-4 rounded-panel border border-border bg-panel p-3">
          <ExportMenu key={job.id} job={job} />
          <ShareLink key={result.render_video_asset_id} assetId={result.render_video_asset_id} />
        </div>
      </div>
    </div>
  );
}
