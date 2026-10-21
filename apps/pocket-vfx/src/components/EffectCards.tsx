"use client";

/**
 * The three effect cards (spec §3, left 220 px). Choosing a card applies the preset's default controls around the
 * current peak. Blink Out stays disabled until an empty background frame (role `clean_plate`) has been uploaded and
 * inspected; the card states the condition (fixed camera + empty frame) and offers that upload. Once selected it
 * opens the Clean plate tool to check the alignment and can replace the frame. The background it reveals is the
 * user's photo: the copy never promises a restored background. Whether the camera is fixed and the photo lines up is
 * checked by validate / the render (`clean_plate_mismatch`, shown on the Render button).
 */
import type { VfxPreset, VfxPresetId } from "@oc/contracts";
import { cx } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { ImageIcon, ImagePlus, Loader2, Sparkles, Waves, Wind } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../api/client";
import { useI18n } from "../i18n";
import { assetById, useEditor } from "../state/editorStore";
import { errorText } from "./errors";

const ICONS: Record<VfxPresetId, typeof Waves> = { impact_wave_v1: Waves, energy_aura_v1: Sparkles, blink_out_v1: Wind };
const HINTS = { impact_wave_v1: "hintImpact", energy_aura_v1: "hintAura", blink_out_v1: "hintBlink" } as const;
export const CLEAN_PLATE_MIMES = "image/jpeg,image/png,image/webp";

export function usePresets() {
  return useQuery({ queryKey: ["vfx-presets"], queryFn: () => api.presets(), staleTime: Infinity }).data?.presets ?? [];
}

export function EffectCards({ projectId }: { projectId: string }) {
  const { t, locale } = useI18n();
  const presets = usePresets();
  const presetId = useEditor((s) => s.config!.effect.preset_id);
  const project = useEditor((s) => s.project);
  const cleanId = useEditor((s) => s.config!.clean_plate_asset_id);
  const clean = assetById(project, cleanId);
  const file = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = async (f: File) => {
    setUploading(true);
    setError(null);
    try {
      const { asset } = await api.uploadAsset(projectId, "clean_plate", f, f.name);
      // linking an upload is not an undo step (undo must not orphan the uploaded frame)
      useEditor.getState().link((c) => { c.clean_plate_asset_id = asset.id; });
      await useEditor.getState().refresh();
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setUploading(false);
    }
  };

  const available = (p: VfxPreset) => p.availability.status === "available" || clean?.state === "ready";
  return (
    <section aria-label={t("effects")} className="flex flex-col gap-2" data-region="effect-cards">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{t("effects")}</h2>
      <div role="radiogroup" aria-label={t("effects")} className="grid grid-cols-3 gap-2 md:grid-cols-1">
        {presets.map((p) => {
          const Icon = ICONS[p.id] ?? Sparkles;
          const ok = available(p);
          const selected = presetId === p.id;
          return (
            <div key={p.id} className={cx("flex flex-col gap-1 rounded-card border p-2 md:p-3",
              selected ? "border-accent bg-app" : "border-border", !ok && "opacity-80")}>
              <button type="button" role="radio" aria-checked={selected} disabled={!ok}
                onClick={() => useEditor.getState().selectPreset(p)}
                className="flex min-h-[44px] flex-col items-start gap-1 text-left disabled:cursor-not-allowed">
                <span className={cx("flex items-center gap-1.5 text-[13px] font-medium", selected ? "text-accent" : ok ? "text-ink" : "text-muted")}>
                  <Icon className="h-4 w-4 shrink-0" aria-hidden />
                  {locale === "ko" ? p.label_ko : p.label_en}
                </span>
                <span className="hidden text-xs text-muted md:block">{t(HINTS[p.id])}</span>
              </button>
              {p.availability.status === "requires_clean_plate" && !ok && (
                <div className="flex flex-col gap-1" data-region="blink-condition">
                  <p className="text-[11px] text-warning">{t("blinkNeeds")}</p>
                  <button type="button" onClick={() => file.current?.click()} disabled={uploading || clean?.state === "uploaded"}
                    className="flex min-h-[44px] items-center gap-1 text-xs font-medium text-accent hover:underline disabled:text-muted md:min-h-8">
                    {uploading || clean?.state === "uploaded" ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <ImagePlus className="h-3.5 w-3.5" aria-hidden />}
                    {clean?.state === "uploaded" ? t("cleanPlateChecking") : t("addCleanPlate")}
                  </button>
                  {clean?.state === "rejected" && <p role="alert" className="text-[11px] text-danger">{t("cleanPlateRejected")}</p>}
                </div>
              )}
              {p.availability.status === "requires_clean_plate" && ok && selected && (
                <div className="flex flex-col gap-1" data-region="blink-clean-plate">
                  <p className="text-[11px] text-muted">{t("blinkPhotoNote")}</p>
                  <div className="flex flex-wrap gap-x-3">
                    <button type="button" onClick={() => useEditor.getState().setTool("clean_plate")}
                      className="flex min-h-[44px] items-center gap-1 text-xs font-medium text-accent hover:underline md:min-h-8">
                      <ImageIcon className="h-3.5 w-3.5" aria-hidden />{t("cleanPlateCheck")}
                    </button>
                    <button type="button" onClick={() => file.current?.click()} disabled={uploading}
                      className="flex min-h-[44px] items-center gap-1 text-xs font-medium text-accent hover:underline disabled:text-muted md:min-h-8">
                      {uploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <ImagePlus className="h-3.5 w-3.5" aria-hidden />}
                      {t("replaceCleanPlate")}
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <input ref={file} type="file" accept={CLEAN_PLATE_MIMES} className="sr-only" tabIndex={-1} aria-hidden
        onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void upload(f); }} />
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    </section>
  );
}
