"use client";

import type { Job, JobKind } from "@oc/contracts";
import { api } from "../api/client";
import { watchJob, type SourceLike } from "@oc/web-core";
import { useEditor } from "./editorStore";

export function followJob(job: Job): Promise<Job> {
  const { trackJob } = useEditor.getState();
  trackJob(job);
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
export async function startJob(kind: JobKind, options: Record<string, unknown>): Promise<Job> {
  const state = useEditor.getState();
  const project = state.project!;
  const revision = await state.flush();
  const job = await api.createJob(project.id, kind, revision, options, crypto.randomUUID());
  return followJob(job);
}

export function randomSeed(): number {
  return crypto.getRandomValues(new Uint32Array(1))[0] & 0x7fffffff;
}
