import type { CameraSketchConfig, CameraSketchProject, CompileResponse } from "@oc/contracts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  patchProject: vi.fn(async (..._a: unknown[]): Promise<unknown> => { throw new Error("no network in tests"); }),
  compilePath: vi.fn(async (..._a: unknown[]): Promise<unknown> => { throw new Error("no network in tests"); }),
  getProject: vi.fn(),
}));
vi.mock("../src/api/client", () => ({ api }));
const { useEditor, currentServerPath, renderGate, stopCompileLoop } = await import("../src/state/editorStore");
const { sceneToLink } = await import("../src/flow/scene");

const project = (over: Partial<CameraSketchConfig> = {}): CameraSketchProject => ({
  id: "p", app_slug: "camera-sketch", title: "t", current_revision: 1, assets: [], recent_jobs: [], created_at: "x", upstream_mock: false, handoff: null,
  config: {
    scene_asset_id: null, depth_asset_id: null, target: { u: 0.5, v: 0.5, depth_scale: 1 },
    path: { preset: "slide", sketch_space: "target_screen_position", sketch_points: [], lateral_ratio: 0.04, push_ratio: 0.06, arc_deg: 0, easing: "ease_in_out" },
    ...over,
  },
} as unknown as CameraSketchProject);

const response = (status: CompileResponse["status"], tag: number, extra: Partial<CompileResponse["path"]> = {}): CompileResponse => ({
  path: { status, reasons: [], suggested_params: null, samples: [], keyframes: [], tag, ...extra } as unknown as CompileResponse["path"],
  control_points: [], warnings: [], status, hole_ratio: null, deferred: false,
});

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

/** PATCH succeeds, bumping the revision by one and echoing the config. */
function serverAcceptsSaves() {
  api.patchProject.mockImplementation(async (_id: unknown, rev: unknown, cfg: unknown) => ({ current_revision: (rev as number) + 1, config: cfg }));
}

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

describe("scene auto-link and undo", () => {
  const scene = (id: string, created_at: string) => ({
    id, project_id: "p", role: "scene", state: "ready", created_at, meta: {}, source_asset_id: null,
  }) as unknown as CameraSketchProject["assets"][number];
  const withAssets = (p: CameraSketchProject, assets: CameraSketchProject["assets"]) => ({ ...p, assets });

  it("links an uploaded scene once; undo of a later scene change is not reverted", () => {
    const s1 = scene("s1", "2026-01-01T00:00:00Z"), s2 = scene("s2", "2026-01-02T00:00:00Z");
    useEditor.getState().load(withAssets(project(), [s1]));
    const linkNow = () => sceneToLink(useEditor.getState().project!.assets, useEditor.getState().config!, useEditor.getState().handledSceneIds);
    expect(linkNow()).toBe("s1");
    useEditor.getState().linkScene("s1");
    expect(linkNow()).toBeNull();
    // a second upload arrives (refresh) and is linked once
    useEditor.setState({ project: { ...useEditor.getState().project!, assets: [s1, s2] } });
    expect(linkNow()).toBe("s2");
    useEditor.getState().linkScene("s2");
    // the user switches back to s1 (an undoable edit), then undoes and redoes it
    useEditor.getState().update((c) => { c.scene_asset_id = "s1"; c.depth_asset_id = null; });
    expect(linkNow()).toBeNull(); // not re-linked to the newest scene
    useEditor.getState().undo();
    expect(useEditor.getState().config!.scene_asset_id).toBe("s2");
    expect(linkNow()).toBeNull();
    useEditor.getState().redo();
    expect(useEditor.getState().config!.scene_asset_id).toBe("s1");
    expect(linkNow()).toBeNull();
  });

  it("a reload with a scene set does not relink older uploads", () => {
    const s1 = scene("s1", "2026-01-01T00:00:00Z"), s2 = scene("s2", "2026-01-02T00:00:00Z");
    useEditor.getState().load(withAssets(project({ scene_asset_id: "s1" }), [s1, s2]));
    expect(sceneToLink(useEditor.getState().project!.assets, useEditor.getState().config!, useEditor.getState().handledSceneIds)).toBeNull();
  });
});

