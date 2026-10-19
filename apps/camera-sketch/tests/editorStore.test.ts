import type { CameraSketchProject } from "@oc/contracts";
import { describe, expect, it, vi } from "vitest";

vi.mock("../src/api/client", () => ({ api: { patchProject: vi.fn(async () => { throw new Error("no network in tests"); }) } }));
const { useEditor } = await import("../src/state/editorStore");

const project = (): CameraSketchProject => ({
  id: "p", app_slug: "camera-sketch", title: "t", current_revision: 1, assets: [], recent_jobs: [], created_at: "x",
  config: { scene_asset_id: null, depth_asset_id: null, path: { push_ratio: 0.06 } },
} as unknown as CameraSketchProject);

describe("automatic links are not undo steps", () => {
  it("link() adds no history entry", () => {
    useEditor.getState().load(project());
    useEditor.getState().link((c) => { c.scene_asset_id = "s"; c.depth_asset_id = null; });
    useEditor.getState().link((c) => { c.depth_asset_id = "d"; });
    expect(useEditor.getState().canUndo).toBe(false);
    expect(useEditor.getState().config?.depth_asset_id).toBe("d");
  });
  it("undo after a user edit returns to the pre-edit state, keeping the links", () => {
    useEditor.getState().load(project());
    useEditor.getState().link((c) => { c.scene_asset_id = "s"; });
    useEditor.getState().update((c) => { c.path.push_ratio = 0.1; });
    useEditor.getState().link((c) => { c.depth_asset_id = "d"; });
    expect(useEditor.getState().canUndo).toBe(true);
    useEditor.getState().undo();
    const cfg = useEditor.getState().config!;
    expect(cfg.path.push_ratio).toBe(0.06);
    expect(cfg.scene_asset_id).toBe("s");
    expect(cfg.depth_asset_id).toBe("d");
    expect(useEditor.getState().canUndo).toBe(false);
    useEditor.getState().redo();
    expect(useEditor.getState().config!.depth_asset_id).toBe("d");
  });
});

describe("currentServerPath", () => {
  const response = { path: { status: "ok" } } as unknown as import("@oc/contracts").CompileResponse;
  it("uses the server path only for the current, saved revision", async () => {
    const { currentServerPath } = await import("../src/state/editorStore");
    expect(currentServerPath({ serverCompile: null, revision: 3, saveState: "saved" })).toBeNull();
    expect(currentServerPath({ serverCompile: { revision: 3, response }, revision: 3, saveState: "saved" })).toBe(response.path);
    expect(currentServerPath({ serverCompile: { revision: 2, response }, revision: 3, saveState: "saved" })).toBeNull();
    expect(currentServerPath({ serverCompile: { revision: 3, response }, revision: 3, saveState: "saving" })).toBeNull();
  });
});
