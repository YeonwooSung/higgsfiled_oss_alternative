/** Persisted collapsed state of the desktop sidebar. Any storage failure means "expanded" (spec §4.1). */
export const COLLAPSED_KEY = "oc.sidebar.collapsed";

export function readCollapsed(storage: Pick<Storage, "getItem"> | null): boolean {
  try {
    return storage?.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeCollapsed(storage: Pick<Storage, "setItem"> | null, v: boolean): void {
  try {
    storage?.setItem(COLLAPSED_KEY, v ? "1" : "0");
  } catch {
    // Private mode / blocked storage: the choice just is not remembered.
  }
}

/** `window.localStorage`, or null where it is missing or its accessor throws. */
export function browserStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}
