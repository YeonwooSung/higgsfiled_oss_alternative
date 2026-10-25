"use client";

import { createI18n } from "@oc/web-core";
import { en, type Dict } from "./en";
import { ko } from "./ko";

export type { Locale } from "@oc/web-core";

/** The shell's own namespace; each tool keeps its own `createI18n` namespace under the same `LocaleProvider`. */
export const { I18nProvider: ShellI18nProvider, useI18n: useShellI18n } = createI18n<Dict>({ en, ko });
