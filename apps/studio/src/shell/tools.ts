import type { AppSlug, ProjectSummary } from "@oc/contracts";
import { Lock, Sparkles, Video, type LucideIcon } from "lucide-react";
import type { Dict } from "./i18n/en";

export type ToolId = "camera" | "product" | "vfx";

export interface Tool {
  id: ToolId;
  slug: AppSlug;
  href: string;
  /** Product names stay English in every locale, but go through the dictionary like other copy. */
  labelKey: keyof Dict;
  blurbKey: keyof Dict;
  icon: LucideIcon;
  /** RGB triplet of the tool's `--oc-accent` (kept equal to `.theme-<id>` in globals.css by a test). */
  accent: string;
}

/** Sidebar order (spec §4.1): Camera Sketch is the default tool and comes first. */
export const TOOLS: readonly Tool[] = [
  { id: "camera", slug: "camera-sketch", href: "/camera", labelKey: "toolCamera", blurbKey: "toolCameraBlurb", icon: Video, accent: "102 220 242" },
  { id: "product", slug: "product-lock", href: "/product", labelKey: "toolProduct", blurbKey: "toolProductBlurb", icon: Lock, accent: "76 195 138" },
  { id: "vfx", slug: "pocket-vfx", href: "/vfx", labelKey: "toolVfx", blurbKey: "toolVfxBlurb", icon: Sparkles, accent: "255 154 82" },
];

/** Lenient lookup for data from the backend, which may know tools this build does not. */
export function findToolForSlug(slug: string): Tool | undefined {
  return TOOLS.find((t) => t.slug === slug);
}

/** Strict lookup for typed callers; throws on a slug outside `AppSlug`. */
export function toolForSlug(slug: AppSlug): Tool {
  const tool = findToolForSlug(slug);
  if (!tool) throw new Error(`unknown app slug: ${slug}`);
  return tool;
}

/** Projects the sidebar can show, paired with their tool; unknown slugs are skipped, order kept. */
export function listableProjects(projects: readonly ProjectSummary[]): { project: ProjectSummary; tool: Tool }[] {
  return projects.flatMap((project) => {
    const tool = findToolForSlug(project.app_slug);
    return tool ? [{ project, tool }] : [];
  });
}

export function projectHref(p: Pick<ProjectSummary, "id" | "app_slug">): string {
  return `${toolForSlug(p.app_slug).href}/p/${p.id}`;
}

/** The tool whose start or editor route `pathname` is in; undefined elsewhere (e.g. `/s/[token]`). */
export function activeToolForPath(pathname: string | null): Tool | undefined {
  if (!pathname) return undefined;
  return TOOLS.find((t) => pathname === t.href || pathname.startsWith(`${t.href}/`));
}

/** React Query key of the sidebar's project list; invalidate after create, hand-off and delete. */
export const PROJECTS_QUERY_KEY = ["projects"] as const;
