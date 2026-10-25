"use client";

import { Button, Section, Segmented, SliderField } from "@oc/ui";
import { Brush, Eraser, MousePointerClick, ShieldCheck, Undo2, Wand2, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { maskToPngBlob } from "../hooks/assets";
import { useI18n } from "../i18n";
import { BRUSH_MAX, BRUSH_MIN } from "../model/mask";
import { waitForJob } from "@oc/web-core";
import { useEditor, type Tool } from "../state/editorStore";
import { startJob } from "../state/jobs";
import { useMask } from "../state/maskStore";
import { errorText } from "./errors";

const pct = (v: number) => `${Math.round(v * 100)}%`;

function Tools() {
  const { t } = useI18n();
  const { tool, setTool, brush, setBrush, update, config, project } = useEditor();
  const mask = useMask();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const tools: { id: Tool; label: string; icon: React.ReactNode }[] = [
    { id: "select", label: t("toolSelect"), icon: <MousePointerClick className="h-4 w-4" /> },
    { id: "add", label: t("toolAdd"), icon: <Brush className="h-4 w-4" /> },
    { id: "remove", label: t("toolRemove"), icon: <Eraser className="h-4 w-4" /> },
    { id: "protect", label: t("toolProtect"), icon: <ShieldCheck className="h-4 w-4" /> },
  ];

  const runSegmentation = async () => {
    setBusy(true);
    setMessage(null);
    const startedMask = config!.mask_asset_id;
    try {
      const job = await startJob("product.segment", { points: mask.points });
      if (job.state !== "succeeded") throw new Error(job.error?.message ?? "failed");
      // A late result must not overwrite a mask the user edited meanwhile.
      const current = useEditor.getState().config!;
      if (current.mask_asset_id !== startedMask || useMask.getState().dirty) {
        setMessage(t("segmentationStale"));
      } else {
        update((c) => { c.mask_asset_id = job.result!.mask_asset_id as string; });
        mask.clearPoints();
      }
    } catch (e) {
      setMessage(errorText(e, t));
    } finally {
      setBusy(false);
    }
  };

  const applyMask = async () => {
    if (!mask.buffer) return;
    setBusy(true);
    setMessage(null);
    try {
      const { asset, inspect_job_id } = await api.uploadAsset(project!.id, "mask", await maskToPngBlob(mask.buffer), "mask.png");
      await waitForJob((id) => api.getJob(id), inspect_job_id); // keeps the strokes if inspection fails
      mask.markClean();
      update((c) => { c.mask_asset_id = asset.id; });
      await useEditor.getState().refresh();
    } catch (e) {
      setMessage(errorText(e, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title={t("tools")}>
      <div className="grid grid-cols-2 gap-1.5" role="toolbar" aria-label={t("tools")}>
        {tools.map((x) => (
          <Button key={x.id} size="sm" variant={tool === x.id ? "primary" : "secondary"} icon={x.icon}
            aria-pressed={tool === x.id} onClick={() => setTool(x.id)}>
            {x.label}
          </Button>
        ))}
      </div>
      {(tool === "add" || tool === "remove") && (
        <SliderField label={t("brushSize")} value={brush} min={BRUSH_MIN} max={BRUSH_MAX} step={1} onChange={setBrush} />
      )}
      {tool === "select" && (
        <>
          <p className="text-xs text-muted">{t("segmentHint")}</p>
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" icon={<Wand2 className="h-4 w-4" />} disabled={busy || mask.points.length === 0}
              onClick={runSegmentation}>{t("runSegmentation")}</Button>
            {mask.points.length > 0 && <Button size="sm" variant="ghost" icon={<X className="h-4 w-4" />} onClick={mask.clearPoints}>{mask.points.length}</Button>}
          </div>
        </>
      )}
      {tool === "protect" && (
        <div className="space-y-2">
          {config!.protect_boxes.map((b, i) => (
            <div key={i} className="flex items-center justify-between rounded-button border border-border px-2 py-1 text-xs tabular-nums">
              <span>{pct(b.x)}, {pct(b.y)} · {pct(b.w)} × {pct(b.h)}</span>
              <button type="button" aria-label="remove" className="p-1 text-muted hover:text-danger"
                onClick={() => update((c) => { c.protect_boxes.splice(i, 1); })}><X className="h-3.5 w-3.5" /></button>
            </div>
          ))}
        </div>
      )}
      {mask.dirty && (
        <div className="flex gap-2">
          <Button size="sm" variant="primary" disabled={busy} onClick={applyMask}>{t("applyMask")}</Button>
          <Button size="sm" variant="ghost" icon={<Undo2 className="h-4 w-4" />} onClick={mask.undo} disabled={!mask.undoStack.length}>{t("undo")}</Button>
          <Button size="sm" variant="ghost" onClick={() => mask.setBuffer(null, null)}>{t("revertMask")}</Button>
        </div>
      )}
      <p className="text-xs text-muted">{t("thinWarning")}</p>
      {message && <p role="status" className="text-xs text-amber-800">{message}</p>}
    </Section>
  );
}

export function Inspector({ only }: { only?: "scene" | "output" }) {
  const { t } = useI18n();
  const config = useEditor((s) => s.config)!;
  const update = useEditor((s) => s.update);
  const tr = config.transform;
  const show = (g: string) => !only || only === g;

  return (
    <div>
      {!only && <Tools />}
      {!only && (
        <Section title={t("product")}>
          <SliderField label={t("positionX")} value={tr.center_x} min={0} max={1} step={0.005}
            onChange={(v) => update((c) => { c.transform.center_x = v; })} format={pct} />
          <SliderField label={t("positionY")} value={tr.center_y} min={0} max={1} step={0.005}
            onChange={(v) => update((c) => { c.transform.center_y = v; })} format={pct} />
          <SliderField label={t("width")} value={tr.width_ratio} min={0.15} max={0.7} step={0.01}
            onChange={(v) => update((c) => { c.transform.width_ratio = v; })} format={pct} />
          <SliderField label={t("feather")} value={tr.feather_px} min={0} max={4} step={0.5}
            onChange={(v) => update((c) => { c.transform.feather_px = v; })} />
        </Section>
      )}
      {show("scene") && (
        <Section title={t("scene")}>
          <label className="flex items-center justify-between text-[13px]">
            {t("accent")}
            <input type="color" value={config.scene.accent_hex} className="h-8 w-14 cursor-pointer rounded border border-border"
              onChange={(e) => update((c) => { c.scene.accent_hex = e.target.value.toUpperCase(); })} />
          </label>
          <label className="block text-[13px]">
            <span className="mb-1 flex justify-between">{t("extraPrompt")}<span className="tabular-nums text-muted">{config.scene.extra_prompt.length}/300</span></span>
            <textarea maxLength={300} rows={3} value={config.scene.extra_prompt}
              placeholder="e.g. warm late-afternoon light, soft haze"
              onChange={(e) => update((c) => { c.scene.extra_prompt = e.target.value; })}
              className="w-full rounded-button border border-border bg-panel p-2 text-[13px] focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent" />
            <span className="text-xs text-muted">{t("extraPromptHint")}</span>
          </label>
          <label className="flex items-center justify-between text-[13px]">
            {t("category")}
            <select value={config.scene.category} onChange={(e) => update((c) => { c.scene.category = e.target.value as typeof c.scene.category; })}
              className="rounded-button border border-border bg-panel px-2 py-1">
              {["beverage", "cosmetics", "food", "household", "electronics", "other"].map((x) => <option key={x}>{x}</option>)}
            </select>
          </label>
        </Section>
      )}
      {show("scene") && (
        <Section title={t("lighting")}>
          <SliderField label={t("brightness")} value={config.lighting.background_brightness} min={-0.2} max={0.2} step={0.01}
            onChange={(v) => update((c) => { c.lighting.background_brightness = v; })} format={pct} />
          <label className="flex items-center justify-between text-[13px]">
            {t("shadow")}
            <input type="checkbox" checked={config.shadow.enabled} className="h-4 w-4 accent-accent"
              onChange={(e) => update((c) => { c.shadow.enabled = e.target.checked; })} />
          </label>
          <SliderField label={t("shadowOpacity")} value={config.shadow.opacity} min={0} max={0.6} step={0.01} disabled={!config.shadow.enabled}
            onChange={(v) => update((c) => { c.shadow.opacity = v; })} format={pct} />
          <SliderField label={t("shadowBlur")} value={config.shadow.blur_px} min={0} max={40} step={1} disabled={!config.shadow.enabled}
            onChange={(v) => update((c) => { c.shadow.blur_px = v; })} />
        </Section>
      )}
      {show("output") && (
        <Section title={t("motion")}>
          <Segmented label={t("motion")} value={config.motion}
            options={[{ value: "subtle", label: t("subtle") }, { value: "balanced", label: t("balanced") }]}
            onChange={(v) => update((c) => { c.motion = v; })} />
          <p className="text-xs text-muted">{t("motionHint")}</p>
        </Section>
      )}
    </div>
  );
}
