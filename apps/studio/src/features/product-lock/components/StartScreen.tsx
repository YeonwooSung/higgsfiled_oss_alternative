"use client";

import type { Preset } from "@oc/contracts";
import { Button, UploadDrop } from "@oc/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ImagePlus, Lock, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "../api/client";
import { PROJECTS_QUERY_KEY } from "../../../shell/tools";
import { useI18n } from "../i18n";
import { errorText } from "./errors";
import { TopBar } from "../../../shell/TopBar";

const ACCEPT = "image/png,image/jpeg,image/webp";

function PresetCard({ preset }: { preset: Preset }) {
  const dark = preset.id.startsWith("neon");
  return (
    <div className="overflow-hidden rounded-panel border border-border bg-panel">
      <div className="relative aspect-[9/12]" style={{ background: dark
        ? `linear-gradient(180deg,#121214 0%,#1d1b24 62%,#0b0b0d 62%), radial-gradient(${preset.default_accent} 0 0)`
        : `linear-gradient(180deg, ${preset.default_accent} 0%, #f3f1ec 62%, #dcd8cf 62%)` }}>
        {dark && <div className="absolute inset-x-0 top-[28%] h-[2px]" style={{ background: preset.default_accent, boxShadow: `0 0 12px ${preset.default_accent}` }} />}
        {preset.id.startsWith("botanical") && <div className="absolute inset-0 opacity-50" style={{ background: "radial-gradient(circle at 20% 25%, #6f9f5a 0 12%, transparent 13%), radial-gradient(circle at 80% 20%, #7fb36a 0 10%, transparent 11%)" }} />}
        <div className="absolute bottom-[22%] left-1/2 h-[34%] w-[26%] -translate-x-1/2 rounded-md border border-white/60 bg-white/30 backdrop-blur-[1px]" aria-hidden />
      </div>
      <div className="p-3">
        <p className="text-[13px] font-medium">{preset.name}</p>
        <p className="text-xs text-muted">{preset.description}</p>
      </div>
    </div>
  );
}

export function StartScreen() {
  const { t } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const presets = useQuery({ queryKey: ["presets"], queryFn: () => api.presets() });

  const start = async (file: File, title: string) => {
    setError(null);
    try {
      setBusy(t("uploading"));
      await api.startSession();
      const project = await api.createProject(title.slice(0, 120) || "Untitled");
      void queryClient.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY });
      await api.uploadAsset(project.id, "product", file, file.name);
      router.push(`/product/p/${project.id}`);
    } catch (e) {
      setError(errorText(e, t));
      setBusy(null);
    }
  };

  const trySample = async () => {
    const blob = await fetch("/samples/product/can_lock_cola.png").then((r) => r.blob());
    await start(new File([blob], "can_lock_cola.png", { type: "image/png" }), "Lock Cola sample");
  };

  return (
    <div className="flex min-h-screen flex-col">
      <TopBar />
      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-4 py-6 md:px-8">
      <div className="grid gap-10 md:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <section>
          <h1 className="mb-2">{t("tagline")}</h1>
          <p className="mb-6 text-muted">{t("uploadHint")}</p>
          <UploadDrop accept={ACCEPT} disabled={!!busy} onFile={(f) => start(f, f.name.replace(/\.[^.]+$/, ""))}>
            <ImagePlus className="h-8 w-8 text-accent" aria-hidden />
            <span className="text-[15px] font-medium">{busy ?? t("uploadProduct")}</span>
            <span className="text-xs text-muted">{t("noSpendOnUpload")}</span>
          </UploadDrop>
          <div className="mt-4 flex items-center gap-3">
            <Button variant="secondary" icon={<Sparkles className="h-4 w-4" />} onClick={trySample} disabled={!!busy}>
              {t("trySample")}
            </Button>
          </div>
          {error && <p role="alert" className="mt-4 rounded-button border border-red-200 bg-red-50 px-3 py-2 text-danger">{error}</p>}
        </section>
        <section aria-labelledby="gallery">
          <div className="mb-3 flex items-center gap-2">
            <h2 id="gallery" className="text-xs font-semibold uppercase tracking-wide text-muted">{t("presetGallery")}</h2>
          </div>
          <p className="mb-3 text-xs text-muted">{t("presetIllustration")}</p>
          <div className="grid grid-cols-3 gap-3">
            {(presets.data?.presets ?? []).map((p) => <PresetCard key={p.id} preset={p} />)}
          </div>
        </section>
      </div>
    </main>
    </div>
  );
}
