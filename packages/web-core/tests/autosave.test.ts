import { describe, expect, it, vi } from "vitest";
import { Autosaver } from "../src/autosave";

describe("Autosaver", () => {
  it("debounces edits by 600ms and saves the latest config with the expected revision", async () => {
    vi.useFakeTimers();
    const save = vi.fn(async (rev: number, cfg: { v: number }) => ({ revision: rev + 1, config: cfg }));
    const s = new Autosaver({ save, revision: 3, onSaved: () => {}, onConflict: () => {}, onError: () => {} });
    s.schedule({ v: 1 });
    vi.advanceTimersByTime(300);
    s.schedule({ v: 2 });
    vi.advanceTimersByTime(599);
    expect(save).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith(3, { v: 2 });
    expect(s.revision).toBe(4);
    vi.useRealTimers();
  });

  it("serializes saves and sends edits made during a save afterwards", async () => {
    vi.useFakeTimers();
    let release!: () => void;
    const calls: number[] = [];
    const save = vi.fn(async (rev: number, cfg: { v: number }) => {
      calls.push(cfg.v);
      if (cfg.v === 1) await new Promise<void>((r) => (release = r));
      return { revision: rev + 1, config: cfg };
    });
    const s = new Autosaver({ save, revision: 1, onSaved: () => {}, onConflict: () => {}, onError: () => {} });
    s.schedule({ v: 1 });
    await vi.advanceTimersByTimeAsync(600);
    s.schedule({ v: 2 });
    await vi.advanceTimersByTimeAsync(600);
    expect(calls).toEqual([1]);
    release();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toEqual([1, 2]);
    expect(save).toHaveBeenLastCalledWith(2, { v: 2 });
    vi.useRealTimers();
  });

  it("reports revision conflicts instead of overwriting", async () => {
    vi.useFakeTimers();
    const conflict = { status: 409, code: "revision_conflict" };
    const onConflict = vi.fn();
    const s = new Autosaver({
      save: async () => {
        throw conflict;
      },
      revision: 1,
      onSaved: () => {},
      onConflict,
      onError: () => {},
    });
    s.schedule({ v: 1 });
    await vi.advanceTimersByTimeAsync(600);
    expect(onConflict).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("is no longer dirty when onSaved fires for the last pending edit", async () => {
    vi.useFakeTimers();
    let dirtyAtSave: boolean | null = null;
    const s: Autosaver<{ v: number }> = new Autosaver({
      save: async (rev, cfg) => ({ revision: rev + 1, config: cfg }),
      revision: 1,
      onSaved: () => (dirtyAtSave = s.dirty),
      onConflict: () => {},
      onError: () => {},
    });
    s.schedule({ v: 1 });
    await vi.advanceTimersByTimeAsync(600);
    expect(dirtyAtSave).toBe(false);
    vi.useRealTimers();
  });
});
