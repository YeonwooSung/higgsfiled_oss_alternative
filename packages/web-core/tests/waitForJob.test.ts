import { describe, expect, it, vi } from "vitest";
import type { Job } from "@oc/contracts";
import { waitForJob } from "../src/waitForJob";

const j = (state: Job["state"]) => ({ id: "j", state, error: null } as unknown as Job);
const noSleep = async () => {};

describe("waitForJob", () => {
  it("resolves on success", async () => {
    const get = vi.fn().mockResolvedValueOnce(j("running")).mockResolvedValueOnce(j("succeeded"));
    expect((await waitForJob(get, "j", { sleep: noSleep })).state).toBe("succeeded");
  });

  it("rejects when the job ends in any non-success state", async () => {
    await expect(waitForJob(async () => j("cancelled"), "j", { sleep: noSleep })).rejects.toThrow();
  });

  it("rejects on timeout instead of continuing silently", async () => {
    await expect(waitForJob(async () => j("running"), "j", { sleep: noSleep, tries: 3 })).rejects.toThrow(/timed out/);
  });
});
