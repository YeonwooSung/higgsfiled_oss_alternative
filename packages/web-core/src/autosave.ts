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

  constructor(private opts: AutosaverOptions<C>) {
    this.revision = opts.revision;
  }

  get dirty(): boolean {
    return this.pending !== null || this.saving;
  }

  schedule(config: C): void {
    this.pending = config;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.flush(), this.opts.delayMs ?? AUTOSAVE_DEBOUNCE_MS);
  }

  async flush(): Promise<void> {
    this.timer = null;
    if (this.saving || this.pending === null) return;
    const config = this.pending;
    this.pending = null;
    this.saving = true;
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
    outcome();
    if (this.pending !== null && !this.timer) await this.flush();
  }

  cancel(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.pending = null;
  }
}
