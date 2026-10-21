"use client";

/**
 * Public `s/[token]` page: the shared result video with the Mock badge when it came from the mock profile.
 * (The share action and the Quick / Generated plate badges arrive with the result view.)
 */
import { Badge } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { useI18n } from "../i18n";

interface ShareMeta {
  mime: string;
  role?: string;
  mock?: boolean;
}

export function ShareView({ token }: { token: string }) {
  const { t } = useI18n();
  const meta = useQuery({
    queryKey: ["share", token],
    queryFn: async () => {
      const r = await fetch(`/api/v1/public/shares/${encodeURIComponent(token)}/meta`);
      if (!r.ok) throw new Error("gone");
      return (await r.json()) as ShareMeta;
    },
    retry: false,
  });
  const src = `/api/v1/public/shares/${encodeURIComponent(token)}`;
  const m = meta.data;
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col items-center gap-4 px-4 py-8">
      <p className="flex items-center gap-2 font-semibold"><Sparkles className="h-4 w-4 text-accent" aria-hidden />{t("shareViewTitle")}</p>
      {meta.isError && <p role="alert" className="text-muted">{t("shareGone")}</p>}
      {m?.mock && <Badge tone="mock" title={t("mockExplain")}>{t("mock")}</Badge>}
      {m?.mime === "video/mp4" && <video src={src} controls muted playsInline loop className="max-h-[80vh] max-w-full rounded-panel" />}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {m && m.mime !== "video/mp4" && <img src={src} alt={t("shareViewTitle")} className="max-h-[80vh] max-w-full rounded-panel" />}
    </main>
  );
}
