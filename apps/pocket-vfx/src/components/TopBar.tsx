"use client";

import { Badge } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { api } from "../api/client";
import { useI18n } from "../i18n";
import { LanguageToggle } from "./LanguageToggle";

/** Capabilities are anonymous; the Mock badge and the backend reachability both come from them. */
export function useCapabilities() {
  return useQuery({ queryKey: ["caps"], queryFn: () => api.vfxCapabilities(), staleTime: 60_000, retry: 0 });
}

/**
 * Top bar (56 px): logo, project title, save/job status, persistent Mock badge (shared §11), language. `modelProfile`
 * is the project's profile; without a project the server's default profile decides the badge.
 */
export function TopBar({ title, modelProfile, status, children }: { title?: string; modelProfile?: string; status?: ReactNode; children?: ReactNode }) {
  const { t } = useI18n();
  const caps = useCapabilities();
  const id = modelProfile ?? caps.data?.default_profile;
  const profile = caps.data?.profiles.find((p) => p.id === id);
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-panel px-3 md:px-4">
      <Link href="/" className="flex min-h-[44px] items-center gap-1.5 font-semibold" aria-label={t("appName")}>
        <Sparkles className="h-4 w-4 text-accent" aria-hidden />
        <span className="hidden sm:inline">{t("appName")}</span>
      </Link>
      {title && <span className="min-w-0 truncate text-muted">/ {title}</span>}
      {status && <span className="flex shrink-0 items-center gap-1 whitespace-nowrap text-xs text-muted" role="status" aria-live="polite">{status}</span>}
      {profile?.is_mock && <span data-badge="mock"><Badge tone="mock" title={t("mockExplain")}>{t("mock")}</Badge></span>}
      <div className="ml-auto flex items-center gap-1">
        {children}
        <LanguageToggle />
      </div>
    </header>
  );
}
