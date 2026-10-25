"use client";

import { cx } from "@oc/ui";
import { ApiClient } from "@oc/web-core";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tooltip from "@radix-ui/react-tooltip";
import { useQuery } from "@tanstack/react-query";
import { CircleAlert, PanelLeftClose, PanelLeftOpen, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { useShellI18n } from "./i18n";
import { browserStorage, readCollapsed, writeCollapsed } from "./sidebarState";
import { activeToolForPath, listableProjects, PROJECTS_QUERY_KEY, projectHref, TOOLS, type Tool } from "./tools";

/** `listProjects` is tool-agnostic; the slug only matters for endpoints this client never calls. */
const shellApi = new ApiClient("camera-sketch");
const PROJECT_LIMIT = 50;

/* ---------- mobile drawer state (the shell top bar's menu button opens it) ---------- */

interface DrawerState {
  open: boolean;
  setOpen: (open: boolean) => void;
}

const DrawerContext = createContext<DrawerState>({ open: false, setOpen: () => {} });

export function SidebarDrawerProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const value = useMemo(() => ({ open, setOpen }), [open]);
  return <DrawerContext.Provider value={value}>{children}</DrawerContext.Provider>;
}

/** Open state of the sidebar drawer shown below the `md` breakpoint. */
export function useSidebarDrawer(): DrawerState {
  return useContext(DrawerContext);
}

/* ---------- shared content ---------- */

/**
 * `rail`: the desktop sidebar. Below `lg` it is always the 56px icon rail (the editors' side panels
 * need the width); from `lg` it is 200px with labels unless the user collapsed it.
 * `drawer`: the mobile drawer, always labelled.
 */
type Mode = { kind: "rail"; collapsed: boolean } | { kind: "drawer" };

function labelClass(mode: Mode): string {
  if (mode.kind === "drawer") return "";
  return mode.collapsed ? "sr-only" : "sr-only lg:not-sr-only";
}

const accentVar = (tool: Tool) => ({ "--oc-accent": tool.accent }) as CSSProperties;

