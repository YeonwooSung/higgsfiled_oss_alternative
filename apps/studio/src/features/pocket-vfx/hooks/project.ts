"use client";

import { TERMINAL_JOB_STATES, type Job, type PocketVfxConfig, type PocketVfxProject } from "@oc/contracts";
import { watchJob, type SourceLike } from "@oc/web-core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";

export const projectKey = (id: string) => ["project", id] as const;

/** The project (config, assets, recent jobs); a missing session cookie (401) starts a new anonymous session once. */
export function useProject(projectId: string) {
  return useQuery({
    queryKey: projectKey(projectId),
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
}

export type SaveState = "idle" | "saving" | "error" | "conflict";

/**
 * Strictly sequential config PATCHes, each on the latest saved revision. `mutate` gets the current saved config and
 * project; returning an equal config skips the PATCH. A 409 reloads the project and reports `conflict`.
 */
export function useConfigSaver(projectId: string) {
  const qc = useQueryClient();
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  const inFlight = useRef(0);
  const [state, setState] = useState<SaveState>("idle");

  const save = useCallback((mutate: (config: PocketVfxConfig, project: PocketVfxProject) => PocketVfxConfig): Promise<PocketVfxProject> => {
    const run = chain.current.then(async () => {
      const project = qc.getQueryData<PocketVfxProject>(projectKey(projectId));
      if (!project) throw new Error("project not loaded");
      const next = mutate(project.config, project);
      if (JSON.stringify(next) === JSON.stringify(project.config)) return project;
      const saved = await api.patchProject(projectId, project.current_revision, next);
      qc.setQueryData(projectKey(projectId), saved);
      return saved;
    });
    inFlight.current += 1;
    setState("saving");
    chain.current = run.then(
      () => {
        inFlight.current -= 1;
        if (inFlight.current === 0) setState("idle");
      },
      async (e) => {
        inFlight.current -= 1;
        if (e instanceof ApiError && e.status === 409) {
          setState("conflict");
          await qc.invalidateQueries({ queryKey: projectKey(projectId) });
        } else {
          setState("error");
        }
      },
    );
    return run;
  }, [projectId, qc]);

  return { save, state };
}

/**
 * Live job updates over SSE (polling fallback in `watchJob`); a finished job reloads the project so its outputs and
 * result arrive with the assets. `jobs` holds the newest known state of every followed job.
 */
export function useJobs(projectId: string) {
  const qc = useQueryClient();
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const watching = useRef(new Map<string, () => void>());

  const follow = useCallback((job: Job) => {
    setJobs((all) => ({ ...all, [job.id]: job }));
    if (TERMINAL_JOB_STATES.includes(job.state) || watching.current.has(job.id)) return;
    const stop = watchJob(job.id, {
      eventSource: (url) => new EventSource(url, { withCredentials: true }) as unknown as SourceLike,
      fetchJob: (id) => api.getJob(id),
      onUpdate: (j) => setJobs((all) => ({ ...all, [j.id]: j })),
      onDone: (j) => {
        watching.current.delete(j.id);
        setJobs((all) => ({ ...all, [j.id]: j }));
        void qc.invalidateQueries({ queryKey: projectKey(projectId) });
      },
    });
    watching.current.set(job.id, stop);
  }, [projectId, qc]);

  useEffect(() => {
    const live = watching.current;
    return () => {
      for (const stop of live.values()) stop();
      live.clear();
    };
  }, []);

  return { jobs, follow };
}

/** Recent jobs of the project with the newer live state of followed jobs. */
export function mergeJobs(recent: Job[], live: Record<string, Job>): Job[] {
  const byId = new Map(recent.map((j) => [j.id, j]));
  for (const j of Object.values(live)) byId.set(j.id, j);
  return [...byId.values()];
}
