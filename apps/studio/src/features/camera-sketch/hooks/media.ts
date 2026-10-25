"use client";

import { useSyncExternalStore } from "react";

const QUERY = "(min-width: 768px)"; // Tailwind `md`

function subscribe(cb: () => void) {
  const m = window.matchMedia(QUERY);
  m.addEventListener("change", cb);
  return () => m.removeEventListener("change", cb);
}

/** True at the `md` breakpoint and up (false during SSR/hydration, then corrected). */
export function useIsDesktop(): boolean {
  return useSyncExternalStore(subscribe, () => window.matchMedia(QUERY).matches, () => false);
}
