"use client";

import type { Job, JobKind } from "@oc/contracts";
import { watchJob, type SourceLike } from "@oc/web-core";
import { api } from "../api/client";
import { useEditor } from "./editorStore";

export function followJob(job: Job): Promise<Job> {
  useEditor.getState().trackJob(job);
  return new Promise((resolve) => {
    watchJob(job.id, {
      eventSource: (url) => new EventSource(url, { withCredentials: true }) as unknown as SourceLike,
      fetchJob: (id) => api.getJob(id),
      onUpdate: (j) => useEditor.getState().trackJob(j),
      onDone: (j) => {
        useEditor.getState().trackJob(j);
        void useEditor.getState().refresh();
        resolve(j);
      },
    });
  });
}

/** Saves pending edits first so the job pins the revision the user is looking at. */
export async function startJob(kind: JobKind, options: Record<string, unknown> = {}): Promise<Job> {
  const state = useEditor.getState();
  const revision = await state.flush();
  const job = await api.createJob(state.project!.id, kind, revision, options, crypto.randomUUID());
  return followJob(job);
}
