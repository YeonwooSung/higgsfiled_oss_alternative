"use client";

/**
 * Inspector (spec §3): Timing, Position (anchor, follow, track window, correction keyframes), Effect, Depth order,
 * Tracking (slot: `TrackPanel`), Effect plate (Quick bundled plates / generated plates with seed, raw/matte preview,
 * matte threshold + knee, and the explicit "Generate effect plate" with its estimate) and Audio. Every edit is a store
 * edit (undo + autosave); scale/intensity/timing/palette edits never re-run track or plate stages.
 */
import type { Estimate, VfxEffect, VfxLayerOrder, VfxPalette, VfxPreset } from "@oc/contracts";
import { Badge, Button, cx, Section, Segmented, SliderField } from "@oc/ui";
import { Dices, Loader2, Sparkles, TriangleAlert, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api } from "../api/client";
import { clipSource } from "../flow/clip";
import { useAssetUrl } from "../hooks/assets";
import {
  activeJob, chooseBundledPlate, errorReason, focusedPlate, generatedPlates, plateGate, selectGeneratedPlate, startJob, type GeneratedPlate,
} from "../hooks/jobs";
import { useCurrentValidation } from "../hooks/validate";
import { useI18n } from "../i18n";
import { sfxFromPercent, sfxPercent } from "../model/audio";
import { parseSeed, SEED_MAX } from "../model/seed";
import { outsideTrackWindow } from "../model/frames";
import { effectWindow, FPS } from "../model/timeline";
import { assetById, useEditor, type TimingHandle } from "../state/editorStore";
import { usePresets } from "./EffectCards";
import { ErrorLine, JobProgress, jobFailure, Modal, Reasons, reasonText, type T } from "./JobUi";

const pct = (v: number) => `${Math.round(v * 1000) / 10}`;
type ControlKey = "scale" | "intensity" | "rotation_deg" | "squash";
const CONTROL_BOUNDS: Record<ControlKey, [number, number]> = { scale: [0.25, 2], intensity: [0, 1], rotation_deg: [-180, 180], squash: [0.2, 1] };
const CONTROL_LABEL = { scale: "scale", intensity: "intensity", rotation_deg: "rotation", squash: "squash" } as const;
const CONTROL_STEP: Record<ControlKey, number> = { scale: 0.01, intensity: 0.01, rotation_deg: 1, squash: 0.01 };

