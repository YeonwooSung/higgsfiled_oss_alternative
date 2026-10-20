"use client";

/**
 * Sharing (shared §6): `ShareLink` creates / revokes a public link for one result video (explicit
 * action only); `ShareView` is the public `s/[token]` page and labels the video by its mode
 * ("Path render" / "AI generated", never exact camera control) and Mock profile.
 */
import { Badge, Button } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { CircleAlert, Link2, Video } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import { useI18n } from "../i18n";
import { useEditor } from "../state/editorStore";
import { ModeBadge } from "./badges";

export function ShareLink({ assetId, label }: { assetId: string; label: string }) {
  const { t } = useI18n();
  const projectId = useEditor((s) => s.project?.id);
  const [share, setShare] = useState<{ id: string; url: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("genericError"));
    } finally {
      setBusy(false);
    }
  };
  const full = share ? `${typeof location === "undefined" ? "" : location.origin}${share.url}` : "";
  return (
    <div className="space-y-2 text-xs" data-region="share">
      <h3 className="font-semibold uppercase tracking-wide text-muted">{t("shareLink")} · {label}</h3>
      {share ? (
        <>
          <p className="text-muted">{t("shareExplain")}</p>
          <div className="flex gap-2">
            <input readOnly value={full} aria-label={t("shareLink")} onFocus={(e) => e.currentTarget.select()}
              className="min-h-[44px] min-w-0 flex-1 rounded-button border border-border bg-app px-2 md:min-h-0 md:py-1" />
            <Button size="sm" onClick={() => void navigator.clipboard?.writeText(full).then(() => setNote(t("copied")), () => {})}>{t("copy")}</Button>
          </div>
          <Button size="sm" variant="danger" disabled={busy}
            onClick={() => run(async () => { await api.revokeShare(share.id); setShare(null); setNote(t("shareRevoked")); })}>{t("revoke")}</Button>
        </>
      ) : (
        <Button size="sm" variant="ghost" disabled={busy || !projectId} icon={<Link2 className="h-3.5 w-3.5" aria-hidden />}
          onClick={() => run(async () => { setNote(null); setShare(await api.createShare(projectId!, assetId)); })}>{t("shareCreate")}</Button>
      )}
      {note && <p role="status" className="text-muted">{note}</p>}
      {error && <p role="alert" className="flex items-start gap-1.5 text-danger"><CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /><span>{t("errorLabel")}: {error}</span></p>}
    </div>
  );
}

interface ShareMeta {
  mime: string;
  role?: string;
  mode?: "geometry" | "cinematic" | null;
  mock?: boolean;
  width?: number | null;
  height?: number | null;
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
  const mode = m?.mode ?? (m?.role === "cinematic_video" ? "cinematic" : m?.role === "geometry_video" ? "geometry" : null);
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col items-center gap-4 px-4 py-8">
      <p className="flex items-center gap-2 font-semibold"><Video className="h-4 w-4 text-accent" aria-hidden />{t("shareViewTitle")}</p>
      {meta.isError && <p role="alert" className="text-muted">{t("shareGone")}</p>}
      {m && (
        <div className="flex flex-wrap items-center justify-center gap-2" data-region="share-badges">
          {mode && <ModeBadge mode={mode} />}
          {m.mock && <Badge tone="mock" title={t("mockExplain")}>{t("mock")}</Badge>}
        </div>
      )}
      {m?.mime === "video/mp4" && <video src={src} controls muted autoPlay loop playsInline className="max-h-[80vh] max-w-full rounded-panel" />}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {m && m.mime !== "video/mp4" && <img src={src} alt={t("shareViewTitle")} className="max-h-[80vh] max-w-full rounded-panel" />}
      {mode === "cinematic" && <p className="max-w-xl text-center text-xs text-muted">{t("cinematicNote")}</p>}
    </main>
  );
}
