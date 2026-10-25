"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { editorProjectQuery } from "../../../shell/projectQuery";
import { api } from "../api/client";
import { useI18n } from "../i18n";
import { useEditor } from "../state/editorStore";
import { BottomPanel } from "./BottomPanel";
import { Inspector } from "./Inspector";
import { LeftPanel } from "./LeftPanel";
import { StageArea } from "./StageArea";
import { TopBarActions } from "./TopBarActions";
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
  const qc = useQueryClient();
  const query = useQuery(editorProjectQuery(api, projectId, qc));

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
    // Desktop: fixed app shell (panels scroll on their own) that never gets shorter than 640px —
    // below that the page itself scrolls instead of clipping. Mobile: a normal scrolling page.
    <div className="flex min-h-[100dvh] flex-col md:h-[100dvh] md:min-h-[640px]">
      <TopBarActions />
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <aside className="scroll-panel hidden w-[260px] shrink-0 border-r border-border bg-panel md:block">
          <LeftPanel />
        </aside>
        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          <StageArea />
          <BottomPanel />
        </main>
        <aside className="scroll-panel hidden w-[320px] shrink-0 border-l border-border bg-panel md:block">
          <Inspector />
        </aside>
      </div>
      {/* Mobile: stage on top, inspector below with sticky tabs; the page scrolls */}
      <nav className="border-t border-border bg-panel md:hidden">
        <div role="tablist" className="sticky top-0 z-10 flex border-b border-border bg-panel">
          {(["product", "scene", "output"] as const).map((tab) => (
            <button key={tab} role="tab" aria-selected={mobileTab === tab} onClick={() => setMobileTab(tab)}
              className={cx("min-h-[44px] flex-1 text-[13px]", mobileTab === tab ? "font-semibold text-accent" : "text-muted")}>
              {t(tab)}
            </button>
          ))}
        </div>
        <div>
          {mobileTab === "product" ? <LeftPanel compact /> : <Inspector only={mobileTab} />}
        </div>
      </nav>
    </div>
  );
}
