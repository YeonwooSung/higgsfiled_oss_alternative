"use client";

import { useQuery } from "@tanstack/react-query";
import { topBarMock } from "../../../shell/topBarMock";
import { api } from "../api/client";

/** Capabilities are anonymous; the Mock badge and the backend reachability both come from them. */
export function useCapabilities() {
  return useQuery({ queryKey: ["caps"], queryFn: () => api.vfxCapabilities(), staleTime: 60_000, retry: 0 });
}

/** Mock flag for the shell top bar. `modelProfile` is the project's profile; without a project the server's default decides. */
export function useVfxMock(modelProfile?: string, upstreamMock?: boolean): boolean {
  const caps = useCapabilities();
  const id = modelProfile ?? caps.data?.default_profile;
  return topBarMock(caps.data?.profiles.find((p) => p.id === id)?.is_mock, upstreamMock);
}
