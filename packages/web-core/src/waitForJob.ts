import { TERMINAL_JOB_STATES, type Job } from "@oc/contracts";

/** Poll a job until it succeeds; any other terminal state or a timeout rejects. */
export async function waitForJob(
  getJob: (id: string) => Promise<Job>,
  id: string,
  { tries = 100, intervalMs = 300, sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms)) } = {},
): Promise<Job> {
  for (let i = 0; i < tries; i++) {
    const job = await getJob(id);
    if (job.state === "succeeded") return job;
    if (TERMINAL_JOB_STATES.includes(job.state)) throw new Error(job.error?.message ?? `job ${job.state}`);
    await sleep(intervalMs);
  }
  throw new Error("job timed out");
}
