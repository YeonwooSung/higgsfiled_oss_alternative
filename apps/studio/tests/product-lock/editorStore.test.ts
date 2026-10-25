import type { Project } from "@oc/contracts";
import { describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ getProject: vi.fn(), patchProject: vi.fn() }));
vi.mock("../../src/features/product-lock/api/client", () => ({ api }));
const { useEditor } = await import("../../src/features/product-lock/state/editorStore");

const job = (id: string) => ({ id, kind: "product.render", state: "succeeded", created_at: "2026-01-01T00:00:00Z" });
const project = (id: string, jobs: string[]) =>
  ({ id, app_slug: "product-lock", title: "t", current_revision: 1, assets: [], recent_jobs: jobs.map(job), config: {} }) as unknown as Project;

describe("jobs belong to the loaded project", () => {
  it("opening another project drops the previous project's jobs; reloading the same project keeps them", () => {
    useEditor.getState().load(project("a", ["ja"]));
    useEditor.getState().load(project("a", []));
    expect(Object.keys(useEditor.getState().jobs)).toEqual(["ja"]);
    useEditor.getState().load(project("b", ["jb"]));
    expect(Object.keys(useEditor.getState().jobs)).toEqual(["jb"]);
  });
});

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe("view and selection belong to the loaded project", () => {
  it("opening another project resets the result view, the selected render and the tool", () => {
    useEditor.getState().load(project("a", ["ja"]));
    useEditor.getState().selectRender("ja");
    useEditor.getState().setTool("add");
    useEditor.getState().selectRender("ja");
    expect(useEditor.getState().view).toBe("result");
    useEditor.getState().load(project("b", []));
    const s = useEditor.getState();
    expect(s.view).toBe("compose");
    expect(s.selectedRenderJobId).toBeNull();
    expect(s.tool).toBe("select");
  });
  it("reloading the same project keeps them", () => {
    useEditor.getState().load(project("a", ["ja"]));
    useEditor.getState().selectRender("ja");
    useEditor.getState().load(project("a", ["ja"]));
    expect(useEditor.getState().view).toBe("result");
    expect(useEditor.getState().selectedRenderJobId).toBe("ja");
  });
});

describe("late responses for a project that is no longer open are dropped", () => {
  it("refresh() of project A resolving after B opened does not write A into B", async () => {
    useEditor.getState().load(project("a", []));
    const late = deferred<Project>();
    api.getProject.mockReturnValueOnce(late.promise);
    const pending = useEditor.getState().refresh();
    useEditor.getState().load(project("b", ["jb"]));
    late.resolve(project("a", ["ja"]));
    await pending;
    expect(useEditor.getState().project?.id).toBe("b");
    expect(Object.keys(useEditor.getState().jobs)).toEqual(["jb"]);
  });

  it("a save conflict of project A resolving after B opened does not load A", async () => {
    useEditor.getState().load(project("a", []));
    api.patchProject.mockRejectedValueOnce(Object.assign(new Error("conflict"), { status: 409 }));
    const late = deferred<Project>();
    api.getProject.mockReturnValueOnce(late.promise);
    useEditor.getState().update(() => {});
    const saving = useEditor.getState().flush();
    await saving;
    useEditor.getState().load(project("b", ["jb"]));
    late.resolve(project("a", ["ja"]));
    await new Promise((r) => setTimeout(r, 0));
    expect(useEditor.getState().project?.id).toBe("b");
    expect(useEditor.getState().saveState).toBe("saved");
  });
});