describe("currentServerPath", () => {
  const r = response("ok", 0);
  it("uses the server path only for the current, saved revision", () => {
    expect(currentServerPath({ serverCompile: null, revision: 3, saveState: "saved" })).toBeNull();
    expect(currentServerPath({ serverCompile: { revision: 3, response: r }, revision: 3, saveState: "saved" })).toBe(r.path);
    expect(currentServerPath({ serverCompile: { revision: 2, response: r }, revision: 3, saveState: "saved" })).toBeNull();
    expect(currentServerPath({ serverCompile: { revision: 3, response: r }, revision: 3, saveState: "saving" })).toBeNull();
  });
});

describe("compile loop", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    api.patchProject.mockReset();
    api.compilePath.mockReset();
    serverAcceptsSaves();
  });
  afterEach(() => vi.useRealTimers());

  it("compiles each saved revision 300 ms after autosave and ignores late responses", async () => {
    const calls: { rev: number; d: ReturnType<typeof deferred<CompileResponse>> }[] = [];
    api.compilePath.mockImplementation((_id: unknown, body: unknown) => {
      const d = deferred<CompileResponse>();
      calls.push({ rev: (body as { revision: number }).revision, d });
      return d.promise;
    });
    useEditor.getState().load(project({ scene_asset_id: "s", depth_asset_id: "d" } as Partial<CameraSketchConfig>));
    await vi.advanceTimersByTimeAsync(299);
    expect(calls).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls.map((c) => c.rev)).toEqual([1]);

    useEditor.getState().update((c) => { c.path.push_ratio = 0.08; });
    await vi.advanceTimersByTimeAsync(600); // autosave -> revision 2
    expect(useEditor.getState().revision).toBe(2);
    expect(calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(300);
    expect(calls.map((c) => c.rev)).toEqual([1, 2]);
    const body = api.compilePath.mock.calls[1][1] as Record<string, unknown>;
    expect(body).toMatchObject({ revision: 2, preset: "slide", sketch: [], parameters: { lateral_ratio: 0.04, push_ratio: 0.08, arc_deg: 0, easing: "ease_in_out" } });

    useEditor.getState().update((c) => { c.path.push_ratio = 0.09; });
    await vi.advanceTimersByTimeAsync(900); // revision 3 saved + compile requested
    expect(calls.map((c) => c.rev)).toEqual([1, 2, 3]);

    calls[2].d.resolve(response("ok", 3));
    await vi.advanceTimersByTimeAsync(0);
    expect(useEditor.getState().serverCompile?.revision).toBe(3);
    // Responses for older revisions arrive late: never applied.
    calls[1].d.resolve(response("invalid", 2));
    calls[0].d.resolve(response("invalid", 1));
    await vi.advanceTimersByTimeAsync(0);
    expect(useEditor.getState().serverCompile?.revision).toBe(3);
    expect(currentServerPath(useEditor.getState())).toMatchObject({ status: "ok", tag: 3 });
  });

  it("debounces compile requests while edits keep coming", async () => {
    api.compilePath.mockImplementation(async () => response("ok", 0));
    useEditor.getState().load(project({ scene_asset_id: "s", depth_asset_id: "d" } as Partial<CameraSketchConfig>));
    await vi.advanceTimersByTimeAsync(300);
    expect(api.compilePath).toHaveBeenCalledTimes(1);
    useEditor.getState().update((c) => { c.path.push_ratio = 0.07; });
    await vi.advanceTimersByTimeAsync(400);
    useEditor.getState().update((c) => { c.path.push_ratio = 0.08; });
    await vi.advanceTimersByTimeAsync(400);
    expect(api.compilePath).toHaveBeenCalledTimes(1); // autosave debounce restarted, nothing saved yet
    await vi.advanceTimersByTimeAsync(500);
    expect(api.compilePath).toHaveBeenCalledTimes(2);
    expect((api.compilePath.mock.calls[1][1] as { revision: number }).revision).toBe(2);
  });

  it("does not compile without a depth map", async () => {
    useEditor.getState().load(project());
    useEditor.getState().update((c) => { c.path.push_ratio = 0.08; });
    await vi.advanceTimersByTimeAsync(2000);
    expect(api.patchProject).toHaveBeenCalled();
    expect(api.compilePath).not.toHaveBeenCalled();
  });

  it("retries a failed compile once after 2 s, then records the error and offers Retry", async () => {
    api.compilePath.mockImplementation(async () => { throw Object.assign(new Error("x"), { code: "depth_missing" }); });
    useEditor.getState().load(project({ scene_asset_id: "s", depth_asset_id: "d" } as Partial<CameraSketchConfig>));
    await vi.advanceTimersByTimeAsync(300);
    expect(api.compilePath).toHaveBeenCalledTimes(1);
    expect(useEditor.getState().compileError).toBeNull(); // retry pending
    await vi.advanceTimersByTimeAsync(1999);
    expect(api.compilePath).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(api.compilePath).toHaveBeenCalledTimes(2);
    expect(useEditor.getState().compileError).toBe("depth_missing");
    expect(useEditor.getState().serverCompile).toBeNull();
    await vi.advanceTimersByTimeAsync(5000);
    expect(api.compilePath).toHaveBeenCalledTimes(2); // no endless retries
    api.compilePath.mockImplementation(async () => response("ok", 1));
    useEditor.getState().retryCompile();
    expect(useEditor.getState().compileError).toBeNull();
    await vi.advanceTimersByTimeAsync(0);
    expect(api.compilePath).toHaveBeenCalledTimes(3);
    expect(useEditor.getState().serverCompile?.revision).toBe(1);
  });

  it("stopCompileLoop cancels a scheduled compile (editor unmount)", async () => {
    api.compilePath.mockImplementation(async () => response("ok", 0));
    useEditor.getState().load(project({ scene_asset_id: "s", depth_asset_id: "d" } as Partial<CameraSketchConfig>));
    stopCompileLoop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(api.compilePath).not.toHaveBeenCalled();
  });
});

