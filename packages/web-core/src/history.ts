export const HISTORY_LIMIT = 50;

/** Local config history for undo/redo (persisted revisions live on the server). */
export class History<T> {
  private past: T[] = [];
  private future: T[] = [];

  constructor(private value: T, private limit = HISTORY_LIMIT) {}

  get current(): T {
    return this.value;
  }
  get canUndo(): boolean {
    return this.past.length > 0;
  }
  get canRedo(): boolean {
    return this.future.length > 0;
  }

  push(next: T): void {
    if (JSON.stringify(next) === JSON.stringify(this.value)) return;
    this.past.push(this.value);
    if (this.past.length > this.limit) this.past.shift();
    this.future = [];
    this.value = next;
  }

  /** Replace the current value without creating an undo step (e.g. server echo). */
  reset(value: T): void {
    this.value = value;
  }

  /**
   * Apply `fn` to every stored state (past, current, future) without adding an undo step.
   * For automatic, non-user changes (e.g. linking an uploaded asset) that undo must never revert.
   */
  rebase(fn: (state: T) => T): void {
    this.past = this.past.map(fn);
    this.future = this.future.map(fn);
    this.value = fn(this.value);
  }

  undo(): T {
    const prev = this.past.pop();
    if (prev === undefined) return this.value;
    this.future.push(this.value);
    this.value = prev;
    return prev;
  }

  redo(): T {
    const next = this.future.pop();
    if (next === undefined) return this.value;
    this.past.push(this.value);
    this.value = next;
    return next;
  }
}
