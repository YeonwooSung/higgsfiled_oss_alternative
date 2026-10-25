"use client";

import { Button, UploadDrop } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { CircleAlert, ImagePlus, Info, Sparkles, Video } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "../api/client";
import { fetchSample, startProjectWithScene } from "../flow/upload";
import { SAMPLES, type SampleId } from "../flow/scene";
import { useI18n } from "../i18n";
import { errorText } from "./errors";
import { LanguageToggle } from "./LanguageToggle";

const ACCEPT = "image/png,image/jpeg,image/webp";

export function StartScreen() {
  const { t } = useI18n();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Capabilities are fetched anonymously; used here only to warn when the backend is unreachable.
  useQuery({ queryKey: ["caps"], queryFn: () => api.cameraCapabilities(), staleTime: 60_000, retry: 0 });

  const start = async (load: () => Promise<File>, title: string) => {
    setError(null);
    setBusy(true);
    try {
      const file = await load();
      const project = await startProjectWithScene(file, title);
      router.push(`/camera/p/${project.id}`);
    } catch (e) {
      setError(errorText(e, t));
      setBusy(false);
    }
  };
  const startSample = (id: SampleId) => {
    const s = SAMPLES.find((x) => x.id === id)!;
    return start(() => fetchSample(s.url), t(s.titleKey));
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-6xl flex-col px-4 py-6 md:px-8">
      <header className="mb-10 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Video className="h-5 w-5 text-accent" aria-hidden />
          <span className="text-[15px] font-semibold">{t("appName")}</span>
        </div>
        <LanguageToggle />
      </header>
      <div className="grid gap-10 md:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <section>
          <h1 className="mb-2">{t("tagline")}</h1>
          <p className="mb-6 flex gap-2 text-muted"><Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />{t("recommendedInput")}</p>
          <UploadDrop accept={ACCEPT} disabled={busy} onFile={(f) => start(async () => f, f.name.replace(/\.[^.]+$/, ""))}>
            <ImagePlus className="h-8 w-8 text-accent" aria-hidden />
            <span className="text-[15px] font-medium">{busy ? t("uploading") : t("uploadScene")}</span>
            <span className="text-xs text-muted">{t("noSpendOnUpload")}</span>
          </UploadDrop>
          <div className="mt-4">
            <Button variant="secondary" icon={<Sparkles className="h-4 w-4" />} onClick={() => startSample("room")} disabled={busy}>
              {t("trySample")}
            </Button>
          </div>
          {error && (
            <p role="alert" className="mt-4 flex items-start gap-2 rounded-button border border-red-200 bg-red-50 px-3 py-2 text-danger">
              <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <span><span className="font-semibold">{t("errorLabel")}: </span>{error}</span>
            </p>
          )}
        </section>
        <section aria-labelledby="examples">
          <h2 id="examples" className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{t("chooseSample")}</h2>
          <p className="mb-3 text-xs text-muted">{t("sampleHint")}</p>
          <ul className="grid grid-cols-3 gap-3">
            {SAMPLES.map((s) => (
              <li key={s.id}>
                <button type="button" disabled={busy} onClick={() => startSample(s.id)}
                  className="group block w-full overflow-hidden rounded-panel border border-border bg-panel text-left hover:border-accent disabled:opacity-60">
                  <div className="flex h-36 items-center justify-center bg-stage">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={s.url} alt="" className="max-h-full max-w-full object-contain" />
                  </div>
                  <span className="block min-h-[44px] px-3 py-2 text-[13px] font-medium">{t(s.titleKey)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </main>
  );
}