function Toggle({ label, checked, onChange, disabled, hint }: { label: string; checked: boolean; onChange: (on: boolean) => void; disabled?: boolean; hint?: ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="flex min-h-[44px] items-center gap-2 text-[13px] md:min-h-0">
        <input type="checkbox" className="h-4 w-4 accent-[#FF9A52]" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
        <span className={disabled ? "text-muted" : "text-ink"}>{label}</span>
      </label>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

function TimingSection({ n }: { n: number }) {
  const { t } = useI18n();
  const effect = useEditor((s) => s.config!.effect);
  const duration = useEditor((s) => s.config!.trim.duration_ms);
  const w = effectWindow(effect, duration);
  const field = (h: TimingHandle, f: number) => (
    <SliderField key={h} label={t(`handle_${h}`)} value={f + 1} min={1} max={n} step={1}
      format={(v) => t("timingFrame", { frame: v, s: ((v - 1) / FPS).toFixed(2) })}
      onChange={(v) => useEditor.getState().dragTiming(h, Math.round(v) - 1, `inspector:${h}`)} />
  );
  return (
    <Section title={t("secTiming")}>
      {field("start", w.start)}
      {field("peak", w.peak)}
      {field("end", w.end)}
    </Section>
  );
}

function PositionSection() {
  const { t } = useI18n();
  const anchor = useEditor((s) => s.config!.anchor);
  const corrections = useEditor((s) => s.config!.corrections);
  const { update, setFrame, setPlaying } = useEditor.getState();
  const go = (f: number) => { setPlaying(false); setFrame(f); };
  const win = anchor.track_window_frames;
  return (
    <Section title={t("secPosition")}>
      <div className="flex items-center justify-between gap-2 text-[13px]">
        <span>{t("anchorFrame", { frame: anchor.frame + 1 })}</span>
        <Button size="sm" variant="ghost" onClick={() => go(anchor.frame)}>{t("goToFrame", { frame: anchor.frame + 1 })}</Button>
      </div>
      <SliderField label={t("anchorX")} value={anchor.x} min={0} max={1} step={0.001} format={(v) => `${pct(v)}%`}
        onChange={(v) => update((c) => { c.anchor.x = v; }, "inspector:anchor-x")} />
      <SliderField label={t("anchorY")} value={anchor.y} min={0} max={1} step={0.001} format={(v) => `${pct(v)}%`}
        onChange={(v) => update((c) => { c.anchor.y = v; }, "inspector:anchor-y")} />
      <Toggle label={t("follow")} checked={anchor.follow} onChange={(on) => update((c) => { c.anchor.follow = on; })}
        hint={anchor.follow ? undefined : t("followHint")} />
      <Toggle label={t("trackWindow")} checked={win !== null} onChange={(on) => update((c) => { c.anchor.track_window_frames = on ? 24 : null; })}
        hint={win === null ? t("trackWindowAll") : undefined} />
      {win !== null && (
        <SliderField label={t("trackWindow")} value={win} min={1} max={120} step={1} format={(v) => t("trackWindowFrames", { n: v })}
          onChange={(v) => update((c) => { c.anchor.track_window_frames = Math.round(v); }, "inspector:window")} />
      )}
      <div className="space-y-1.5" data-region="corrections">
        <h4 className="text-[13px] text-ink">{t("corrections")}</h4>
        {corrections.length === 0 ? <p className="text-xs text-muted">{t("noCorrections")}</p> : (
          <ul className="space-y-1">
            {corrections.map((k) => (
              <li key={k.frame} className="flex items-center gap-1">
                <button type="button" onClick={() => go(k.frame)} title={t("goToFrame", { frame: k.frame + 1 })}
                  className="min-h-[44px] flex-1 rounded-button border border-border px-2 text-left text-xs tabular hover:bg-app md:min-h-7">
                  {t("correctionItem", { frame: k.frame + 1, x: pct(k.x), y: pct(k.y) })}
                  {outsideTrackWindow(anchor, k.frame) && <span className="ml-1 text-warning">· {t("outsideWindow")}</span>}
                </button>
                <button type="button" aria-label={t("removeCorrection", { frame: k.frame + 1 })} title={t("removeCorrection", { frame: k.frame + 1 })}
                  onClick={() => update((c) => { c.corrections = c.corrections.filter((x) => x.frame !== k.frame); })}
                  className="flex h-[44px] w-[44px] items-center justify-center rounded-button text-muted hover:bg-app hover:text-ink md:h-7 md:w-7">
                  <X className="h-3.5 w-3.5" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  );
}

function EffectSection({ preset }: { preset: VfxPreset | undefined }) {
  const { t } = useI18n();
  const effect = useEditor((s) => s.config!.effect);
  const update = useEditor((s) => s.update);
  const keys = (Object.keys(CONTROL_BOUNDS) as ControlKey[]).filter((k) => (preset ? !!preset.controls[k] : k === "scale" || k === "intensity"));
  const fmt = (k: ControlKey, v: number) => (k === "rotation_deg" ? `${Math.round(v)}°` : k === "scale" ? `×${v.toFixed(2)}` : v.toFixed(2));
  return (
    <Section title={t("secEffect")}>
      {keys.map((k) => {
        const [lo, hi] = preset?.controls[k] ?? CONTROL_BOUNDS[k];
        return (
          <SliderField key={k} label={t(CONTROL_LABEL[k])} value={effect[k] as number} min={lo} max={hi} step={CONTROL_STEP[k]} format={(v) => fmt(k, v)}
            onChange={(v) => update((c) => { (c.effect as VfxEffect)[k] = v; }, `inspector:${k}`)} />
        );
      })}
      <div className="space-y-1.5">
        <span className="text-[13px]">{t("palette")}</span>
        <Segmented<VfxPalette> label={t("palette")} value={effect.palette}
          options={(["violet", "amber", "cyan"] as const).map((p) => ({ value: p, label: t(`palette_${p}`) }))}
          onChange={(p) => update((c) => { c.effect.palette = p; })} />
      </div>
    </Section>
  );
}

function DepthSection() {
  const { t } = useI18n();
  const order = useEditor((s) => s.config!.effect.layer_order);
  return (
    <Section title={t("secDepth")}>
      <Segmented<VfxLayerOrder> label={t("secDepth")} value={order}
        options={(["behind_subject", "in_front", "split_glow"] as const).map((o) => ({ value: o, label: t(`layer_${o}`) }))}
        onChange={(o) => useEditor.getState().update((c) => { c.effect.layer_order = o; })} />
      {order !== "in_front" && <p className="text-xs text-muted">{t("layerHint")}</p>}
    </Section>
  );
}

function EstimateBody({ estimate, t }: { estimate: Estimate; t: T }) {
  const p = estimate.profile;
  return (
    <>
      <p className="flex flex-wrap items-center gap-2">
        <span className="text-muted">{t("estimateProfile")}:</span> {p.label}
        {p.is_mock && <Badge tone="mock" title={t("mockExplain")}>{t("mock")}</Badge>}
      </p>
      <p className="text-muted">{t("estimateStages", { value: estimate.stages.map((s) => t(`stage_${s}` as Parameters<T>[0])).join(" → ") })}</p>
      {estimate.uses_gpu
        ? <p className="tabular">{t("estimateGpuCap", { units: estimate.reserved_units, unit: estimate.unit === "gpu_seconds" ? t("gpuSeconds") : estimate.unit.replace(/_/g, " ") })}</p>
        : <p>{t("estimateNoGpu")}</p>}
      <p>{estimate.price === null || estimate.price_status === "unknown" ? t("estimatePriceUnknown") : t("estimatePrice", { value: estimate.price })}</p>
      {!p.available && (
        <p role="alert" className="flex items-start gap-1.5 text-danger">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>{t("estimateUnavailable", { reason: p.reason ?? t("profileUnavailable") })}</span>
        </p>
      )}
      <p className="text-xs text-muted">{t("estimateExplicit")}</p>
    </>
  );
}

/** Raw plate / matte preview of one generated plate (looping, muted). */
function PlatePreview({ plate }: { plate: GeneratedPlate }) {
  const { t } = useI18n();
  const project = useEditor((s) => s.project);
  const [view, setView] = useState<"raw" | "matte">("raw");
  const id = view === "raw" ? plate.result.plate_asset_id : plate.result.matte_preview_asset_id;
  const url = useAssetUrl(assetById(project, id));
  return (
    <div className="space-y-1.5" data-region="plate-preview">
      <Segmented<"raw" | "matte"> label={t("platePreview")} value={view} onChange={setView}
        options={[{ value: "raw", label: t("viewRaw") }, { value: "matte", label: t("viewMatteOnly") }]} />
      <div className="flex aspect-video items-center justify-center overflow-hidden rounded-button bg-stage ring-1 ring-border">
        {url ? <video key={url} src={url} muted loop autoPlay playsInline className="h-full w-full object-contain" aria-label={view === "raw" ? t("viewRaw") : t("viewMatteOnly")} />
          : <Loader2 className="h-4 w-4 animate-spin text-muted" aria-hidden />}
      </div>
    </div>
  );
}

function PlateSection({ preset }: { preset: VfxPreset | undefined }) {
  const { t } = useI18n();
  const config = useEditor((s) => s.config!);
  const project = useEditor((s) => s.project!);
  const jobs = useEditor((s) => s.jobs);
  const saveState = useEditor((s) => s.saveState);
  const validation = useCurrentValidation();
  const update = useEditor((s) => s.update);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const plate = config.plate;
  const bundled = preset?.bundled_plates ?? [];
  const plates = generatedPlates(jobs, config.effect.preset_id);
  const focus = focusedPlate(plates, config);
  const gate = plateGate({ validation, jobs, saveState });
  const running = activeJob(jobs, "vfx.generate_plate");
  const qa = focus?.result.matte_qa;
  const usingGenerated = plate.mode === "generated";

  const askEstimate = async () => {
    setError(null);
    setPending(true);
    try {
      const revision = await useEditor.getState().flush();
      setEstimate(await api.estimate(project.id, "vfx.generate_plate", revision, {}));
    } catch (e) {
      setError(reasonText(errorReason(e), t));
    } finally {
      setPending(false);
    }
  };
  const generate = async () => {
    setEstimate(null);
    setError(null);
    setPending(true);
    try {
      // the finished plate is listed below; selecting it stays an explicit choice
      setError(jobFailure(await startJob("vfx.generate_plate"), t("job_plate"), t));
    } catch (e) {
      setError(reasonText(errorReason(e), t));
    } finally {
      setPending(false);
    }
  };

  return (
    <Section title={t("secPlate")}>
      <div className="space-y-1" role="radiogroup" aria-label={t("plateBundled")} data-region="bundled-plates">
        <h4 className="text-[13px]">{t("plateBundled")}</h4>
        {bundled.map((b, i) => {
          const on = !usingGenerated && (plate.bundled_id ?? bundled[0]?.id) === b.id;
          return (
            <button key={b.id} type="button" role="radio" aria-checked={on} onClick={() => chooseBundledPlate(i === 0 ? null : b.id)}
              className={cx("flex min-h-[44px] w-full items-center gap-2 rounded-button border px-2 text-left text-xs md:min-h-8",
                on ? "border-accent text-ink" : "border-border text-muted hover:text-ink")}>
              <span className="flex-1">{t("bundledItem", { id: b.id, frames: b.frames })}{b.loopable ? ` · ${t("loops")}` : ""}</span>
              {on && <span className="text-accent">{t("inUse")}</span>}
            </button>
          );
        })}
      </div>

      <div className="space-y-1" role="radiogroup" aria-label={t("plateGenerated")} data-region="generated-plates">
        <h4 className="text-[13px]">{t("plateGenerated")}</h4>
        {plates.length === 0 ? <p className="text-xs text-muted">{t("noGeneratedPlates")}</p> : plates.slice(0, 5).map((p) => {
          const on = usingGenerated && plate.asset_id === p.result.plate_asset_id;
          return (
            <button key={p.job.id} type="button" role="radio" aria-checked={on} onClick={() => selectGeneratedPlate(p.result.plate_asset_id)}
              className={cx("flex min-h-[44px] w-full items-center gap-2 rounded-button border px-2 text-left text-xs md:min-h-8",
                on ? "border-accent text-ink" : "border-border text-muted hover:text-ink")}>
              <span className="flex-1 tabular">{t("plateItem", { seed: p.result.seed, palette: t(`palette_${p.result.palette}`) })}</span>
              {p.result.mock && <Badge tone="mock">{t("mock")}</Badge>}
              <span className={p.result.matte_qa.status === "ok" ? "text-tracking" : "text-warning"}>
                {p.result.matte_qa.status === "ok" ? t("matteOk") : t("matteCheck")}
              </span>
              {on && <span className="text-accent">{t("inUse")}</span>}
            </button>
          );
        })}
      </div>

      {focus && <PlatePreview plate={focus} />}
      {qa?.status === "check" && (
        <div role="status" className="space-y-1.5 rounded-button border border-amber-200 bg-amber-50 px-2.5 py-2 text-xs text-warning" data-region="matte-check">
          <p className="flex items-center gap-1.5 font-medium"><TriangleAlert className="h-3.5 w-3.5" aria-hidden />{t("matteCheckTitle")}</p>
          <ul className="list-disc space-y-0.5 pl-5">
            {qa.reasons.map((r) => <li key={r}>{r === "halo_risk" || r === "flicker" || r === "empty_plate" ? t(`matte_${r}`) : r}</li>)}
          </ul>
          <p className="text-muted">{t("matteNoAuto")}</p>
          {usingGenerated && <Button size="sm" onClick={() => chooseBundledPlate()}>{t("useBundled")}</Button>}
        </div>
      )}

      <SliderField label={t("matteThreshold")} value={plate.matte.threshold} min={0.01} max={0.3} step={0.005} format={(v) => v.toFixed(3)}
        onChange={(v) => update((c) => { c.plate.matte.threshold = v; }, "inspector:threshold")} />
      <SliderField label={t("matteKnee")} value={plate.matte.knee} min={0} max={0.2} step={0.005} format={(v) => v.toFixed(3)}
        onChange={(v) => update((c) => { c.plate.matte.knee = v; }, "inspector:knee")} />
      <p className="text-xs text-muted">{t("matteHint")}</p>

      <div className="flex items-end gap-2">
        <label className="flex flex-1 flex-col gap-1 text-[13px]">
          {t("plateSeed")}
          <input type="number" min={0} max={SEED_MAX} step={1} value={plate.seed}
            className="min-h-[44px] rounded-[6px] border border-border bg-panel px-2 tabular md:min-h-8"
            onChange={(e) => {
              const v = parseSeed(e.target.value); // empty or partial: keep the saved seed
              if (v !== null) update((c) => { c.plate.seed = v; }, "inspector:seed");
            }} />
        </label>
        <Button size="sm" variant="ghost" className="min-h-[44px] md:min-h-8" icon={<Dices className="h-4 w-4" aria-hidden />}
          onClick={() => update((c) => { c.plate.seed = Math.floor(Math.random() * SEED_MAX); })}>{t("newSeed")}</Button>
      </div>
      <Button disabled={!gate.ok || pending || !!running} onClick={() => void askEstimate()}
        icon={pending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Sparkles className="h-4 w-4 text-effect" aria-hidden />}>
        {t("generatePlate")}
      </Button>
      {running && <JobProgress job={running} label={t("job_plate")} />}
      {!gate.ok && !running ? <Reasons reasons={gate.reasons} /> : <p className="text-xs text-muted">{t("generatePlateHint")}</p>}
      <ErrorLine text={error} />

      <Modal open={!!estimate} title={t("estimateTitle")} onClose={() => setEstimate(null)}
        actions={<>
          <Button variant="ghost" onClick={() => setEstimate(null)}>{t("cancel")}</Button>
          <Button variant="primary" disabled={!estimate?.profile.available} icon={<Sparkles className="h-4 w-4" aria-hidden />}
            onClick={() => void generate()}>{t("generate")}</Button>
        </>}>
        {estimate && <EstimateBody estimate={estimate} t={t} />}
      </Modal>
    </Section>
  );
}

function AudioSection() {
  const { t } = useI18n();
  const audio = useEditor((s) => s.config!.audio);
  const hasAudio = useEditor((s) => clipSource(assetById(s.project, s.config!.video_asset_id))?.has_audio ?? false);
  const update = useEditor((s) => s.update);
  const level = sfxPercent(audio);
  return (
    <Section title={t("secAudio")}>
      <Toggle label={t("keepOriginal")} checked={hasAudio && audio.keep_original} disabled={!hasAudio}
        onChange={(on) => update((c) => { c.audio.keep_original = on; })} hint={hasAudio ? undefined : t("noSound")} />
      <SliderField label={t("sfxVolume")} value={level} min={0} max={100} step={1}
        format={(v) => (v <= 0 ? t("sfxOff") : t("sfxLevel", { pct: Math.round(v), db: (sfxFromPercent(v).sfx_gain_db ?? 0).toFixed(1) }))}
        onChange={(v) => update((c) => { Object.assign(c.audio, sfxFromPercent(v)); }, "inspector:sfx")} />
    </Section>
  );
}

/** The inspector sections; `tracking` is placed after Depth order (desktop) or omitted (mobile Track tab has it). */
export function Inspector({ n, tracking }: { n: number; tracking?: ReactNode }) {
  const presets = usePresets();
  const presetId = useEditor((s) => s.config!.effect.preset_id);
  const preset = presets.find((p) => p.id === presetId);
  return (
    <div className="flex flex-col" data-region="inspector-sections">
      <TimingSection n={n} />
      <PositionSection />
      <EffectSection preset={preset} />
      <DepthSection />
      {tracking && <section className="border-b border-border px-4 py-4">{tracking}</section>}
      <PlateSection preset={preset} />
      <AudioSection />
    </div>
  );
}
