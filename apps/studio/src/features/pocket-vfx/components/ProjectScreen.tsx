"use client";

/**
 * /p/[id]: the trim screen until a fresh work clip is linked (analysis of the config's clip, trim and aspect), then
 * the editor. "Trim" in the editor saves pending edits and returns here; the prepared trim screen offers "Continue".
 */
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { matchingWork } from "../flow/clip";
import { projectKey, useProject } from "../hooks/project";
import { useEditor } from "../state/editorStore";
import { Editor } from "./Editor";
import { TrimScreen } from "./TrimScreen";

export function ProjectScreen({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const project = useProject(projectId).data;
  const [trim, setTrim] = useState(false);
  const fresh = !!project?.config.work_asset_id && matchingWork(project.assets, project.config)?.id === project.config.work_asset_id;

  if (fresh && !trim) {
    return (
      <Editor initial={project!} onTrim={async () => {
        const s = useEditor.getState();
        const revision = await s.flush();
        const cur = useEditor.getState();
        // hand the editor's saved state to the trim screen (it reads the project query)
        if (cur.project && cur.config && cur.saveState !== "error" && cur.saveState !== "conflict") {
          qc.setQueryData(projectKey(projectId), { ...cur.project, config: cur.config, current_revision: revision });
        } else await qc.invalidateQueries({ queryKey: projectKey(projectId) });
        setTrim(true);
      }} />
    );
  }
  return <TrimScreen projectId={projectId} onContinue={fresh ? () => setTrim(false) : undefined} />;
}
