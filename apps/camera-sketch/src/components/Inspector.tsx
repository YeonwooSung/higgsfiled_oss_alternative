"use client";

/**
 * Inspector (spec §4): Target, Camera, Path, Scene, Render — every canvas action has a numeric
 * equivalent here. Bounds come from `GET /camera-sketch/presets` and the config schema.
 */
import type { ControlPreset } from "@oc/contracts";
import { Button, cx, Section, Segmented, SliderField } from "@oc/ui";
import { CircleAlert, Loader2, Plus, RotateCcw, ShieldCheck, Trash2, TriangleAlert } from "lucide-react";
import { useId, type ReactNode } from "react";
import { usePresets, useRenderGate, type CameraPathState } from "../hooks/cameraPath";
import { useI18n } from "../i18n";
import { PARAMS, type ParamName } from "../model/path";
import {
  addControlPoint, HOLE_WARN_RATIO, keyframeTimeMs, moveControlPoint, reasonKey, removeControlPoint, sketchAtTarget, sourceToOutputUv,
  STRENGTH_PARAM, strengthOf, withStrength,
} from "../model/sketch";
import { useEditor } from "../state/editorStore";
import { PresetPicker } from "./LeftPanel";

const fmt = (x: number, d = 3) => Number(x.toFixed(d)).toString();

function Notice({ tone, icon, children }: { tone: "warn" | "error" | "info"; icon?: ReactNode; children: ReactNode }) {
  return (
    <div role={tone === "error" ? "alert" : "status"}
      className={cx("flex gap-2 rounded-button border px-2.5 py-2 text-xs",
        tone === "error" ? "border-red-200 bg-red-50 text-danger" : tone === "warn" ? "border-amber-200 bg-amber-50 text-target" : "border-border bg-app text-muted")}>
      <span className="mt-px shrink-0" aria-hidden>{icon ?? (tone === "info" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : tone === "error" ? <CircleAlert className="h-3.5 w-3.5" /> : <TriangleAlert className="h-3.5 w-3.5" />)}</span>
      <div className="min-w-0 space-y-1.5">{children}</div>
    </div>
  );
}

/** Server/provisional path status, reasons in words, reduced-motion and restore-preset actions. */
export function PathStatus({ cam }: { cam: CameraPathState }) {
  const { t } = useI18n();
  const compileError = useEditor((s) => s.compileError);
  const hasSketch = useEditor((s) => !!s.config?.path.sketch_points.length);
  const restorePreset = useEditor((s) => s.restorePreset);
  const applySuggested = useEditor((s) => s.applySuggestedParams);
  const path = cam.path;
  if (!path) return null;
  const reasons = path.reasons.filter((r) => r !== "unsupported_sketch");
  const unsupported = path.reasons.includes("unsupported_sketch");
  const holesHigh = cam.holeRatio !== null && cam.holeRatio > HOLE_WARN_RATIO;
  return (
    <div className="space-y-2" data-region="path-status">
      {cam.provisional && !compileError && <Notice tone="info">{t("serverChecking")}</Notice>}
      {compileError && <Notice tone="error">{t(reasonKey(compileError === "depth_missing" ? "depth_missing" : "compile_failed"))}</Notice>}
      {path.status === "invalid" && (
        <Notice tone="error">
          <p className="font-semibold">{t("pathInvalid")}</p>
          <ul className="list-disc space-y-0.5 pl-4">{(reasons.length ? reasons : ["unknown"]).map((r) => <li key={r}>{t(reasonKey(r))}</li>)}</ul>
        </Notice>
      )}
      {path.status === "warning" && reasons.length > 0 && (
        <Notice tone="warn">
          <p className="font-semibold">{t("pathWarning")}</p>
          <ul className="list-disc space-y-0.5 pl-4">{reasons.map((r) => <li key={r}>{t(reasonKey(r))}</li>)}</ul>
        </Notice>
      )}
      {unsupported && hasSketch && (
        <Notice tone="warn">
          <p>{t("reason_unsupported_sketch")}</p>
          <Button size="sm" onClick={restorePreset} icon={<RotateCcw className="h-3.5 w-3.5" />}>{t("restorePreset")}</Button>
        </Notice>
      )}
      {holesHigh && <Notice tone="warn">{t("reason_holes_high")}</Notice>}
      {cam.suggestion && (
        <Notice tone="warn">
          <p>{t("compareHint")}</p>
          <p className="tabular text-muted">
            {PARAMS.filter((k) => cam.suggestion!.params[k] !== (path.params[k] ?? 0)).map((k) => `${t(PARAM_LABEL[k])} ${fmt(path.params[k] ?? 0)} → ${fmt(cam.suggestion!.params[k])}`).join(" · ")}
          </p>
          <Button size="sm" variant="primary" onClick={() => applySuggested(cam.suggestion!.params)}>{t("applyReducedMotion")}</Button>
        </Notice>
      )}
      {!cam.provisional && cam.response && (
        <p className="text-xs text-muted tabular">
          {cam.response.deferred ? t("holeDeferred") : cam.holeRatio !== null ? t("holeRatio", { value: (cam.holeRatio * 100).toFixed(1) }) : null}
        </p>
      )}
    </div>
  );
}

