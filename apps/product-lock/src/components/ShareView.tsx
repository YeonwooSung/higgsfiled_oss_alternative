"use client";

/**
 * Sharing: `ShareLink` creates / revokes a public link to the result video on an explicit click. Its state is the
 * server list (`GET /projects/{id}/shares`), so a link made earlier stays listed and revocable after the result view
 * is left; the URL itself is shown only right after creating it (the server stores the token hash only).
 * `ShareView` is the public `s/[token]` page.
 */
import { Button } from "@oc/ui";
import { shareRowsFor, sharesQueryKey } from "@oc/web-core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, Lock } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { useI18n } from "../i18n";
import { useEditor } from "../state/editorStore";
import { errorText } from "./errors";

export function ShareLink({ assetId }: { assetId: string | undefined }) {
  const { t } = useI18n();
  const projectId = useEditor((s) => s.project?.id);
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: sharesQueryKey(projectId ?? ""),
    queryFn: () => api.listShares(projectId!),
    enabled: !!projectId,
  });
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rows = assetId ? shareRowsFor(list.data, assetId, urls, typeof location === "undefined" ? "" : location.origin) : [];
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      if (projectId) await qc.invalidateQueries({ queryKey: sharesQueryKey(projectId) });
      setBusy(false);
    }
  };
  const shownError = error ?? (list.isError ? t("genericError") : null);
  return (
    <div className="space-y-2 text-xs" data-region="share">
      {rows.length > 0 ? (
        rows.map(({ share, url }) => (
          <div key={share.id} className="space-y-2">
            {url ? (
              <>
                <p>{t("shareCreated")}</p>
                <div className="flex gap-2">
                  <input readOnly value={url} aria-label={t("shareLink")} onFocus={(e) => e.currentTarget.select()}
                    className="min-w-0 flex-1 rounded-button border border-border px-2 py-1" />
                  <Button size="sm" onClick={() => void navigator.clipboard?.writeText(url).then(() => setNote(t("copied")), () => {})}>{t("copy")}</Button>
                </div>
              </>
            ) : (
              <p className="text-muted">{t("shareUrlHidden")}</p>
            )}
            <Button size="sm" variant="danger" disabled={busy}
              onClick={() => run(async () => { await api.revokeShare(share.id); setNote(t("shareRevoked")); })}>{t("revoke")}</Button>
          </div>
        ))
      ) : (
        <Button size="sm" variant="ghost" icon={<Link2 className="h-4 w-4" />} disabled={busy || !projectId || !assetId || list.isPending}
          onClick={() => run(async () => {
            const created = await api.createShare(projectId!, assetId!);
            setUrls((u) => ({ ...u, [created.id]: created.url }));
          })}>{t("share")}</Button>
      )}
      {note && <p role="status" className="text-muted">{note}</p>}
      {shownError && <p role="alert" className="text-danger">{shownError}</p>}
    </div>
  );
}

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
