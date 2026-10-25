"use client";

/**
 * Pre-render checks of the saved revision (`POST /projects/{id}/vfx/validate`): run 300 ms after each autosave and
 * again when the server state the checks read changes (a track or plate job finished, an upload was inspected). Only
 * the newest request may report: a late answer for an older revision is dropped.
 */
import type { PocketVfxProject, VfxValidateResponse, Job } from "@oc/contracts";
import { useEffect } from "react";
import { create } from "zustand";
import { api } from "../api/client";
import { useEditor } from "../state/editorStore";

export const VALIDATE_DEBOUNCE_MS = 300;

export interface Validation {
  revision: number;
  /** revision + server state it was checked against (`validationKey`) */
  key: string;
  result: VfxValidateResponse | null;
  /** the call failed (network / server error) */
  error: boolean;
}

/** Debounced validate calls; `onResult` only ever sees the answer of the newest request. */
export class ValidateLoop {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private seq = 0;

  constructor(
    private readonly call: (revision: number) => Promise<VfxValidateResponse>,
    private readonly onResult: (v: Validation) => void,
    private readonly delayMs = VALIDATE_DEBOUNCE_MS,
  ) {}

  request(revision: number, key: string): void {
    if (this.timer) clearTimeout(this.timer);
    const seq = ++this.seq;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.call(revision).then(
        (result) => { if (seq === this.seq) this.onResult({ revision, key, result, error: false }); },
        () => { if (seq === this.seq) this.onResult({ revision, key, result: null, error: true }); },
      );
    }, this.delayMs);
  }

  cancel(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.seq++;
  }
}

/** The validation when it was made for `key`, else null (still checking). */
export function currentValidation(v: Validation | null, key: string): Validation | null {
  return v && v.key === key ? v : null;
}

/** Server state the checks read besides the revision: finished jobs (tracks, plates) and ready assets. */
export function validationKey(revision: number, project: PocketVfxProject | null, jobs: Record<string, Job>): string {
  const done = Object.values(jobs).filter((j) => j.state === "succeeded").map((j) => j.id).sort().join(",");
  const ready = (project?.assets ?? []).filter((a) => a.state === "ready").length;
  return `${revision}|${done}|${ready}`;
}

export const useValidation = create<{ last: Validation | null }>(() => ({ last: null }));

let loop: ValidateLoop | null = null;

/** Re-check now (after a failed call, or after a job was refused with validation errors). */
export function revalidate(): void {
  const s = useEditor.getState();
  if (loop && s.revision > 0) loop.request(s.revision, validationKey(s.revision, s.project, s.jobs));
}

/** Mount once in the editor: validates every saved revision. */
export function useValidateLoop(projectId: string): void {
  const revision = useEditor((s) => s.revision);
  const saved = useEditor((s) => s.saveState === "saved");
  const key = useEditor((s) => validationKey(s.revision, s.project, s.jobs));
  useEffect(() => {
    const own = new ValidateLoop((rev) => api.validate(projectId, rev), (v) => useValidation.setState({ last: v }));
    loop = own;
    useValidation.setState({ last: null });
    return () => {
      own.cancel();
      if (loop === own) loop = null;
    };
  }, [projectId]);
  useEffect(() => {
    if (saved && revision > 0) loop?.request(revision, key);
  }, [revision, saved, key]);
}

/** The validation of the current saved revision, or null while it is being checked. */
export function useCurrentValidation(): Validation | null {
  const last = useValidation((s) => s.last);
  const key = useEditor((s) => validationKey(s.revision, s.project, s.jobs));
  return currentValidation(last, key);
}
