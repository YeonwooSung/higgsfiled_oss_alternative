"use client";

import { Button, UploadDrop } from "@oc/ui";
import { CircleAlert, Film, Info, Sparkles, VolumeX } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ACCEPT, clipFileProblem, fetchSample, SAMPLES, startProjectWithClip, type SampleId } from "../flow/upload";
import { useI18n } from "../i18n";
import { errorText } from "./errors";
import { TopBar } from "./TopBar";

/** A file the client already knows the server would refuse (type / size); its message is user text. */
class ClipFileError extends Error {}

/** Screen A, step 1 (spec §3): Upload clip / Try sample. Both use the same upload path; nothing is generated. */
export function StartScreen() {
  const { t } = useI18n();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = async (load: () => Promise<File>, title: string) => {
    setError(null);
    setBusy(true);
    try {
      const file = await load();
      const problem = clipFileProblem(file);
      if (problem) throw new ClipFileError(t(problem));
      const project = await startProjectWithClip(file, title);
      router.push(`/p/${project.id}`);
    } catch (e) {
      setError(e instanceof ClipFileError ? e.message : errorText(e, t));
      setBusy(false);
    }
  };
  const startSample = (id: SampleId) => {
    const s = SAMPLES.find((x) => x.id === id)!;
    return start(() => fetchSample(s.url), t(s.titleKey));
  };

  return (
    <div className="flex min-h-screen flex-col">
      <TopBar />
      <main className="mx-auto grid w-full max-w-6xl flex-1 content-start gap-10 px-4 py-8 md:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] md:px-8 md:py-12">
        <section>
          <h1 className="mb-2">{t("tagline")}</h1>
          <p className="mb-3 text-muted">{t("intro")}</p>
          <p className="mb-6 flex gap-2 text-xs text-muted"><Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />{t("recommendedInput")}</p>
          <UploadDrop accept={ACCEPT} disabled={busy} onFile={(f) => start(async () => f, f.name.replace(/\.[^.]+$/, ""))}>
            <Film className="h-8 w-8 text-accent" aria-hidden />
            <span className="text-[15px] font-medium">{busy ? t("uploading") : t("uploadClip")}</span>
            <span className="text-xs text-muted">{t("uploadHint")}</span>
            <span className="text-xs text-muted">{t("noSpendOnUpload")}</span>
          </UploadDrop>
          <div className="mt-4">
            <Button variant="secondary" icon={<Sparkles className="h-4 w-4" aria-hidden />} onClick={() => startSample("stomp")} disabled={busy}>
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
        <section aria-labelledby="samples">
          <h2 id="samples" className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{t("chooseSample")}</h2>
          <p className="mb-3 flex items-center gap-1.5 text-xs text-muted"><VolumeX className="h-3.5 w-3.5" aria-hidden />{t("sampleHint")} {t("sampleSilent")}</p>
          <ul className="grid grid-cols-2 gap-3">
            {SAMPLES.map((s) => (
              <li key={s.id}>
                <button type="button" disabled={busy} onClick={() => startSample(s.id)}
                  className="group block w-full overflow-hidden rounded-card border border-border bg-panel text-left transition-colors duration-150 hover:border-accent disabled:opacity-60">
                  <div className="flex h-48 items-center justify-center bg-stage">
                    {/* First frame only; nothing autoplays (reduced motion). */}
                    <video src={`${s.url}#t=0.5`} muted playsInline preload="metadata" aria-hidden tabIndex={-1}
                      className="max-h-full max-w-full object-contain" style={{ aspectRatio: s.ratio }} />
                  </div>
                  <span className="block min-h-[44px] px-3 py-2 text-[13px] font-medium">{t(s.titleKey)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  );
}