const PARAM_LABEL: Record<ParamName, "lateral" | "push" | "arc"> = { lateral_ratio: "lateral", push_ratio: "push", arc_deg: "arc" };

function ParamSlider({ k, mobileLabel }: { k: ParamName; mobileLabel?: string }) {
  const { t } = useI18n();
  const presets = usePresets();
  const path = useEditor((s) => s.config!.path);
  const update = useEditor((s) => s.update);
  const preset = presets.find((p) => p.id === path.preset);
  const [lo, hi] = preset?.bounds[k] ?? [0, 0];
  const used = hi > lo;
  const step = k === "arc_deg" ? 0.1 : 0.001;
  const unit = (v: number) => (k === "lateral_ratio" ? t("lateralUnit", { value: fmt(v) }) : k === "push_ratio" ? t("pushUnit", { value: fmt(v * 100, 1) }) : t("arcUnit", { value: fmt(v, 1) }));
  return (
    <div>
      <SliderField label={mobileLabel ?? t(PARAM_LABEL[k])} value={used ? path[k] : 0} min={used ? lo : 0} max={used ? hi : 1} step={step} disabled={!used}
        format={used ? unit : () => t("notInPreset")} onChange={(v) => update((c) => { c.path[k] = v; }, `param:${k}`)} />
    </div>
  );
}

function LateralProposal() {
  const { t } = useI18n();
  const proposal = useEditor((s) => s.lateralProposal);
  const apply = useEditor((s) => s.applyLateralProposal);
  const dismiss = useEditor((s) => s.dismissLateralProposal);
  if (proposal === null) return null;
  return (
    <Notice tone="warn">
      {/* the target moves right on screen when the camera moves left: proposal < 0 means a rightward sketch */}
      <p>{t("lateralProposalText", { dir: proposal < 0 ? t("dirRight") : t("dirLeft"), value: fmt(proposal) })}</p>
      <div className="flex gap-2">
        <Button size="sm" variant="primary" onClick={apply}>{t("apply")}</Button>
        <Button size="sm" variant="ghost" onClick={dismiss}>{t("dismiss")}</Button>
      </div>
    </Notice>
  );
}

function TargetSection() {
  const { t } = useI18n();
  const target = useEditor((s) => s.config!.target);
  const update = useEditor((s) => s.update);
  const auto = target.depth_scale === 1;
  return (
    <Section title={t("secTarget")}>
      <SliderField label={t("targetX")} value={target.u} min={0} max={1} step={0.001} format={(v) => `${fmt(v * 100, 1)}%`}
        onChange={(v) => update((c) => { c.target.u = v; }, "target:u")} />
      <SliderField label={t("targetY")} value={target.v} min={0} max={1} step={0.001} format={(v) => `${fmt(v * 100, 1)}%`}
        onChange={(v) => update((c) => { c.target.v = v; }, "target:v")} />
      <SliderField label={t("depthCorrection")} value={target.depth_scale} min={0.5} max={2} step={0.01}
        format={(v) => (v === 1 ? `${t("depthAuto")} (×1)` : `×${fmt(v, 2)}`)}
        onChange={(v) => update((c) => { c.target.depth_scale = v; }, "target:depth")} />
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs text-muted">{t("depthAutoHint")}</p>
        <Button size="sm" variant="ghost" disabled={auto} onClick={() => update((c) => { c.target.depth_scale = 1; })}>{t("setAuto")}</Button>
      </div>
    </Section>
  );
}

function CameraSection() {
  const { t } = useI18n();
  const focal = useEditor((s) => s.config!.camera.focal_length_mm_equiv);
  const update = useEditor((s) => s.update);
  return (
    <Section title={t("secCamera")}>
      <PresetPicker />
      <ParamSlider k="lateral_ratio" />
      <LateralProposal />
      <ParamSlider k="push_ratio" />
      <ParamSlider k="arc_deg" />
      <SliderField label={t("focal")} value={focal} min={35} max={70} step={1} format={() => t("focalNote")}
        onChange={(v) => update((c) => { c.camera.focal_length_mm_equiv = v; }, "camera:focal")} />
      <div className="flex justify-between text-[13px]"><span>{t("roll")}</span><span className="text-muted">{t("rollFixed")}</span></div>
    </Section>
  );
}

