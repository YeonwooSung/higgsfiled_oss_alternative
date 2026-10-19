import { ApiError } from "../api/client";
import type { Dict } from "../i18n/en";

const CODE_KEYS: Partial<Record<string, keyof Dict>> = {
  core_too_small: "coreTooSmall",
  mask_required: "maskRequired",
  background_required: "renderNeedsBackground",
  submission_unknown: "submissionUnknown",
};

/** Map API errors to user text; never show stack traces or internals. */
export function errorText(e: unknown, t: (k: keyof Dict) => string): string {
  if (e instanceof ApiError) {
    const key = CODE_KEYS[e.code];
    return key ? t(key) : e.message;
  }
  return t("genericError");
}
