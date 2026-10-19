"use client";

import { Badge, Button } from "@oc/ui";
import { Check, CircleAlert, Loader2, Lock, Redo2, Undo2 } from "lucide-react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useI18n } from "../i18n";
import { useEditor } from "../state/editorStore";
import { JobIndicator } from "./JobPanel";
import { LanguageToggle } from "./LanguageToggle";

export function TopBar() {
  const { t } = useI18n();
  const project = useEditor((s) => s.project)!;
  const config = useEditor((s) => s.config)!;
  const saveState = useEditor((s) => s.saveState);
  const { canUndo, canRedo, undo, redo } = useEditor();
  const caps = useQuery({ queryKey: ["caps"], queryFn: () => api.capabilities(), staleTime: 60_000 });
  const profile = caps.data?.profiles.find((p) => p.id === config.model_profile);

  const status = {
    saved: <><Check className="h-3.5 w-3.5" aria-hidden />{t("saved")}</>,
    saving: <><Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />{t("saving")}</>,
    error: <><CircleAlert className="h-3.5 w-3.5 text-danger" aria-hidden />{t("saveFailed")}</>,
    conflict: <><CircleAlert className="h-3.5 w-3.5 text-amber-700" aria-hidden />{t("conflict")}</>,
  }[saveState];

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-panel px-3 md:px-4">
      <Link href="/" className="flex items-center gap-1.5 font-semibold" aria-label={t("appName")}>
        <Lock className="h-4 w-4 text-accent" aria-hidden />
        <span className="hidden sm:inline">{t("appName")}</span>
      </Link>
      <span className="truncate text-muted">/ {project.title}</span>
      <span className="flex shrink-0 items-center gap-1 whitespace-nowrap text-xs text-muted" aria-live="polite">{status}</span>
      {profile?.is_mock && <Badge tone="mock" title={t("mockExplain")}>{t("mock")}</Badge>}
      <div className="ml-auto flex items-center gap-1">
        <JobIndicator />
        <Button variant="ghost" size="sm" onClick={undo} disabled={!canUndo} aria-label={t("undo")} icon={<Undo2 className="h-4 w-4" />} />
        <Button variant="ghost" size="sm" onClick={redo} disabled={!canRedo} aria-label={t("redo")} icon={<Redo2 className="h-4 w-4" />} />
        <LanguageToggle />
      </div>
    </header>
  );
}
