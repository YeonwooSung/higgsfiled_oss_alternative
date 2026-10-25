"use client";

import { Button, Section, Segmented, cx } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { Check, Loader2, RefreshCw, TriangleAlert } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../api/client";
import { useAssetUrl } from "../hooks/assets";
import { useI18n } from "../i18n";
import { assetById, assetsByRole, useEditor } from "../state/editorStore";
import { errorText } from "./errors";

export function LeftPanel({ compact = false }: { compact?: boolean }) {
  const { t } = useI18n();
  const project = useEditor((s) => s.project)!;
  const config = useEditor((s) => s.config)!;
  const update = useEditor((s) => s.update);
  const refresh = useEditor((s) => s.refresh);
  const presets = useQuery({ queryKey: ["presets"], queryFn: () => api.presets() });
  const fileInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const productAsset = assetById(project, config.product_asset_id) ??
    project.assets.filter((a) => a.role === "product").at(-1);
  const productUrl = useAssetUrl(productAsset);
  const suggestedMask = assetsByRole(project, "mask").find((m) => m.source_asset_id === productAsset?.id && m.meta.origin === "alpha");

  // Bind the uploaded product to the config once inspection made it ready.
  if (productAsset?.state === "ready" && config.product_asset_id !== productAsset.id) {
    queueMicrotask(() => update((c) => {
      c.product_asset_id = productAsset.id;
      c.mask_asset_id = null;
      c.core_mask_asset_id = null;
    }));
  }

  const replace = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      await api.uploadAsset(project.id, "product", file, file.name);
      await refresh();
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <Section title={t("product")}>
        <div className={cx("checker flex items-center justify-center overflow-hidden rounded-button border border-border", compact ? "h-28" : "h-44")}>
          {productUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={productUrl} alt={productAsset?.filename ?? "product"} className="max-h-full max-w-full object-contain" />
          ) : productAsset?.state === "rejected" ? (
            <p className="px-3 text-center text-xs text-danger"><TriangleAlert className="mx-auto mb-1 h-4 w-4" />{productAsset.error_code}</p>
          ) : (
            <p className="flex items-center gap-2 text-xs text-muted"><Loader2 className="h-4 w-4 animate-spin" />{t("inspecting")}</p>
          )}
        </div>
        {suggestedMask && !config.mask_asset_id && (
          <div className="rounded-button border border-emerald-200 bg-emerald-50 p-3 text-xs">
            <p className="mb-2">{t("suggestedMaskNote")}</p>
            <Button size="sm" variant="primary" icon={<Check className="h-3.5 w-3.5" />}
              onClick={() => update((c) => { c.mask_asset_id = suggestedMask.id; })}>
              {t("useSuggestedMask")}
            </Button>
          </div>
        )}
        {!config.mask_asset_id && productAsset?.state === "ready" && !suggestedMask && (
          <p className="text-xs text-muted">{t("maskRequired")}</p>
        )}
        <Button size="sm" variant="ghost" disabled={busy} icon={<RefreshCw className="h-3.5 w-3.5" />} onClick={() => fileInput.current?.click()}>
          {busy ? t("uploading") : t("replaceProduct")}
        </Button>
        <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void replace(f); e.target.value = ""; }} />
        {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      </Section>

      <Section title={t("preset")}>
        <div className="grid gap-2">
          {(presets.data?.presets ?? []).map((p) => {
            const active = config.scene.preset_id === p.id;
            return (
              <button key={p.id} type="button" aria-pressed={active}
                onClick={() => update((c) => {
                  c.scene.preset_id = p.id;
                  c.scene.accent_hex = p.default_accent;
                  c.shadow = { ...p.default_shadow };
                })}
                className={cx("flex min-h-[44px] items-center gap-3 rounded-button border p-2 text-left transition-colors duration-150",
                  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent",
                  active ? "border-accent bg-emerald-50" : "border-border hover:bg-app")}>
                <span className="h-8 w-8 shrink-0 rounded-[6px] border border-border" style={{ background: p.default_accent }} aria-hidden />
                <span>
                  <span className="block text-[13px] font-medium">{p.name}</span>
                  <span className="block text-xs text-muted">{p.description}</span>
                </span>
              </button>
            );
          })}
        </div>
      </Section>

      <Section title={t("output")}>
        <Segmented label={t("output")} value={config.output.aspect}
          options={[{ value: "9:16", label: t("portrait") }, { value: "16:9", label: t("landscape") }]}
          onChange={(aspect) => update((c) => {
            if (c.output.aspect !== aspect) {
              c.output.aspect = aspect;
              c.scene.background_asset_id = null;
              c.scene.background_video_asset_id = null;
            }
          })} />
        <p className="text-xs text-muted">{t("aspectChangeHint")}</p>
        <p className="text-xs text-muted">{t("duration")}</p>
      </Section>
    </div>
  );
}
