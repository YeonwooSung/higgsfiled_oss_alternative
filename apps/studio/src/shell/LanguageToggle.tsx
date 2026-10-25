"use client";

import { useLocale } from "@oc/web-core";
import { Languages } from "lucide-react";
import { useShellI18n } from "./i18n";

export function LanguageToggle() {
  const { locale, setLocale } = useLocale();
  const { t } = useShellI18n();
  return (
    <button
      type="button"
      onClick={() => setLocale(locale === "en" ? "ko" : "en")}
      className="inline-flex min-h-[44px] items-center gap-1 whitespace-nowrap rounded-button px-2 text-[13px] text-muted hover:bg-app hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent md:min-h-8"
      aria-label={t("language")}
    >
      <Languages className="h-4 w-4" aria-hidden />
      {locale === "en" ? "한국어" : "English"}
    </button>
  );
}
