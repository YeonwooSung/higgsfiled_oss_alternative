"use client";

import type { CameraPresetId } from "@oc/contracts";
import { cx } from "@oc/ui";
import { Crosshair, Move, PenLine } from "lucide-react";
import { usePresets } from "../hooks/cameraPath";
import { useI18n } from "../i18n";
import { switchPreset } from "../model/sketch";
import { useEditor, type Tool } from "../state/editorStore";
import { AnalyzePanel, JobStatusList } from "./ScenePanel";

const TOOLS: { id: Exclude<Tool, "none">; icon: typeof Crosshair; label: "toolTarget" | "toolSketch" | "toolKeyframe"; hint: "toolHintTarget" | "toolHintSketch" | "toolHintKeyframe" }[] = [
  { id: "target", icon: Crosshair, label: "toolTarget", hint: "toolHintTarget" },
  { id: "sketch", icon: PenLine, label: "toolSketch", hint: "toolHintSketch" },
  { id: "keyframe", icon: Move, label: "toolKeyframe", hint: "toolHintKeyframe" },
];

/** Select target / Sketch framing / Move keyframe (Escape cancels). Needs the depth preview. */
export function ToolPicker() {
  const { t } = useI18n();
  const tool = useEditor((s) => s.tool);
  const setTool = useEditor((s) => s.setTool);
  const hasDepth = useEditor((s) => !!s.config?.depth_asset_id);
  const active = TOOLS.find((x) => x.id === tool);
  return (
    <section aria-label={t("toolsLabel")} className="space-y-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{t("toolsLabel")}</h2>
      <div role="radiogroup" aria-label={t("toolsLabel")} className="grid grid-cols-3 gap-1">
        {TOOLS.map(({ id, icon: Icon, label }) => (
          <button key={id} type="button" role="radio" aria-checked={tool === id} disabled={!hasDepth} title={t(label)}
            onClick={() => {
              setTool(tool === id ? "none" : id);
              // The target is picked on the source view (t = 0), where output uv maps back to the source image.
              if (id === "target") useEditor.setState({ playheadMs: 0, playing: false });
            }}
            className={cx("flex min-h-[44px] flex-col items-center justify-center gap-0.5 rounded-button border px-1 text-[11px] leading-tight md:min-h-[52px]",
              tool === id ? "border-accent bg-app text-accent" : "border-border text-muted hover:text-ink disabled:opacity-50 disabled:hover:text-muted")}>
            <Icon className="h-4 w-4" aria-hidden />
            {t(label)}
          </button>
        ))}
      </div>
      <p className="text-xs text-muted" aria-live="polite">{active ? t(active.hint) : hasDepth ? t("toolNoneHint") : t("noDepth")}</p>
    </section>
  );
}

/** Path preset buttons; switching zeroes params the new preset does not allow (one undo step). */
export function PresetPicker() {
  const { t, locale } = useI18n();
  const presets = usePresets();
  const path = useEditor((s) => s.config?.path);
  const update = useEditor((s) => s.update);
  if (!path) return null;
  const pick = (id: CameraPresetId) => {
    if (id === path.preset) return;
    update((c) => Object.assign(c.path, switchPreset(c.path, id, presets)));
  };
  return (
    <section aria-label={t("preset")} className="space-y-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{t("preset")}</h2>
      <div role="radiogroup" aria-label={t("preset")} className="grid grid-cols-3 gap-1 md:grid-cols-1">
        {presets.map((p) => (
          <button key={p.id} type="button" role="radio" aria-checked={path.preset === p.id} onClick={() => pick(p.id)}
            className={cx("min-h-[44px] rounded-button border px-2 text-[13px] md:min-h-9 md:text-left",
              path.preset === p.id ? "border-accent bg-app font-medium text-accent" : "border-border text-muted hover:text-ink")}>
            {locale === "ko" ? p.label_ko : p.label_en}
          </button>
        ))}
      </div>
    </section>
  );
}

export function LeftPanel() {
  return (
    <div className="space-y-5 p-3">
      <AnalyzePanel />
      <ToolPicker />
      <PresetPicker />
      <JobStatusList />
    </div>
  );
}
