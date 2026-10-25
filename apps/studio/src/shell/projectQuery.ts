import type { ProjectSummary } from "@oc/contracts";
import { ApiError } from "@oc/web-core";
import type { QueryClient } from "@tanstack/react-query";
import { PROJECTS_QUERY_KEY } from "./tools";

const isUnauthorized = (e: unknown) => e instanceof ApiError && e.status === 401;

/**
 * The sidebar's project list. A visitor without a session cookie (401) simply has no projects yet: browsing must not
 * start a session, and must not show an error.
 */
export async function listProjectsOrEmpty(
  client: { listProjects(limit?: number): Promise<{ projects: ProjectSummary[] }> },
  limit: number,
): Promise<{ projects: ProjectSummary[] }> {
  try {
    return await client.listProjects(limit);
  } catch (e) {
    if (isUnauthorized(e)) return { projects: [] };
    throw e;
  }
}

/**
 * Runs `call`; a missing session cookie (401) starts a new anonymous session once and retries. The new session's
 * project list replaces the sidebar's empty one.
 */
export async function withSession<T>(
  client: { startSession(): Promise<unknown> },
  qc: Pick<QueryClient, "invalidateQueries">,
  call: () => Promise<T>,
): Promise<T> {
  try {
    return await call();
  } catch (e) {
    if (!isUnauthorized(e)) throw e;
    await client.startSession();
    void qc.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY });
    return call();
  }
}

export const projectQueryKey = (id: string) => ["project", id] as const;

/**
 * The project query of an editor whose store takes over the project after `load()`. The store does not write back to
 * this cache, so it must not outlive the editor: `gcTime: 0` drops it on unmount and the next mount loads the server's
 * current revision (a cached first GET would be autosaved against an old revision: 409 "Changed elsewhere").
 * `staleTime: Infinity` keeps a mounted editor from refetching and calling `load()` over local edits.
 */
export function editorProjectQuery<P>(
  client: { startSession(): Promise<unknown>; getProject(id: string): Promise<P> },
  projectId: string,
  qc: Pick<QueryClient, "invalidateQueries">,
) {
  return {
    queryKey: projectQueryKey(projectId),
    queryFn: () => withSession(client, qc, () => client.getProject(projectId)),
    staleTime: Infinity,
    gcTime: 0,
  };
}
