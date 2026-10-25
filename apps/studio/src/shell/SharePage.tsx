"use client";

import { useQuery } from "@tanstack/react-query";
import { I18nProvider as CameraI18n } from "../features/camera-sketch/i18n";
import { ShareView as CameraShareView } from "../features/camera-sketch/components/ShareView";
import { I18nProvider as ProductI18n, useI18n as useProductI18n } from "../features/product-lock/i18n";
import { ShareView as ProductShareView } from "../features/product-lock/components/ShareView";
import "../features/product-lock/product.css";
import { I18nProvider as VfxI18n } from "../features/pocket-vfx/i18n";
import { ShareView as VfxShareView } from "../features/pocket-vfx/components/ShareView";
import { pickShareView, type PublicShareMeta } from "./pickShareView";

const SHELL = "min-h-screen bg-app text-ink";

function Gone() {
  const { t } = useProductI18n();
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl items-center justify-center px-4 py-8">
      <p role="alert" className="text-muted">{t("shareGone")}</p>
    </main>
  );
}

/** The one public `/s/[token]` page: fetch the meta once, render the owning tool's view in its theme. */
export function SharePage({ token }: { token: string }) {
  const q = useQuery({
    queryKey: ["share", token],
    queryFn: async () => {
      const r = await fetch(`/api/v1/public/shares/${encodeURIComponent(token)}/meta`);
      if (!r.ok) throw new Error("gone");
      return (await r.json()) as PublicShareMeta;
    },
    retry: false,
  });
  if (q.isError) {
    return (
      <div className={`theme-product ${SHELL}`}>
        <ProductI18n><Gone /></ProductI18n>
      </div>
    );
  }
  if (!q.data) return <div className={`theme-product ${SHELL}`} />;
  const meta = q.data;
  switch (pickShareView(meta)) {
    case "camera":
      return <div className={`theme-camera ${SHELL}`}><CameraI18n><CameraShareView token={token} meta={meta} /></CameraI18n></div>;
    case "vfx":
      return <div className={`theme-vfx ${SHELL}`}><VfxI18n><VfxShareView token={token} meta={meta} /></VfxI18n></div>;
    default:
      return <div className={`theme-product ${SHELL}`}><ProductI18n><ProductShareView token={token} meta={meta} /></ProductI18n></div>;
  }
}
