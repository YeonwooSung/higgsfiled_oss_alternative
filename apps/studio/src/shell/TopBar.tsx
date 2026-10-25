"use client";

import { Badge } from "@oc/ui";
import { Check, CircleAlert, Layers, Loader2, Menu } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { useShellI18n } from "./i18n";
import { LanguageToggle } from "./LanguageToggle";
import { useSidebarDrawer } from "./Sidebar";

export type SaveState = "saved" | "saving" | "error" | "conflict";

/** The one top bar for every tool: brand, title, save status, Mock badge, tool `actions`, language. */
export function TopBar({ title, saveState, mock, actions, onMenu }: {
  title?: string;
  saveState?: SaveState;
  mock?: boolean;
  actions?: ReactNode;
  onMenu?: () => void;
}) {
  const { t } = useShellI18n();
  const drawer = useSidebarDrawer();
  const status = saveState && {
    saved: <><Check className="h-3.5 w-3.5" aria-hidden />{t("saved")}</>,
    saving: <><Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />{t("saving")}</>,
    error: <><CircleAlert className="h-3.5 w-3.5 text-danger" aria-hidden />{t("saveFailed")}</>,
    conflict: <><CircleAlert className="h-3.5 w-3.5 text-target" aria-hidden />{t("conflict")}</>,
  }[saveState];

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-panel px-3 md:px-4">
      <button
        type="button"
        onClick={onMenu ?? (() => drawer.setOpen(true))}
        className="-ml-2 inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-button text-muted hover:bg-app hover:text-ink md:hidden"
        aria-label={t("openMenu")}
      >
        <Menu className="h-5 w-5" aria-hidden />
      </button>
      <Link href="/camera" className="flex min-h-[44px] items-center gap-1.5 font-semibold" aria-label={t("appName")}>
        <Layers className="h-4 w-4 text-accent" aria-hidden />
        <span className="hidden sm:inline">{t("appName")}</span>
      </Link>
      {title && <span className="min-w-0 truncate text-muted">/ {title}</span>}
      {status && <span className="flex shrink-0 items-center gap-1 whitespace-nowrap text-xs text-muted" role="status" aria-live="polite">{status}</span>}
      {mock && <span data-badge="mock"><Badge tone="mock" title={t("mockExplain")}>{t("mock")}</Badge></span>}
      <div className="ml-auto flex items-center gap-1">
        {actions}
        <LanguageToggle />
      </div>
    </header>
  );
}
