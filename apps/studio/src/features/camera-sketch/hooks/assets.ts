"use client";

import type { Asset } from "@oc/contracts";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

/** Short-lived read URL for a ready asset (server read URLs live 5 minutes). */
export function useAssetUrl(asset: Asset | undefined | null): string | undefined {
  const { data } = useQuery({
    queryKey: ["asset-url", asset?.id],
    queryFn: () => api.getAsset(asset!.id),
    enabled: !!asset && asset.state === "ready",
    staleTime: 4 * 60_000,
    refetchInterval: 4 * 60_000,
  });
  return data?.read_url;
}