describe("Apply reduced motion", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    api.patchProject.mockReset();
    api.compilePath.mockReset();
    serverAcceptsSaves();
  });
  afterEach(() => vi.useRealTimers());

  it("writes suggested_params as a new, undoable revision", async () => {
    useEditor.getState().load(project());
    useEditor.getState().applySuggestedParams({ lateral_ratio: 0.02, push_ratio: 0.03, arc_deg: 0 });
    const cfg = useEditor.getState().config!;
    expect(cfg.path).toMatchObject({ lateral_ratio: 0.02, push_ratio: 0.03, arc_deg: 0, preset: "slide" });
    expect(useEditor.getState().canUndo).toBe(true);
    await vi.advanceTimersByTimeAsync(600);
    expect(api.patchProject).toHaveBeenCalledTimes(1);
    expect(api.patchProject.mock.calls[0][1]).toBe(1);
    expect((api.patchProject.mock.calls[0][2] as CameraSketchConfig).path.lateral_ratio).toBe(0.02);
    expect(useEditor.getState().revision).toBe(2);

    useEditor.getState().undo();
    expect(useEditor.getState().config!.path).toMatchObject({ lateral_ratio: 0.04, push_ratio: 0.06 });
    await vi.advanceTimersByTimeAsync(600);
    expect(useEditor.getState().revision).toBe(3); // undo is saved as another revision
  });

  it("sketch_scale scales the stored sketch displacement in the same undo step", () => {
    useEditor.getState().load(project());
    useEditor.getState().update((c) => { c.path.sketch_points = [{ t: 0, u: 0.5, v: 0.5 }, { t: 0.3, u: 0.7, v: 0.4 }, { t: 1, u: 0.9, v: 0.5 }]; });
    useEditor.getState().applySuggestedParams({ lateral_ratio: 0.02, push_ratio: 0.03, arc_deg: 0, sketch_scale: 0.5 });
    expect(useEditor.getState().config!.path.sketch_points).toEqual([{ t: 0, u: 0.5, v: 0.5 }, { t: 0.3, u: 0.6, v: 0.45 }, { t: 1, u: 0.7, v: 0.5 }]);
    expect(useEditor.getState().config!.path.lateral_ratio).toBe(0.02);
    useEditor.getState().undo();
    expect(useEditor.getState().config!.path.sketch_points[2]).toEqual({ t: 1, u: 0.9, v: 0.5 });
    expect(useEditor.getState().config!.path.lateral_ratio).toBe(0.04);
  });
  it("ignores unknown or non-finite keys", () => {
    useEditor.getState().load(project());
    useEditor.getState().applySuggestedParams({ lateral_ratio: Number.NaN, bogus: 1 } as Record<string, number>);
    expect(useEditor.getState().canUndo).toBe(false);
  });
});

