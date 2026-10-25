"use client";

import { Languages } from "lucide-react";
import { useI18n } from "../i18n";

export function LanguageToggle() {
  const { locale, setLocale, t } = useI18n();
  return (
    <button
      type="button"
      onClick={() => setLocale(locale === "en" ? "ko" : "en")}
      className="inline-flex h-8 items-center gap-1 rounded-button px-2 text-[13px] text-muted hover:bg-panel focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      aria-label={t("language")}
    >
      <Languages className="h-4 w-4" aria-hidden />
      {locale === "en" ? "한국어" : "English"}
    </button>
  );
}
