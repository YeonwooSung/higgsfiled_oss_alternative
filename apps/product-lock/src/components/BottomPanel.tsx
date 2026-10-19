"use client";

import type { Estimate, Job, JobKind } from "@oc/contracts";
import { TERMINAL_JOB_STATES } from "@oc/contracts";
import { Badge, Button, cx } from "@oc/ui";
import * as Tabs from "@radix-ui/react-tabs";
import { Clapperboard, ImagePlus, Loader2, Pause, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAssetUrl } from "../hooks/assets";
import { useI18n } from "../i18n";
import { assetById, assetsByRole, useEditor } from "../state/editorStore";
import { randomSeed, startJob } from "../state/jobs";
import { useMask } from "../state/maskStore";
import { EstimateDialog } from "./EstimateDialog";
import { errorText } from "./errors";
import { jobLabel } from "./JobPanel";
import { useMatchingBackgroundVideo } from "./StageArea";

function Playbar() {
  const [state, setState] = useState({ t: 0, playing: false });
  useEffect(() => {
    const id = setInterval(() => {
      const v = document.querySelector<HTMLVideoElement>("video[data-stage-video]");
      if (v) setState({ t: v.currentTime, playing: !v.paused });
    }, 100);
    return () => clearInterval(id);
  }, []);
  const video = () => document.querySelector<HTMLVideoElement>("video[data-stage-video]");
  return (
    <div className="flex items-center gap-2">
      <Button size="sm" variant="ghost" aria-label={state.playing ? "pause" : "play"}
        icon={state.playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
        onClick={() => { const v = video(); if (v) v.paused ? void v.play() : v.pause(); }} />
      <input type="range" min={0} max={5} step={0.04} value={Math.min(5, state.t)} aria-label="timeline"
        onChange={(e) => { const v = video(); if (v) v.currentTime = Number(e.target.value); }}
        className="h-1 w-40 accent-[#18794E] md:w-64" />
      <span className="w-16 text-xs tabular-nums text-muted">{Math.min(5, state.t).toFixed(1)} / 5.0s</span>
    </div>
  );
}

function Thumb({ assetId, selected, onClick, label }: { assetId: string; selected?: boolean; onClick: () => void; label: string }) {
  const project = useEditor((s) => s.project);
  const url = useAssetUrl(assetById(project, assetId));
  return (
    <button type="button" onClick={onClick} aria-pressed={selected}
      className={cx("relative h-20 w-14 shrink-0 overflow-hidden rounded-button border-2 bg-stage", selected ? "border-accent" : "border-transparent")}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {url && <img src={url} alt={label} className="h-full w-full object-cover" />}
      {selected && <span className="absolute bottom-0 inset-x-0 bg-accent text-[10px] text-white">{label}</span>}
    </button>
  );
}

export function BottomPanel() {
  const { t } = useI18n();
  const { project, config, update, jobs, selectRender, revision } = useEditor();
  const maskDirty = useMask((s) => s.dirty);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [pending, setPending] = useState<null | { kind: JobKind; options: Record<string, unknown> }>(null);
  const [error, setError] = useState<string | null>(null);
  const backgrounds = assetsByRole(project, "background").filter((b) => b.meta.aspect === config!.output.aspect);
  const reuse = useMatchingBackgroundVideo();
  const renders = Object.values(jobs).filter((j) => j.kind === "product.render")
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const busy = (kind: JobKind) => Object.values(jobs).some((j) => j.kind === kind && !TERMINAL_JOB_STATES.includes(j.state));

  const ask = async (kind: JobKind, options: Record<string, unknown>) => {
    setError(null);
    try {
      const rev = await useEditor.getState().flush();
      setEstimate(await api.estimate(project!.id, kind, rev, options));
      setPending({ kind, options });
    } catch (e) {
      setError(errorText(e, t));
    }
  };

  const confirm = async () => {
    const p = pending;
    setEstimate(null);
    setPending(null);
    if (!p) return;
    try {
      const job: Job = await startJob(p.kind, p.options);
      if (job.state === "submission_unknown") setError(t("submissionUnknown"));
      else if (job.state === "failed") setError(job.error?.message ?? t("genericError"));
      else if (job.state === "succeeded" && p.kind === "product.background" && !useEditor.getState().config!.scene.background_asset_id) {
        update((c) => { c.scene.background_asset_id = job.output_asset_ids[0]; });
      } else if (job.state === "succeeded" && p.kind === "product.render") {
        selectRender(job.id);
      }
    } catch (e) {
      setError(errorText(e, t));
    }
  };

  const canRender = !!config!.scene.background_asset_id && !!config!.mask_asset_id && !maskDirty;

  return (
    <div className="shrink-0 border-t border-border bg-panel">
      <div className="flex flex-wrap items-center gap-3 px-3 py-2">
        <Playbar />
        <div className="ml-auto flex items-center gap-2">
          <Button variant="secondary" icon={busy("product.background") ? <Loader2 className="h-4 w-4 animate-spin" /> : <ImagePlus className="h-4 w-4" />}
            disabled={busy("product.background")}
            onClick={() => ask("product.background", { preset_id: config!.scene.preset_id, seed: randomSeed() })}>
            {t("generateBackground")}
          </Button>
          <Button variant="primary" icon={busy("product.render") ? <Loader2 className="h-4 w-4 animate-spin" /> : <Clapperboard className="h-4 w-4" />}
            disabled={!canRender || busy("product.render")} title={canRender ? undefined : t(config!.mask_asset_id ? "renderNeedsBackground" : "maskRequired")}
            onClick={() => ask("product.render", reuse ? { reuse_background_video_asset_id: reuse.id } : {})}>
            {t("renderAd")}
          </Button>
        </div>
      </div>
      {error && <p role="alert" className="px-3 pb-2 text-xs text-danger">{error}</p>}
      <Tabs.Root defaultValue="backgrounds" className="hidden border-t border-border md:block">
        <Tabs.List className="flex gap-4 px-3 text-xs">
          <Tabs.Trigger value="backgrounds" className="py-1.5 text-muted data-[state=active]:font-semibold data-[state=active]:text-ink">{t("backgrounds")}</Tabs.Trigger>
          <Tabs.Trigger value="renders" className="py-1.5 text-muted data-[state=active]:font-semibold data-[state=active]:text-ink">{t("renders")}</Tabs.Trigger>
        </Tabs.List>
        <Tabs.Content value="backgrounds" className="flex h-24 items-center gap-2 overflow-x-auto px-3 pb-2">
          {backgrounds.length === 0 && <p className="text-xs text-muted">{t("noBackgrounds")}</p>}
          {backgrounds.map((b) => (
            <Thumb key={b.id} assetId={b.id} selected={config!.scene.background_asset_id === b.id} label={t("selected")}
              onClick={() => update((c) => { c.scene.background_asset_id = b.id; })} />
          ))}
        </Tabs.Content>
        <Tabs.Content value="renders" className="flex h-24 items-center gap-2 overflow-x-auto px-3 pb-2">
          {renders.length === 0 && <p className="text-xs text-muted">{t("noRenders")}</p>}
          {renders.map((j) => (
            <button key={j.id} type="button" onClick={() => j.state === "succeeded" && selectRender(j.id)}
              className="flex h-20 w-40 shrink-0 flex-col justify-between rounded-button border border-border p-2 text-left text-xs hover:bg-app">
              <span className="font-medium">rev {j.input_revision}</span>
              <span className="text-muted">{j.state === "succeeded" ? t("jobDone") : j.state === "failed" ? t("jobFailed") : jobLabel(j, t)}</span>
              {j.input_revision < revision && j.state === "succeeded" && <Badge>{t("previousVersion")}</Badge>}
            </button>
          ))}
        </Tabs.Content>
      </Tabs.Root>
      <EstimateDialog estimate={estimate} onConfirm={confirm} onClose={() => { setEstimate(null); setPending(null); }} />
    </div>
  );
}
