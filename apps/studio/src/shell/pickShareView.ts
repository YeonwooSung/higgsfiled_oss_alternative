import type { AppSlug, VfxRenderProfile } from "@oc/contracts";

export type ShareViewKind = "camera" | "product" | "vfx";

/** `GET /public/shares/{token}/meta` (services/api/oc_api/routes/shares.py `share_meta`); `mock` includes upstream mock. */
export interface PublicShareMeta {
  app_slug?: AppSlug;
  mime: string | null;
  role: string;
  mock?: boolean;
  width?: number | null;
  height?: number | null;
  mode?: "geometry" | "cinematic" | null;
  render_profile?: VfxRenderProfile | null;
  plate_source?: string | null;
  expires_at?: string;
}

const BY_SLUG: Record<AppSlug, ShareViewKind> = {
  "camera-sketch": "camera",
  "product-lock": "product",
  "pocket-vfx": "vfx",
};

/** Which tool's view renders a shared asset: the owning app when known, else a guess from the asset role. */
export function pickShareView(meta: { app_slug?: AppSlug; role: string }): ShareViewKind {
  if (meta.app_slug && BY_SLUG[meta.app_slug]) return BY_SLUG[meta.app_slug];
  if (meta.role === "geometry_video" || meta.role === "cinematic_video") return "camera";
  if (meta.role === "render_video") return "vfx";
  return "product";
}
