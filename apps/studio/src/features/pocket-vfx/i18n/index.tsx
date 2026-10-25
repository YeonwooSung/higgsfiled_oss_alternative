"use client";

import { createI18n } from "@oc/web-core";
import { en, type Dict } from "./en";
import { ko } from "./ko";

export type { Locale } from "@oc/web-core";

export const { I18nProvider, useI18n } = createI18n<Dict>({ en, ko });
