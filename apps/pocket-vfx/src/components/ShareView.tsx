"use client";

/**
 * Sharing (shared §6, §10): `ShareLink` creates / revokes a public link to one render video, only on an explicit click
 * (results are private by default; the original clip is never shareable — the API allows finished outputs only).
 * `ShareView` is the public `s/[token]` page: `GET /public/shares/{token}/meta` gives mime, role, `render_profile`,
 * `plate_source` and `mock`; `GET /public/shares/{token}` redirects to the file. A video plays muted; an image shows as an image;
 * anything else (e.g. a ZIP) is never put in an <img>.
 */
import type { VfxRenderProfile } from "@oc/contracts";
import { Button } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { Copy, Link2, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import { useI18n } from "../i18n";
import { profileBadgeOf } from "../model/results";
import { useEditor } from "../state/editorStore";
import { MockBadge, ProfileBadge } from "./badges";
import { ErrorLine } from "./JobUi";

export function ShareLink({ assetId }: { assetId: string }) {
  const { t, locale } = useI18n();
  const projectId = useEditor((s) => s.project?.id);
  const [share, setShare] = useState<{ id: string; url: string; expires_at: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNote(null);
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
    <section aria-labelledby="share-title" className="flex flex-col gap-2 text-xs" data-region="share">
      <h3 id="share-title" className="font-semibold uppercase tracking-wide text-muted">{t("shareTitle")}</h3>
      {share ? (
        <>
          <p className="text-muted">{t("shareActive", { date: new Date(share.expires_at).toLocaleDateString(locale) })}</p>
          <div className="flex gap-2">
            <input readOnly value={full} aria-label={t("shareLink")} onFocus={(e) => e.currentTarget.select()}
              className="min-h-[44px] min-w-0 flex-1 rounded-button border border-border bg-app px-2 text-ink" />
            <Button size="sm" className="min-h-[44px]" icon={<Copy className="h-3.5 w-3.5" aria-hidden />}
              onClick={() => void navigator.clipboard?.writeText(full).then(() => setNote(t("copied")), () => {})}>{t("copy")}</Button>
          </div>
          <Button size="sm" variant="danger" className="min-h-[44px] self-start" disabled={busy} icon={<Trash2 className="h-3.5 w-3.5" aria-hidden />}
            onClick={() => run(async () => { await api.revokeShare(share.id); setShare(null); setNote(t("shareRevoked")); })}>{t("revoke")}</Button>
        </>
      ) : (
        <>
          <p className="text-muted">{t("shareExplain")}</p>
          <Button size="sm" variant="secondary" className="min-h-[44px] self-start" disabled={busy || !projectId}
            icon={<Link2 className="h-3.5 w-3.5" aria-hidden />}
            onClick={() => run(async () => { setShare(await api.createShare(projectId!, assetId)); })}>{t("shareCreate")}</Button>
        </>
      )}
      {note && <p role="status" className="text-muted">{note}</p>}
      <ErrorLine text={error} />
    </section>
  );
}

/** `GET /public/shares/{token}/meta` (services/api/oc_api/routes/shares.py `share_meta`). */
interface ShareMeta {
  mime: string | null;
  width: number | null;
  height: number | null;
  role: string;
  mock: boolean;
  render_profile?: VfxRenderProfile | null;
  /** "bundled" | "generated" | "procedural" (render videos) */
  plate_source?: string | null;
  expires_at: string;
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
  const video = !!m?.mime?.startsWith("video/");
  const image = !!m?.mime && ["image/png", "image/jpeg", "image/webp"].includes(m.mime);
  // the render profile is on render videos only (a Quick render is never labelled generated)
  const profile = m?.render_profile ? profileBadgeOf({ render_profile: m.render_profile, plate_source: m.plate_source ?? "" }) : null;
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col items-center gap-4 px-4 py-8">
      <p className="flex items-center gap-2 font-semibold"><Sparkles className="h-4 w-4 text-accent" aria-hidden />{t("shareViewTitle")}</p>
      {meta.isError && <p role="alert" className="text-muted">{t("shareGone")}</p>}
      {m && (
        <div className="flex flex-wrap items-center justify-center gap-2" data-region="share-badges">
          {profile && <ProfileBadge kind={profile} />}
          {m.mock && <MockBadge />}
        </div>
      )}
      {video && <video src={src} controls muted autoPlay loop playsInline className="max-h-[80vh] max-w-full rounded-panel" />}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {image && <img src={src} alt={t("shareViewTitle")} className="max-h-[80vh] max-w-full rounded-panel" />}
      {m && !video && !image && <p className="text-muted">{t("shareCannotPreview")}</p>}
    </main>
  );
}
