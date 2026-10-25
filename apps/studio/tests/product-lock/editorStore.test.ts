import type { Project } from "@oc/contracts";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../src/features/product-lock/api/client", () => ({ api: {} }));
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
