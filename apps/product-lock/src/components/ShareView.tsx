"use client";

import { useQuery } from "@tanstack/react-query";
import { Lock } from "lucide-react";
import { useI18n } from "../i18n";

export function ShareView({ token }: { token: string }) {
  const { t } = useI18n();
  const meta = useQuery({
    queryKey: ["share", token],
    queryFn: async () => {
      const r = await fetch(`/api/v1/public/shares/${encodeURIComponent(token)}/meta`);
      if (!r.ok) throw new Error("gone");
      return (await r.json()) as { mime: string };
    },
    retry: false,
  });
  const src = `/api/v1/public/shares/${encodeURIComponent(token)}`;
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col items-center gap-4 px-4 py-8">
      <p className="flex items-center gap-2 font-semibold"><Lock className="h-4 w-4 text-accent" />{t("shareViewTitle")}</p>
      {meta.isError && <p role="alert" className="text-muted">{t("shareGone")}</p>}
      {meta.data?.mime === "video/mp4" && <video src={src} controls muted autoPlay loop playsInline className="max-h-[80vh] rounded-panel shadow" />}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {meta.data && meta.data.mime !== "video/mp4" && <img src={src} alt={t("shareViewTitle")} className="max-h-[80vh] rounded-panel" />}
    </main>
  );
}
