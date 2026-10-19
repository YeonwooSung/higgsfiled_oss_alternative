import { ApiError } from "../api/client";
import type { Dict } from "../i18n/en";

/** Map API errors to user text; never show stack traces or internals. */
export function errorText(e: unknown, t: (k: keyof Dict) => string): string {
  return e instanceof ApiError ? e.message : t("genericError");
}
