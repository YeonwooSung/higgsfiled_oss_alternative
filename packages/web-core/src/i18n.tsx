"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Locale = "en" | "ko";

export interface I18n<Key extends string> {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: Key, vars?: Record<string, string | number>) => string;
}

/** Builds the provider/hook pair for one app's dictionaries (`en` is the fallback for missing `ko` keys). */
export function createI18n<Dict extends Record<string, string>>(dicts: Record<Locale, Dict>) {
  type Key = Extract<keyof Dict, string>;
  const Ctx = createContext<I18n<Key> | null>(null);

  function I18nProvider({ children }: { children: ReactNode }) {
    const [locale, setLocale] = useState<Locale>("en");
    useEffect(() => {
      try {
        const saved = localStorage.getItem("oc.locale") as Locale | null;
        if (saved === "en" || saved === "ko") setLocale(saved);
        else if (navigator.language.startsWith("ko")) setLocale("ko");
      } catch {
        /* storage unavailable: keep default */
      }
    }, []);
    const value: I18n<Key> = {
      locale,
      setLocale: (l) => {
        setLocale(l);
        try {
          localStorage.setItem("oc.locale", l);
        } catch {
          /* ignore */
        }
      },
      t: (key, vars) => {
        let s: string = dicts[locale][key] ?? dicts.en[key];
        for (const [k, v] of Object.entries(vars ?? {})) s = s.replace(`{${k}}`, String(v));
        return s;
      },
    };
    return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
  }

  function useI18n(): I18n<Key> {
    const v = useContext(Ctx);
    if (!v) throw new Error("I18nProvider missing");
    return v;
  }

  return { I18nProvider, useI18n };
}
