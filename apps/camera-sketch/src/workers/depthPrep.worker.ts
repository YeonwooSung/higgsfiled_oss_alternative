/// <reference lib="webworker" />
/**
 * One-time depth prep off the main thread: PNG16 bytes in (transferred), `DepthBase` out with its
 * Float32Array transferred back. Pure `prepareDepthBase` — the same code the main-thread fallback runs.
 */
import { prepareDepthBase } from "../model/mesh";

const scope = self as unknown as DedicatedWorkerGlobalScope;
scope.addEventListener("message", (e: MessageEvent<{ png: ArrayBuffer; meta: { min: number; max: number; convention: unknown } }>) => {
  try {
    const base = prepareDepthBase(e.data.png, e.data.meta);
    scope.postMessage({ ok: true, base }, [base.z.buffer]);
  } catch (err) {
    scope.postMessage({ ok: false, error: err instanceof Error ? err.message : String(err) });
  }
});
