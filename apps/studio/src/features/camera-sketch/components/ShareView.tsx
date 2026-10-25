"use client";

/**
 * Sharing (shared §6): `ShareLink` creates / revokes a public link for one result video (explicit
 * action only; state = the server list `GET /projects/{id}/shares`, the URL is shown only right after creation); `ShareView` is the public `s/[token]` page and labels the video by its mode
 * ("Path render" / "AI generated", never exact camera control) and Mock profile.
 */
import { Badge, Button } from "@oc/ui";
import { shareRowsFor, sharesQueryKey } from "@oc/web-core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Link2, Video } from "lucide-react";
import { useState } from "react";
import type { PublicShareMeta } from "../../../shell/pickShareView";
import { api, ApiError } from "../api/client";
import { useI18n } from "../i18n";
import { useEditor } from "../state/editorStore";
import { ModeBadge } from "./badges";

export function ShareLink({ assetId, label }: { assetId: string; label: string }) {
  const { t } = useI18n();
  const projectId = useEditor((s) => s.project?.id);
  const qc = useQueryClient();
  // Server state, so a link stays revocable after the result view is left; the URL is known only right after
  // creating it here (the server keeps the token hash only).
  const list = useQuery({
    queryKey: sharesQueryKey(projectId ?? ""),
    queryFn: () => api.listShares(projectId!),
    enabled: !!projectId,
  });
  const [urls, setUrls] = useState<Record<string, string>>({});
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
      if (projectId) await qc.invalidateQueries({ queryKey: sharesQueryKey(projectId) });
      setBusy(false);
    }
  };
  const rows = shareRowsFor(list.data, assetId, urls, typeof location === "undefined" ? "" : location.origin);
  const shownError = error ?? (list.isError ? t("genericError") : null);
  return (
    <div className="space-y-2 text-xs" data-region="share">
      <h3 className="font-semibold uppercase tracking-wide text-muted">{t("shareLink")} · {label}</h3>
      {rows.length > 0 ? (
        rows.map(({ share, url: full }) => {
          return (
            <div key={share.id} className="space-y-2">
              {full ? (
                <>
                  <p className="text-muted">{t("shareExplain")}</p>
                  <div className="flex gap-2">
                    <input readOnly value={full} aria-label={t("shareLink")} onFocus={(e) => e.currentTarget.select()}
                      className="min-h-[44px] min-w-0 flex-1 rounded-button border border-border bg-app px-2 md:min-h-0 md:py-1" />
                    <Button size="sm" onClick={() => void navigator.clipboard?.writeText(full).then(() => setNote(t("copied")), () => {})}>{t("copy")}</Button>
                  </div>
                </>
              ) : (
                <p className="text-muted">{t("shareUrlHidden")}</p>
              )}
              <Button size="sm" variant="danger" disabled={busy}
                onClick={() => run(async () => { await api.revokeShare(share.id); setNote(t("shareRevoked")); })}>{t("revoke")}</Button>
            </div>
          );
        })
      ) : (
        <Button size="sm" variant="ghost" disabled={busy || !projectId || list.isPending} icon={<Link2 className="h-3.5 w-3.5" aria-hidden />}
          onClick={() => run(async () => {
            setNote(null);
            const created = await api.createShare(projectId!, assetId);
            setUrls((u) => ({ ...u, [created.id]: created.url }));
          })}>{t("shareCreate")}</Button>
      )}
      {note && <p role="status" className="text-muted">{note}</p>}
      {shownError && <p role="alert" className="flex items-start gap-1.5 text-danger"><CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /><span>{t("errorLabel")}: {shownError}</span></p>}
    </div>
  );
}

export function ShareView({ token, meta }: { token: string; meta: PublicShareMeta }) {
  const { t } = useI18n();
  // The public route redirects to a signed read URL; on the S3 backend the video plays cross-origin from the
  // bucket. Bucket CORS requirements: README "Camera Sketch" > "S3 / MinIO: bucket CORS".
  const src = `/api/v1/public/shares/${encodeURIComponent(token)}`;
  const m = meta;
  const mode = m?.mode ?? (m?.role === "cinematic_video" ? "cinematic" : m?.role === "geometry_video" ? "geometry" : null);
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col items-center gap-4 px-4 py-8">
      <p className="flex items-center gap-2 font-semibold"><Video className="h-4 w-4 text-accent" aria-hidden />{t("shareViewTitle")}</p>
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
