"use client";

import { Box, Sparkles } from "lucide-react";
import { useI18n } from "../i18n";

/**
 * Result mode badges (spec §2): Geometry = "Path render / 경로 렌더" (camera path cyan #66DCF2),
 * Cinematic = "AI generated / AI 생성" (generated accent #B89BFF). Cinematic is never labelled exact
 * camera control.
 */
export function ModeBadge({ mode }: { mode: "geometry" | "cinematic" }) {
  const { t } = useI18n();
  return mode === "geometry" ? (
    <span data-badge="path-render" className="inline-flex items-center gap-1 rounded-full border border-[#66DCF2]/60 bg-[#66DCF2]/10 px-2 py-0.5 text-xs font-medium text-[#66DCF2]">
      <Box className="h-3 w-3" aria-hidden />{t("badgeGeometry")}
    </span>
  ) : (
    <span data-badge="ai-generated" className="inline-flex items-center gap-1 rounded-full border border-[#B89BFF]/60 bg-[#B89BFF]/10 px-2 py-0.5 text-xs font-medium text-[#B89BFF]">
      <Sparkles className="h-3 w-3" aria-hidden />{t("badgeCinematic")}
    </span>
  );
}
