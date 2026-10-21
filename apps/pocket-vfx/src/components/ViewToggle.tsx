"use client";

/** Stage view switch (top right of the stage): Source / Mask / Effect matte / Composite, plus "Effects off". */
import { cx } from "@oc/ui";
import { Eye, EyeOff } from "lucide-react";
import type { PreviewView } from "../model/preview";
import { useI18n } from "../i18n";
import { useEditor } from "../state/editorStore";

const VIEWS: { id: PreviewView; label: "viewSource" | "viewMask" | "viewMatte" | "viewComposite" }[] = [
  { id: "source", label: "viewSource" },
  { id: "mask", label: "viewMask" },
  { id: "matte", label: "viewMatte" },
  { id: "composite", label: "viewComposite" },
];

export function ViewToggle({ hasMask }: { hasMask: boolean }) {
  const { t } = useI18n();
  const view = useEditor((s) => s.view);
  const off = useEditor((s) => s.effectsOff);
  const { setView, setEffectsOff } = useEditor.getState();
  return (
    <div className="flex flex-wrap items-center justify-end gap-1" data-region="view-toggle">
      <div role="radiogroup" aria-label={t("viewLabel")} className="flex rounded-button border border-border bg-app/90 p-0.5 backdrop-blur">
        {VIEWS.map((v) => (
          <button key={v.id} type="button" role="radio" aria-checked={view === v.id} onClick={() => setView(v.id)}
            title={v.id === "mask" && !hasMask ? t("viewMaskNone") : undefined}
            className={cx("min-h-[44px] min-w-[44px] rounded-[6px] px-2 text-xs md:min-h-7 md:min-w-0",
              view === v.id ? "bg-panel font-medium text-ink" : "text-muted hover:text-ink")}>
            {t(v.label)}
          </button>
        ))}
      </div>
      <button type="button" aria-pressed={off} onClick={() => setEffectsOff(!off)}
        className={cx("flex min-h-[44px] items-center gap-1 rounded-button border px-2 text-xs md:min-h-8",
          off ? "border-accent bg-accent text-app" : "border-border bg-app/90 text-muted hover:text-ink")}>
        {off ? <EyeOff className="h-3.5 w-3.5" aria-hidden /> : <Eye className="h-3.5 w-3.5" aria-hidden />}
        {t("effectsOff")}
      </button>
    </div>
  );
}
