"use client";

import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";

export type Locale = "en" | "ko";

const STORAGE_KEY = "oc.locale";

export interface I18n<Key extends string> {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: Key, vars?: Record<string, string | number>) => string;
}

/** A tiny observable holding the current locale; `LocaleProvider` renders from it. */
export interface LocaleStore {
  get(): Locale;
  set(l: Locale): void;
  subscribe(listener: () => void): () => void;
}

export function createLocaleStore(initial: Locale = "en"): LocaleStore {
  let locale = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => locale,
    set: (l) => {
      if (l === locale) return;
      locale = l;
      for (const fn of [...listeners]) fn();
    },
    subscribe: (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

export interface LocaleState {
  locale: Locale;
  setLocale: (l: Locale) => void;
}

const LocaleCtx = createContext<LocaleState | null>(null);

/**
 * Owns the one UI locale for everything below it: restores it from `localStorage` (`oc.locale`) or the
 * browser language, persists changes and mirrors it onto `<html lang>`. Every `createI18n` provider
 * mounted inside reads from here, so one language switch changes all namespaces at once.
 * `store` is injectable for tests; apps omit it.
 */
export function LocaleProvider({ children, store: injected }: { children: ReactNode; store?: LocaleStore }) {
  const [store] = useState(() => injected ?? createLocaleStore());
  const locale = useSyncExternalStore(store.subscribe, store.get, store.get);
  useEffect(() => {
    if (injected) return;
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved === "en" || saved === "ko") store.set(saved);
      else if (navigator.language.startsWith("ko")) store.set("ko");
    } catch {
      /* storage unavailable: keep default */
    }
  }, [store, injected]);
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);
  const value: LocaleState = {
    locale,
    setLocale: (l) => {
      store.set(l);
      try {
        localStorage.setItem(STORAGE_KEY, l);
      } catch {
        /* ignore */
      }
    },
  };
  return <LocaleCtx.Provider value={value}>{children}</LocaleCtx.Provider>;
}

export function useLocale(): LocaleState {
  const v = useContext(LocaleCtx);
  if (!v) throw new Error("LocaleProvider missing");
  return v;
}

/**
 * Builds the provider/hook pair for one namespace's dictionaries (`en` is the fallback for missing `ko` keys).
 * Under a `LocaleProvider` the locale is shared; without one the provider mounts its own, so a standalone
 * app keeps working unchanged.
 */
export function createI18n<Dict extends Record<string, string>>(dicts: Record<Locale, Dict>) {
  type Key = Extract<keyof Dict, string>;
  const Ctx = createContext<I18n<Key> | null>(null);

  function Bound({ children }: { children: ReactNode }) {
    const { locale, setLocale } = useLocale();
    const value: I18n<Key> = {
      locale,
      setLocale,
      t: (key, vars) => {
        let s: string = dicts[locale][key] ?? dicts.en[key];
        for (const [k, v] of Object.entries(vars ?? {})) s = s.replace(`{${k}}`, String(v));
        return s;
      },
    };
    return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
  }

  function I18nProvider({ children }: { children: ReactNode }) {
    const shared = useContext(LocaleCtx);
    if (shared) return <Bound>{children}</Bound>;
    return (
      <LocaleProvider>
        <Bound>{children}</Bound>
      </LocaleProvider>
    );
  }

  function useI18n(): I18n<Key> {
    const v = useContext(Ctx);
    if (!v) throw new Error("I18nProvider missing");
    return v;
  }

  return { I18nProvider, useI18n };
}