function NumberCell({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  const id = useId();
  return (
    <label htmlFor={id} className="flex items-center gap-1 text-xs text-muted">
      {label}
      <input id={id} type="number" min={0} max={1} step={0.01} value={fmt(value)} inputMode="decimal"
        onChange={(e) => {
          const v = Number(e.target.value);
          if (e.target.value !== "" && Number.isFinite(v)) onChange(Math.min(1, Math.max(0, v)));
        }}
        className="min-h-[44px] w-16 rounded-button border border-border bg-panel px-1.5 text-right text-[13px] tabular-nums text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent md:min-h-0 md:py-0.5" />
    </label>
  );
}

/** Control points (time + output position), easing, reset — the numeric form of the Sketch / Move keyframe tools. */
export function PathSection({ cam }: { cam: CameraPathState }) {
  const { t } = useI18n();
  const path = useEditor((s) => s.config!.path);
  const update = useEditor((s) => s.update);
  const restorePreset = useEditor((s) => s.restorePreset);
  const pts = path.sketch_points;
  const targetOut = cam.info && cam.srcW && cam.srcH ? sourceToOutputUv(cam.info.target_uv01, cam.srcW, cam.srcH, cam.outW, cam.outH) : null;
  return (
    <Section title={t("secPath")} action={
      <Button size="sm" variant="ghost" disabled={!pts.length} onClick={restorePreset} icon={<RotateCcw className="h-3.5 w-3.5" />}>{t("resetPath")}</Button>
    }>
      {pts.length === 0 ? <p className="text-xs text-muted">{t("noSketch")}</p> : (
        <ol className="space-y-1.5" aria-label={t("sketch")}>
          {pts.map((p, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2">
              <span className="w-5 text-center text-xs font-semibold text-ink" aria-label={t("keyframeN", { n: i + 1 })}>{i + 1}</span>
              <span className="w-12 text-xs tabular text-muted" title={t("cpTimeHint")}>{fmt(keyframeTimeMs(p.t) / 1000, 2)} s</span>
              <NumberCell label="u" value={p.u} onChange={(u) => update((c) => { c.path.sketch_points = moveControlPoint(c.path.sketch_points, i, u, c.path.sketch_points[i].v); }, `cp:${i}:u`)} />
              <NumberCell label="v" value={p.v} onChange={(v) => update((c) => { c.path.sketch_points = moveControlPoint(c.path.sketch_points, i, c.path.sketch_points[i].u, v); }, `cp:${i}:v`)} />
              <button type="button" aria-label={`${t("removePoint")} ${i + 1}`} onClick={() => update((c) => { c.path.sketch_points = removeControlPoint(c.path.sketch_points, i); })}
                className="ml-auto flex min-h-[44px] min-w-[44px] items-center justify-center rounded-button text-muted hover:text-ink md:min-h-7 md:min-w-7">
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
              </button>
            </li>
          ))}
        </ol>
      )}
      {pts.length > 0 && <p className="text-xs text-muted">{t("cpTimeHint")}</p>}
      <Button size="sm" disabled={pts.length >= 5 || (!pts.length && !targetOut)} icon={<Plus className="h-3.5 w-3.5" />}
        onClick={() => update((c) => { c.path.sketch_points = c.path.sketch_points.length ? addControlPoint(c.path.sketch_points) : sketchAtTarget(targetOut!); })}>
        {t("addPoint")}
      </Button>
      <Segmented label={t("easing")} value={path.easing} onChange={(e) => update((c) => { c.path.easing = e; })}
        options={[{ value: "linear", label: t("easingLinear") }, { value: "ease_in_out", label: t("easingEase") }]} />
    </Section>
  );
}

function SceneSection() {
  const { t } = useI18n();
  const scene = useEditor((s) => s.config!.scene);
  const update = useEditor((s) => s.update);
  const id = useId();
  return (
    <Section title={t("secScene")}>
      <SliderField label={t("depthContrast")} value={scene.depth_contrast} min={0.5} max={1.5} step={0.01}
        onChange={(v) => update((c) => { c.scene.depth_contrast = v; }, "scene:contrast")} />
      <SliderField label={t("edgeProtection")} value={scene.edge_protection} min={0} max={1} step={0.01}
        onChange={(v) => update((c) => { c.scene.edge_protection = v; }, "scene:edge")} />
      <label htmlFor={id} className="flex min-h-[44px] items-center justify-between text-[13px] md:min-h-0">
        {t("showHoles")}
        <input id={id} type="checkbox" checked={scene.show_holes} onChange={(e) => update((c) => { c.scene.show_holes = e.target.checked; })}
          className="h-4 w-4 accent-[#66DCF2]" />
      </label>
    </Section>
  );
}

function RenderSection() {
  const { t } = useI18n();
  const mode = useEditor((s) => s.config!.mode);
  const gen = useEditor((s) => s.config!.generation);
  const update = useEditor((s) => s.update);
  const gate = useRenderGate();
  const seedId = useId();
  return (
    <Section title={t("secRender")}>
      <Segmented label={t("mode")} value={mode} onChange={(m) => update((c) => { c.mode = m; })}
        options={[{ value: "geometry", label: t("modeGeometry") }, { value: "cinematic", label: t("modeCinematic") }]} />
      <div className="flex justify-between text-[13px]"><span>{t("length")}</span><span className="text-muted tabular">{t("lengthFixed")}</span></div>
      <label htmlFor={seedId} className="flex items-center justify-between text-[13px]">
        {t("seed")}
        <input id={seedId} type="number" min={0} max={2147483647} step={1} value={gen.seed} inputMode="numeric"
          onChange={(e) => {
            const v = Math.floor(Number(e.target.value));
            if (e.target.value !== "" && Number.isFinite(v) && v >= 0 && v <= 2147483647) update((c) => { c.generation.seed = v; }, "gen:seed");
          }}
          className="min-h-[44px] w-28 rounded-button border border-border bg-panel px-2 text-right tabular-nums focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent md:min-h-0 md:py-0.5" />
      </label>
      {mode === "cinematic" && (
        <Segmented<ControlPreset> label={t("guidance")} value={gen.control_preset} onChange={(v) => update((c) => { c.generation.control_preset = v; })}
          options={[{ value: "low", label: t("guidanceLow") }, { value: "medium", label: t("guidanceMedium") }, { value: "high", label: t("guidanceHigh") }]} />
      )}
      {gate.canRender ? (
        <p className="flex items-center gap-1.5 text-xs text-muted" role="status"><ShieldCheck className="h-3.5 w-3.5 text-accent" aria-hidden />{t("renderReady")}</p>
      ) : (
        <div className="text-xs text-muted" role="status" data-region="render-block">
          <p className="font-medium text-ink">{t("renderBlocked")}</p>
          <ul className="list-disc pl-4">{gate.blockReasons.map((r) => <li key={r}>{t(reasonKey(r))}</li>)}</ul>
        </div>
      )}
    </Section>
  );
}

export function Inspector({ cam }: { cam: CameraPathState }) {
  return (
    <div data-region="inspector" className="-mx-3 flex flex-col">
      <div className="px-4 pb-3"><PathStatus cam={cam} /></div>
      <TargetSection />
      <CameraSection />
      <PathSection cam={cam} />
      <SceneSection />
      <RenderSection />
    </div>
  );
}

/** Mobile default: preset buttons + one strength slider for the preset's motion (+ push as a second slider). */
export function MobileEdit({ cam }: { cam: CameraPathState }) {
  const { t } = useI18n();
  const presets = usePresets();
  const path = useEditor((s) => s.config!.path);
  const update = useEditor((s) => s.update);
  const main = STRENGTH_PARAM[path.preset];
  return (
    <div className="space-y-4">
      <PathStatus cam={cam} />
      <PresetPicker />
      {main !== "push_ratio" && (
        <SliderField label={t("strength")} value={strengthOf(path, presets)} min={0} max={1} step={0.01} format={(v) => `${Math.round(v * 100)}%`}
          onChange={(v) => update((c) => { Object.assign(c.path, withStrength(c.path, v, presets)); }, "mobile:strength")} />
      )}
      <ParamSlider k="push_ratio" mobileLabel={main === "push_ratio" ? t("strength") : t("push")} />
      <LateralProposal />
      <div className="-mx-3"><PathSection cam={cam} /></div>
      <details className="-mx-3 border-t border-border">
        <summary className="flex min-h-[44px] cursor-pointer items-center px-4 text-[13px] text-muted">{t("moreSettings")}</summary>
        <TargetSection />
        <SceneSection />
        <RenderSection />
      </details>
    </div>
  );
}

