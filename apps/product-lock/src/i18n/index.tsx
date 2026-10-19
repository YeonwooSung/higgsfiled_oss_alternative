"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { en, type Dict } from "./en";
import { ko } from "./ko";

export type Locale = "en" | "ko";
const DICTS: Record<Locale, Dict> = { en, ko };
type Key = keyof Dict;

interface I18n {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: Key, vars?: Record<string, string | number>) => string;
}

const Ctx = createContext<I18n | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
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
  const value: I18n = {
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
      let s: string = DICTS[locale][key] ?? en[key];
      for (const [k, v] of Object.entries(vars ?? {})) s = s.replace(`{${k}}`, String(v));
      return s;
    },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18n {
  const v = useContext(Ctx);
  if (!v) throw new Error("I18nProvider missing");
  return v;
}
