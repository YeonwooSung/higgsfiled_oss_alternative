import { ApiError } from "@oc/web-core";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { editorProjectQuery, listProjectsOrEmpty, withSession } from "../../src/shell/projectQuery";
import { PROJECTS_QUERY_KEY } from "../../src/shell/tools";

const unauthorized = () => new ApiError(401, "session_required", "no session");

describe("listProjectsOrEmpty (sidebar)", () => {
  it("is an empty list for a visitor without a session (401), without starting one", async () => {
    const client = { listProjects: vi.fn(async () => { throw unauthorized(); }), startSession: vi.fn() };
    await expect(listProjectsOrEmpty(client, 50)).resolves.toEqual({ projects: [] });
    expect(client.listProjects).toHaveBeenCalledWith(50);
    expect(client.startSession).not.toHaveBeenCalled();
  });
  it("passes other errors through", async () => {
    const boom = new ApiError(500, "internal", "x");
    await expect(listProjectsOrEmpty({ listProjects: async () => { throw boom; } }, 50)).rejects.toBe(boom);
  });
  it("returns the session's projects", async () => {
    const data = { projects: [] };
    await expect(listProjectsOrEmpty({ listProjects: async () => data }, 50)).resolves.toBe(data);
  });
});

describe("withSession", () => {
  it("starts a session on 401, refreshes the sidebar's project list, and retries once", async () => {
    const qc = { invalidateQueries: vi.fn(async () => {}) };
    const client = { startSession: vi.fn(async () => ({ session_id: "s" })) };
    const call = vi.fn().mockRejectedValueOnce(unauthorized()).mockResolvedValueOnce("ok");
    await expect(withSession(client, qc, call)).resolves.toBe("ok");
    expect(client.startSession).toHaveBeenCalledTimes(1);
    expect(qc.invalidateQueries).toHaveBeenCalledWith({ queryKey: PROJECTS_QUERY_KEY });
    expect(call).toHaveBeenCalledTimes(2);
  });
  it("does not start a session or invalidate on other errors", async () => {
    const qc = { invalidateQueries: vi.fn() };
    const client = { startSession: vi.fn() };
    const boom = new ApiError(404, "not_found", "x");
    await expect(withSession(client, qc, async () => { throw boom; })).rejects.toBe(boom);
    expect(client.startSession).not.toHaveBeenCalled();
    expect(qc.invalidateQueries).not.toHaveBeenCalled();
  });
});

describe("editorProjectQuery: a remounted editor loads the server's revision, not the first cached GET", () => {
  /** Observer subscribe/unsubscribe is what useQuery does on mount/unmount; `loads` is what the editor's load() gets. */
  async function mount(qc: QueryClient, client: Parameters<typeof editorProjectQuery>[0], loads: number[]) {
    const observer = new QueryObserver(qc, editorProjectQuery(client, "p1", qc));
    const first = observer.getCurrentResult().data as { current_revision: number } | undefined;
    if (first) loads.push(first.current_revision);
    let resolve!: () => void;
    const settled = new Promise<void>((r) => (resolve = r));
    const unsubscribe = observer.subscribe((r) => {
      if (r.data && r.isFetchedAfterMount && !r.isFetching) {
        const rev = (r.data as { current_revision: number }).current_revision;
        if (loads.at(-1) !== rev) loads.push(rev);
        resolve();
      }
    });
    await settled;
    return unsubscribe;
  }

  it("drops the cached project on unmount and refetches on the next mount", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let revision = 1;
    const client = { startSession: vi.fn(), getProject: vi.fn(async (id: string) => ({ id, current_revision: revision })) };
    const loads: number[] = [];
    const unmount = await mount(qc, client, loads);
    expect(loads).toEqual([1]);
    unmount();
    revision = 4; // the editor autosaved three more revisions before it went away
    await new Promise((r) => setTimeout(r, 5));
    expect(qc.getQueryData(["project", "p1"])).toBeUndefined();
    (await mount(qc, client, loads))();
    expect(loads).toEqual([1, 4]);
    expect(client.getProject).toHaveBeenCalledTimes(2);
  });

  it("never refetches while mounted (a background refetch would load() over local edits)", () => {
    const opts = editorProjectQuery({ startSession: vi.fn(), getProject: vi.fn() }, "p1", new QueryClient());
    expect(opts.staleTime).toBe(Infinity);
    expect(opts.gcTime).toBe(0);
    expect(opts.queryKey).toEqual(["project", "p1"]);
  });
});
