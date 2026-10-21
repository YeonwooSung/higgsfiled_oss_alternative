"use client";

import { Badge } from "@oc/ui";
import { History, Layers, Sparkles } from "lucide-react";
import { useI18n } from "../i18n";
import type { ProfileBadgeKind } from "../model/results";

/**
 * Render profile badge (spec §3, constraints "Badges"): "Quick" (procedural + bundled plate, neutral, never called AI
 * generated) vs "Generated plate / 생성 plate" in the effect accent #DD8BFF.
 */
export function ProfileBadge({ kind }: { kind: ProfileBadgeKind }) {
  const { t } = useI18n();
  return kind === "generated" ? (
    <span data-badge="generated-plate" title={t("generatedNote")}
      className="inline-flex items-center gap-1 rounded-full border border-effect/60 bg-effect/10 px-2 py-0.5 text-xs font-medium text-effect">
      <Sparkles className="h-3 w-3" aria-hidden />{t("renderGenerated")}
    </span>
  ) : (
    <span data-badge="quick" title={t("quickNote")}
      className="inline-flex items-center gap-1 rounded-full border border-border bg-app px-2 py-0.5 text-xs font-medium text-ink">
      <Layers className="h-3 w-3" aria-hidden />{t("renderQuick")}
    </span>
  );
}

export function MockBadge() {
  const { t } = useI18n();
  return <span data-badge="mock"><Badge tone="mock" title={t("mockExplain")}>{t("mock")}</Badge></span>;
}

export function PreviousVersionBadge() {
  const { t } = useI18n();
  return (
    <span data-badge="previous-version">
      <Badge icon={<History className="h-3 w-3" aria-hidden />} title={t("previousVersionHint")}>{t("previousVersion")}</Badge>
    </span>
  );
}
