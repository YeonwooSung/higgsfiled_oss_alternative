import { TERMINAL_JOB_STATES, type Job } from "@oc/contracts";

interface SourceLike {
  onerror: ((e: unknown) => void) | null;
  addEventListener(type: string, fn: (e: { data: string; lastEventId: string }) => void): void;
  close(): void;
}

export interface WatchOptions {
  eventSource: (url: string) => SourceLike;
  fetchJob: (id: string) => Promise<Job>;
  onUpdate: (job: Job) => void;
  onDone: (job: Job) => void;
  sleep?: (ms: number) => Promise<void>;
  pollMs?: number;
}

const SSE_FAILURES_BEFORE_POLLING = 3;
const EVENT_TYPES = ["state", "stage", "progress"];

/**
 * Follows a job over SSE (the browser's EventSource resends Last-Event-ID on reconnect).
 * After 3 stream failures it switches to GET polling. Returns a stop function.
 */
export function watchJob(id: string, opts: WatchOptions): () => void {
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  let stopped = false;
  let failures = 0;
  let polling = false;
  let refreshing: Promise<void> = Promise.resolve();
  const source = opts.eventSource(`/api/v1/jobs/${id}/events`);

  const finish = (job: Job) => {
    if (stopped) return;
    stopped = true;
    source.close();
    opts.onDone(job);
  };

  const refresh = () => {
    refreshing = refreshing.then(async () => {
      if (stopped) return;
      const job = await opts.fetchJob(id);
      opts.onUpdate(job);
      if (TERMINAL_JOB_STATES.includes(job.state)) finish(job);
    });
    return refreshing;
  };

  const poll = async () => {
    polling = true;
    source.close();
    while (!stopped) {
      try {
        await refresh();
      } catch {
        /* keep polling */
      }
      if (!stopped) await sleep(opts.pollMs ?? 2000);
    }
  };

  for (const type of EVENT_TYPES) source.addEventListener(type, () => void refresh().catch(() => {}));
  source.onerror = () => {
    failures += 1;
    if (failures >= SSE_FAILURES_BEFORE_POLLING && !polling) void poll();
  };

  return () => {
    stopped = true;
    source.close();
  };
}