function Tip({ mode, label, children }: { mode: Mode; label: string; children: ReactNode }) {
  if (mode.kind === "drawer") return <>{children}</>;
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="right"
          sideOffset={8}
          className={cx(
            "z-50 max-w-[240px] rounded-button border border-border bg-panel px-2 py-1 text-xs text-ink shadow-lg",
            !mode.collapsed && "lg:hidden",
          )}
        >
          {label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

function SidebarContent({ mode, onNavigate }: { mode: Mode; onNavigate?: () => void }) {
  const { t } = useShellI18n();
  const pathname = usePathname();
  const active = activeToolForPath(pathname);
  const projects = useQuery({
    queryKey: PROJECTS_QUERY_KEY,
    queryFn: () => shellApi.listProjects(PROJECT_LIMIT),
    retry: 0,
  });
  const list = listableProjects(projects.data?.projects ?? []);
  const label = labelClass(mode);
  const rail = mode.kind === "rail";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <nav aria-label={t("tools")} className="shrink-0 p-2">
        <ul className="space-y-1">
          {TOOLS.map((tool) => {
            const isActive = tool.id === active?.id;
            const Icon = tool.icon;
            return (
              <li key={tool.id} style={accentVar(tool)}>
                <Tip mode={mode} label={`${t(tool.labelKey)}: ${t(tool.blurbKey)}`}>
                  <Link
                    href={tool.href}
                    onClick={onNavigate}
                    aria-current={isActive ? "page" : undefined}
                    className={cx(
                      "flex min-h-[44px] items-start gap-2.5 rounded-button border px-2.5 py-2 transition-colors duration-150 md:min-h-0",
                      rail && (mode.collapsed ? "justify-center" : "justify-center lg:justify-start"),
                      isActive ? "border-accent/40 bg-accent/10" : "border-transparent hover:bg-panel",
                    )}
                  >
                    <Icon className={cx("mt-0.5 h-4 w-4 shrink-0", isActive ? "text-accent" : "text-muted")} aria-hidden />
                    <span className={cx("min-w-0", label)}>
                      <span className={cx("block truncate text-[13px] font-semibold", isActive ? "text-accent" : "text-ink")}>
                        {t(tool.labelKey)}
                      </span>
                      <span className="block truncate text-xs text-muted">{t(tool.blurbKey)}</span>
                    </span>
                  </Link>
                </Tip>
              </li>
            );
          })}
        </ul>
      </nav>

      <section aria-labelledby="oc-my-projects" className="flex min-h-0 flex-1 flex-col border-t border-border">
        {/* Padding lives on the inner span: `not-sr-only` resets padding on the element it is applied to. */}
        <h2 id="oc-my-projects" className={label}>
          <span className="block px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-muted">{t("myProjects")}</span>
        </h2>
        <div className="scroll-panel min-h-0 flex-1 overflow-y-auto p-2 pt-1">
          {projects.isError && (
            // Visible in every mode (the icon stays when labels collapse) and announced via role="alert".
            <div role="alert" className="mb-1">
              <Tip mode={mode} label={`${t("projectsLoadFailed")} ${t("retry")}`}>
                <button
                  type="button"
                  onClick={() => void projects.refetch()}
                  disabled={projects.isFetching}
                  aria-label={`${t("projectsLoadFailed")} ${t("retry")}`}
                  className={cx(
                    "flex min-h-[44px] w-full items-start gap-2 rounded-button px-2.5 py-1.5 text-left text-xs transition-colors duration-150 hover:bg-panel disabled:opacity-60 md:min-h-0",
                    rail && (mode.collapsed ? "justify-center" : "justify-center lg:justify-start"),
                  )}
                >
                  <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" aria-hidden />
                  <span className={cx("min-w-0", label)}>
                    <span className="block text-muted">{t("projectsLoadFailed")}</span>
                    <span className="block font-medium text-accent underline underline-offset-2">{t("retry")}</span>
                  </span>
                </button>
              </Tip>
            </div>
          )}
          {list.length === 0 ? (
            !projects.isPending && !projects.isError && (
              <p className={label}>
                <span className="block px-2 py-1 text-xs text-muted">{t("noProjects")}</span>
              </p>
            )
          ) : (
            <ul className="space-y-0.5">
              {list.map(({ project: p, tool }) => {
                const href = projectHref(p);
                const isActive = pathname === href;
                const Icon = tool.icon;
                return (
                  <li key={p.id} style={accentVar(tool)}>
                    <Tip mode={mode} label={p.title}>
                      <Link
                        href={href}
                        onClick={onNavigate}
                        aria-current={isActive ? "page" : undefined}
                        className={cx(
                          "flex min-h-[44px] items-center gap-2.5 rounded-button px-2.5 py-1.5 text-[13px] transition-colors duration-150 md:min-h-0",
                          rail && (mode.collapsed ? "justify-center" : "justify-center lg:justify-start"),
                          isActive ? "bg-accent/10 text-ink" : "text-muted hover:bg-panel hover:text-ink",
                        )}
                      >
                        <Icon className="h-3.5 w-3.5 shrink-0 text-accent" aria-hidden />
                        <span className={cx("min-w-0 truncate", label)}>{p.title}</span>
                      </Link>
                    </Tip>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}

/* ---------- desktop sidebar ---------- */

export function Sidebar() {
  const { t } = useShellI18n();
  // Read after mount so the server render and the first client render agree (expanded).
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => setCollapsed(readCollapsed(browserStorage())), []);
  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    writeCollapsed(browserStorage(), next);
  };
  const mode: Mode = { kind: "rail", collapsed };
  const ToggleIcon = collapsed ? PanelLeftOpen : PanelLeftClose;

  return (
    <aside
      className={cx(
        "sticky top-0 hidden h-[100dvh] shrink-0 flex-col border-r border-border bg-app md:flex",
        collapsed ? "w-[56px]" : "w-[56px] lg:w-[200px]",
      )}
    >
      <Tooltip.Provider delayDuration={300}>
        <SidebarContent mode={mode} />
      </Tooltip.Provider>
      {/* Below lg the rail is always collapsed, so the toggle only exists where it does something. */}
      <div className="hidden shrink-0 border-t border-border p-2 lg:block">
        <button
          type="button"
          onClick={toggle}
          aria-label={collapsed ? t("expandSidebar") : t("collapseSidebar")}
          title={collapsed ? t("expandSidebar") : t("collapseSidebar")}
          className={cx(
            "flex h-8 w-full items-center rounded-button px-2.5 text-muted transition-colors duration-150 hover:bg-panel hover:text-ink",
            collapsed && "justify-center",
          )}
        >
          <ToggleIcon className="h-4 w-4" aria-hidden />
        </button>
      </div>
    </aside>
  );
}

/* ---------- mobile drawer ---------- */

export function SidebarDrawer() {
  const { t } = useShellI18n();
  const { open, setOpen } = useSidebarDrawer();
  const pathname = usePathname();
  useEffect(() => setOpen(false), [pathname, setOpen]);
  // The drawer only exists below md; close it when the viewport grows so Radix's scroll lock is released.
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(min-width: 768px)");
    const onChange = () => {
      if (mq.matches) setOpen(false);
    };
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [setOpen]);

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 md:hidden" />
        <Dialog.Content
          aria-describedby={undefined}
          className="theme-camera fixed inset-y-0 left-0 z-50 flex w-[min(280px,85vw)] flex-col border-r border-border bg-app text-ink shadow-xl md:hidden"
        >
          <div className="flex h-topbar shrink-0 items-center justify-between border-b border-border px-4">
            <Dialog.Title className="text-sm font-semibold">{t("appName")}</Dialog.Title>
            <Dialog.Close
              aria-label={t("closeMenu")}
              className="flex h-11 w-11 items-center justify-center rounded-button text-muted hover:bg-panel hover:text-ink"
            >
              <X className="h-4 w-4" aria-hidden />
            </Dialog.Close>
          </div>
          <SidebarContent mode={{ kind: "drawer" }} onNavigate={() => setOpen(false)} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/* ---------- shell frame ---------- */

/** Sidebar to the left of the active tool's full-height layout; wraps `/camera`, `/product`, `/vfx`, never `/s`. */
export function StudioShell({ children }: { children: ReactNode }) {
  return (
    <SidebarDrawerProvider>
      <div className="flex min-h-[100dvh]">
        <Sidebar />
        <div className="min-w-0 flex-1">{children}</div>
      </div>
      <SidebarDrawer />
    </SidebarDrawerProvider>
  );
}
