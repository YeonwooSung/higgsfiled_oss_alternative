"use client";

import { Button } from "@oc/ui";
import { useQuery } from "@tanstack/react-query";
import { Redo2, Undo2 } from "lucide-react";
import { topBarMock } from "../../../shell/topBarMock";
import { TopBar } from "../../../shell/TopBar";
import { api } from "../api/client";
import { useI18n } from "../i18n";
import { useEditor } from "../state/editorStore";

/** Camera Sketch's editor top bar: the shell bar with undo/redo as its actions. */
export function TopBarActions() {
  const { t } = useI18n();
  const project = useEditor((s) => s.project)!;
  const config = useEditor((s) => s.config)!;
  const saveState = useEditor((s) => s.saveState);
  const { canUndo, canRedo, undo, redo } = useEditor();
  const caps = useQuery({ queryKey: ["caps"], queryFn: () => api.cameraCapabilities(), staleTime: 60_000 });
  const profile = caps.data?.profiles.find((p) => p.id === config.model_profile);
  return (
    <TopBar
      title={project.title}
      saveState={saveState}
      mock={topBarMock(profile?.is_mock, project.upstream_mock)}
      actions={<>
        <Button variant="ghost" size="sm" onClick={undo} disabled={!canUndo} aria-label={t("undo")} icon={<Undo2 className="h-4 w-4" />} />
        <Button variant="ghost" size="sm" onClick={redo} disabled={!canRedo} aria-label={t("redo")} icon={<Redo2 className="h-4 w-4" />} />
      </>}
    />
  );
}
