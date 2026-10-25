import type { VfxValidateResponse } from "@oc/contracts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../src/features/pocket-vfx/api/client", () => ({ api: {} }));
const { ValidateLoop, VALIDATE_DEBOUNCE_MS, currentValidation } = await import("../../src/features/pocket-vfx/hooks/validate");

const report = (code: string | null): VfxValidateResponse => ({
  valid: code === null,
  blocking_errors: code ? [{ code, field: "config", message: code }] : [],
  warnings: [],
});

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("validate loop (debounced 300 ms, latest revision wins)", () => {
  it("debounces: only the last revision of a burst is validated", async () => {
    const call = vi.fn(async (rev: number) => report(rev === 4 ? null : "timing_order"));
    const seen: number[] = [];
    const loop = new ValidateLoop(call, (r) => seen.push(r.revision));
    loop.request(2, "2");
    loop.request(3, "3");
    await vi.advanceTimersByTimeAsync(VALIDATE_DEBOUNCE_MS - 1);
    expect(call).not.toHaveBeenCalled();
    loop.request(4, "4");
    await vi.advanceTimersByTimeAsync(VALIDATE_DEBOUNCE_MS);
    expect(call).toHaveBeenCalledTimes(1);
    expect(call).toHaveBeenCalledWith(4);
    expect(seen).toEqual([4]);
  });

  it("a late response for an older revision is ignored", async () => {
    const pending = new Map<number, ReturnType<typeof deferred<VfxValidateResponse>>>();
    const call = vi.fn((rev: number) => {
      const d = deferred<VfxValidateResponse>();
      pending.set(rev, d);
      return d.promise;
    });
    const results: { revision: number; code: string | null }[] = [];
    const loop = new ValidateLoop(call, (r) => results.push({ revision: r.revision, code: r.result?.blocking_errors[0]?.code ?? null }));
    loop.request(5, "5");
    await vi.advanceTimersByTimeAsync(VALIDATE_DEBOUNCE_MS);
    loop.request(6, "6");
    await vi.advanceTimersByTimeAsync(VALIDATE_DEBOUNCE_MS);
    expect(call.mock.calls.map(([r]) => r)).toEqual([5, 6]);
    pending.get(6)!.resolve(report(null));
    await vi.runAllTimersAsync();
    pending.get(5)!.resolve(report("mask_required")); // arrives after the newer one
    await vi.runAllTimersAsync();
    expect(results).toEqual([{ revision: 6, code: null }]);
  });

  it("a failed call reports an error for its revision only when it is the latest", async () => {
    const call = vi.fn(async () => { throw new Error("offline"); });
    const results: unknown[] = [];
    const loop = new ValidateLoop(call, (r) => results.push(r));
    loop.request(7, "7|a");
    await vi.advanceTimersByTimeAsync(VALIDATE_DEBOUNCE_MS);
    await vi.runAllTimersAsync();
    expect(results).toEqual([{ revision: 7, key: "7|a", result: null, error: true }]);
  });

  it("cancel drops a scheduled call and any answer in flight", async () => {
    const d = deferred<VfxValidateResponse>();
    const call = vi.fn(() => d.promise);
    const results: unknown[] = [];
    const loop = new ValidateLoop(call, (r) => results.push(r));
    loop.request(1, "1");
    await vi.advanceTimersByTimeAsync(VALIDATE_DEBOUNCE_MS);
    loop.cancel();
    d.resolve(report(null));
    await vi.runAllTimersAsync();
    expect(results).toEqual([]);
  });

  it("a result counts only for the revision and server state it was asked for", () => {
    const v = { revision: 3, key: "3|x", result: report(null), error: false };
    expect(currentValidation(v, "3|x")).toBe(v);
    expect(currentValidation(v, "4|x")).toBeNull();
    expect(currentValidation(v, "3|y")).toBeNull(); // e.g. a track finished since
    expect(currentValidation(null, "3|x")).toBeNull();
  });
});
