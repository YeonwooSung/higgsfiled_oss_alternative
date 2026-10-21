export const AUTOSAVE_DEBOUNCE_MS = 600;

export interface AutosaverOptions<C> {
  save: (expectedRevision: number, config: C) => Promise<{ revision: number; config: C }>;
  revision: number;
  onSaved: (revision: number, config: C) => void;
  onConflict: (error: unknown) => void;
  onError: (error: unknown) => void;
  delayMs?: number;
}

/** Debounced, strictly sequential PATCH of the latest config using optimistic revisions. */
export class Autosaver<C> {
  revision: number;
  private pending: C | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private saving = false;
  private inFlight: Promise<void> | null = null;

  constructor(private opts: AutosaverOptions<C>) {
    this.revision = opts.revision;
  }

  get dirty(): boolean {
    return this.pending !== null || this.saving;
  }

  schedule(config: C): void {
    this.pending = config;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.run(), this.opts.delayMs ?? AUTOSAVE_DEBOUNCE_MS);
  }

  /**
   * Save now: waits for a save already in flight, then sends any pending edit, until nothing is left to save.
   * Afterwards `revision` is the latest saved revision (callers pin jobs to it).
   */
  async flush(): Promise<void> {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    while (this.inFlight || this.pending !== null) {
      if (this.inFlight) await this.inFlight;
      else await this.saveOnce();
    }
  }

  /** Debounce timer: one save, then the next pending edit unless a newer debounce is already scheduled. */
  private async run(): Promise<void> {
    this.timer = null;
    if (this.inFlight) return; // the in-flight save picks the pending edit up when it finishes
    await this.saveOnce();
  }

  private saveOnce(): Promise<void> {
    if (this.pending === null) return Promise.resolve();
    const config = this.pending;
    this.pending = null;
    this.saving = true;
    const p = (async () => {
      let outcome: () => void;
      try {
        const res = await this.opts.save(this.revision, config);
        this.revision = res.revision;
        outcome = () => this.opts.onSaved(res.revision, res.config);
      } catch (e) {
        if ((e as { status?: number }).status === 409) {
          this.pending = null;
          outcome = () => this.opts.onConflict(e);
        } else {
          outcome = () => this.opts.onError(e);
        }
      }
      this.saving = false;
      this.inFlight = null;
      outcome();
    })();
    this.inFlight = p;
    return p.then(() => {
      if (this.pending !== null && !this.timer && !this.inFlight) return this.saveOnce();
    });
  }

  cancel(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.pending = null;
  }
}
