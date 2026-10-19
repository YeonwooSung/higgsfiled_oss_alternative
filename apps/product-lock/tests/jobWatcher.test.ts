import { describe, expect, it, vi } from "vitest";
import type { Job } from "@oc/contracts";
import { watchJob } from "../src/model/jobWatcher";

const job = (state: Job["state"]): Job =>
  ({ id: "j1", state, stage: null, progress: null } as unknown as Job);

class FakeSource {
  static instances: FakeSource[] = [];
  onerror: (() => void) | null = null;
  listeners: Record<string, (e: { data: string; lastEventId: string }) => void> = {};
  closed = false;
  constructor(public url: string) {
    FakeSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: { data: string; lastEventId: string }) => void) {
    this.listeners[type] = fn;
  }
  close() {
    this.closed = true;
  }
}

describe("watchJob", () => {
  it("refreshes the job on SSE events and stops at a terminal state", async () => {
    FakeSource.instances = [];
    const fetchJob = vi.fn().mockResolvedValueOnce(job("running")).mockResolvedValueOnce(job("succeeded"));
    const updates: string[] = [];
    const done = new Promise<Job>((resolve) =>
      watchJob("j1", {
        eventSource: (u) => new FakeSource(u),
        fetchJob,
        onUpdate: (j) => updates.push(j.state),
        onDone: resolve,
      }),
    );
    const src = FakeSource.instances[0];
    expect(src.url).toBe("/api/v1/jobs/j1/events");
    src.listeners.state({ data: '{"state":"running"}', lastEventId: "2" });
    await Promise.resolve();
    src.listeners.state({ data: '{"state":"succeeded"}', lastEventId: "3" });
    const final = await done;
    expect(final.state).toBe("succeeded");
    expect(updates).toEqual(["running", "succeeded"]);
    expect(src.closed).toBe(true);
  });

  it("falls back to polling after 3 stream failures", async () => {
    FakeSource.instances = [];
    const fetchJob = vi.fn().mockResolvedValueOnce(job("running")).mockResolvedValueOnce(job("failed"));
    const sleep = vi.fn(async () => {});
    const done = new Promise<Job>((resolve) =>
      watchJob("j1", { eventSource: (u) => new FakeSource(u), fetchJob, onUpdate: () => {}, onDone: resolve, sleep }),
    );
    for (let i = 0; i < 3; i++) FakeSource.instances[0].onerror?.();
    const final = await done;
    expect(final.state).toBe("failed");
    expect(FakeSource.instances[0].closed).toBe(true);
    expect(fetchJob).toHaveBeenCalledTimes(2);
  });
});
