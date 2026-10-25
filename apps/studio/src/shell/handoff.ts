import type { AppSlug, HandoffInfo } from "@oc/contracts";
import { ApiError } from "@oc/web-core";
import type { Dict } from "./i18n/en";
import { findToolForSlug, projectHref } from "./tools";

/**
 * Mirror of the server allowlist `HANDOFF_TARGETS` (services/api/oc_api/handoffs.py), keyed by the source project's
 * tool and the result asset's role. Pocket VFX also has a `cover`, but only Product Lock's may be handed off.
 */
const TARGETS: Partial<Record<AppSlug, Partial<Record<string, AppSlug[]>>>> = {
  "product-lock": { render: ["pocket-vfx"], cover: ["camera-sketch"] },
  "camera-sketch": { geometry_video: ["pocket-vfx"], cinematic_video: ["pocket-vfx"] },
};

/** Tools a result of `app` with asset role `role` can be handed to; `[]` for anything outside the allowlist. */
export function handoffTargets(app: AppSlug, role: string): AppSlug[] {
  return [...(TARGETS[app]?.[role] ?? [])];
}

const BUTTON_KEYS: Partial<Record<AppSlug, keyof Dict>> = {
  "pocket-vfx": "handoffToVfx",
  "camera-sketch": "handoffToCamera",
};

export interface HandoffAsset {
  id: string;
  role: string;
  /** Optional caption shown with the button when several results can go to the same tool. */
  label?: string;
}

export interface HandoffButton {
  /** Stable per (asset, target): also keys the button's idempotency key. */
  key: string;
  assetId: string;
  target: AppSlug;
  labelKey: keyof Dict;
  caption?: string;
}

/** One button per allowed (asset, target), in asset order; missing assets are skipped. */
export function handoffButtons(app: AppSlug, assets: readonly (HandoffAsset | null | undefined)[]): HandoffButton[] {
  return assets.flatMap((a) => (!a ? [] : handoffTargets(app, a.role).flatMap((target) => {
    const labelKey = BUTTON_KEYS[target];
    if (!labelKey) return [];
    return [{ key: `${a.id}:${target}`, assetId: a.id, target, labelKey, ...(a.label ? { caption: a.label } : {}) }];
  })));
}

const ERROR_KEYS: Partial<Record<string, keyof Dict>> = {
  asset_not_ready: "handoffNotReady",
  asset_missing: "handoffMissing",
  not_found: "handoffMissing",
  handoff_not_allowed: "handoffNotAllowed",
  too_large: "handoffTooLarge",
};

/** User text for a failed hand-off: known codes in the UI language, else the server message, never internals. */
export function handoffErrorText(e: unknown, t: (k: keyof Dict) => string): string {
  if (e instanceof ApiError) {
    const key = ERROR_KEYS[e.code];
    return key ? t(key) : e.message;
  }
  return t("handoffFailed");
}

/** Where the provenance badge links: the source project while it exists and its tool is known, else null. */
export function provenanceHref(h: HandoffInfo): string | null {
  if (!h.source_exists || !findToolForSlug(h.source_app)) return null;
  return projectHref({ id: h.source_project_id, app_slug: h.source_app });
}