describe("sketch completion", () => {
  it("stores control points and proposes (never applies) a lateral sign flip for slide", () => {
    useEditor.getState().load(project());
    useEditor.getState().commitSketch([[0.3, 0.5], [0.45, 0.5], [0.6, 0.5]]);
    const s = useEditor.getState();
    expect(s.config!.path.sketch_points).toEqual([{ t: 0, u: 0.3, v: 0.5 }, { t: 1, u: 0.6, v: 0.5 }]);
    expect(s.config!.path.lateral_ratio).toBe(0.04);
    expect(s.lateralProposal).toBe(-0.04);
    s.applyLateralProposal();
    expect(useEditor.getState().config!.path.lateral_ratio).toBe(-0.04);
    expect(useEditor.getState().lateralProposal).toBeNull();
    useEditor.getState().undo();
    expect(useEditor.getState().config!.path.lateral_ratio).toBe(0.04);
  });
  it("Restore preset clears the sketch but keeps preset and params", () => {
    useEditor.getState().load(project());
    useEditor.getState().commitSketch([[0.3, 0.5], [0.6, 0.5]]);
    useEditor.getState().restorePreset();
    expect(useEditor.getState().config!.path).toMatchObject({ sketch_points: [], preset: "slide", lateral_ratio: 0.04, push_ratio: 0.06 });
    expect(useEditor.getState().lateralProposal).toBeNull();
  });
});

describe("renderGate", () => {
  const base = { revision: 2, saveState: "saved" as const, config: { scene_asset_id: "s", depth_asset_id: "d" } as unknown as CameraSketchConfig };
  it("blocks until the server compile for the current revision arrives", () => {
    expect(renderGate({ ...base, serverCompile: null })).toEqual({ canRender: false, blockReasons: ["compile_pending"] });
    expect(renderGate({ ...base, serverCompile: { revision: 1, response: response("ok", 1) } }).canRender).toBe(false);
  });
  it("blocks with the server's reasons when invalid; allows ok and warning", () => {
    const bad = response("invalid", 2, { reasons: ["target_out_of_frame", "framing_clamped"] });
    expect(renderGate({ ...base, serverCompile: { revision: 2, response: bad } })).toEqual({ canRender: false, blockReasons: ["target_out_of_frame"] });
    expect(renderGate({ ...base, serverCompile: { revision: 2, response: response("warning", 2) } })).toEqual({ canRender: true, blockReasons: [] });
  });
  it("a failing autosave blocks with save_failed, not compile_pending", () => {
    expect(renderGate({ ...base, saveState: "error", serverCompile: null })).toEqual({ canRender: false, blockReasons: ["save_failed"] });
    expect(renderGate({ ...base, saveState: "error", serverCompile: { revision: 2, response: response("ok", 2) } }).blockReasons).toEqual(["save_failed"]);
  });
  it("needs a depth map", () => {
    expect(renderGate({ ...base, config: { scene_asset_id: "s", depth_asset_id: null } as unknown as CameraSketchConfig, serverCompile: null }).blockReasons).toEqual(["depth_missing"]);
  });
});

describe("coalesced edits", () => {
  it("a slider drag is one undo step; a different control starts a new one", () => {
    useEditor.getState().load(project());
    const { update } = useEditor.getState();
    update((c) => { c.path.push_ratio = 0.07; }, "push");
    update((c) => { c.path.push_ratio = 0.08; }, "push");
    update((c) => { c.path.push_ratio = 0.09; }, "push");
    update((c) => { c.path.easing = "linear"; }, "easing");
    useEditor.getState().undo();
    expect(useEditor.getState().config!.path.push_ratio).toBe(0.09);
    useEditor.getState().undo();
    expect(useEditor.getState().config!.path.push_ratio).toBe(0.06);
    expect(useEditor.getState().canUndo).toBe(false);
  });
  it("a no-op edit adds no history", () => {
    useEditor.getState().load(project());
    useEditor.getState().update((c) => { c.path.push_ratio = 0.06; });
    expect(useEditor.getState().canUndo).toBe(false);
  });
});
