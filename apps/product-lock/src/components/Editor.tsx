"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useI18n } from "../i18n";
import { useEditor } from "../state/editorStore";
import { BottomPanel } from "./BottomPanel";
import { Inspector } from "./Inspector";
import { LeftPanel } from "./LeftPanel";
import { StageArea } from "./StageArea";
import { TopBar } from "./TopBar";
import { cx } from "@oc/ui";

type MobileTab = "product" | "scene" | "output";

function isTyping(): boolean {
  const el = document.activeElement as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

export function Editor({ projectId }: { projectId: string }) {
  const { t } = useI18n();
  const load = useEditor((s) => s.load);
  const project = useEditor((s) => s.project);
  const [mobileTab, setMobileTab] = useState<MobileTab>("product");
  const query = useQuery({
    queryKey: ["project", projectId],
    queryFn: async () => {
      try {
        return await api.getProject(projectId);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          await api.startSession();
          return api.getProject(projectId);
        }
        throw e;
      }
    },
    staleTime: Infinity,
  });

  useEffect(() => {
    if (query.data) load(query.data);
  }, [query.data, load]);

  // Poll the project while uploads are still being inspected.
  useEffect(() => {
    if (!project?.assets.some((a) => a.state === "uploaded")) return;
    const id = setInterval(() => void useEditor.getState().refresh(), 1500);
    return () => clearInterval(id);
  }, [project]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping()) return;
      const mod = e.metaKey || e.ctrlKey;
      const s = useEditor.getState();
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) s.redo();
        else s.undo();
      } else if (e.key === "Escape") {
        s.setTool("select");
        s.setView("compose");
      } else if (e.key === " ") {
        const video = document.querySelector<HTMLVideoElement>("video[data-stage-video]");
        if (video) {
          e.preventDefault();
          if (video.paused) void video.play();
          else video.pause();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (query.isError) {
    return <main className="p-8"><p role="alert" className="text-danger">{t("genericError")}</p></main>;
  }
  if (!project) return <main className="p-8 text-muted">…</main>;

  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-[260px] shrink-0 overflow-y-auto border-r border-border bg-panel md:block">
          <LeftPanel />
        </aside>
        <main className="flex min-w-0 flex-1 flex-col">
          <StageArea />
          <BottomPanel />
        </main>
        <aside className="hidden w-[320px] shrink-0 overflow-y-auto border-l border-border bg-panel md:block">
          <Inspector />
        </aside>
      </div>
      {/* Mobile: stage on top, inspector as a bottom sheet with tabs */}
      <nav className="border-t border-border bg-panel md:hidden">
        <div role="tablist" className="flex">
          {(["product", "scene", "output"] as const).map((tab) => (
            <button key={tab} role="tab" aria-selected={mobileTab === tab} onClick={() => setMobileTab(tab)}
              className={cx("min-h-[44px] flex-1 text-[13px]", mobileTab === tab ? "font-semibold text-accent" : "text-muted")}>
              {t(tab)}
            </button>
          ))}
        </div>
        <div className="max-h-[40dvh] overflow-y-auto">
          {mobileTab === "product" ? <LeftPanel compact /> : <Inspector only={mobileTab} />}
        </div>
      </nav>
    </div>
  );
}
