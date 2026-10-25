"use client";

import type { Asset } from "@oc/contracts";
import { useQuery, type Query } from "@tanstack/react-query";
import { shellApi } from "./shellApi";

/** Server read URLs live 300 s (`read_url_ttl_s`). Refresh after 4 minutes; never hand one out after 4.5. */
export const ASSET_URL_STALE_MS = 4 * 60_000;
export const ASSET_URL_MAX_AGE_MS = 4.5 * 60_000;

/** Time until the cached URL goes stale, counted from its fetch (a fixed interval would restart on every mount). */
export function assetUrlRefetchDelay(dataUpdatedAt: number, now: number): number | false {
  if (!dataUpdatedAt) return false;
  return Math.max(1_000, dataUpdatedAt + ASSET_URL_STALE_MS - now);
}

/**
 * The URL to render, or undefined. A disabled query still returns its cached data, and a remount returns the cached
 * URL before the refetch: both can be past the signature's lifetime (403). A URL inside the margin is kept while its
 * refresh is in flight, so images and videos do not blank every 4 minutes.
 */
export function usableAssetUrl(enabled: boolean, data: Asset | undefined, dataUpdatedAt: number, now: number): string | undefined {
  return enabled && data && now - dataUpdatedAt < ASSET_URL_MAX_AGE_MS ? data.read_url ?? undefined : undefined;
}

export function assetUrlQuery(asset: Asset | undefined | null) {
  return {
    queryKey: ["asset-url", asset?.id] as const,
    queryFn: () => shellApi.getAsset(asset!.id),
    enabled: !!asset && asset.state === "ready",
    staleTime: ASSET_URL_STALE_MS,
    refetchInterval: (query: Query<Asset, Error, Asset, readonly unknown[]>) => assetUrlRefetchDelay(query.state.dataUpdatedAt, Date.now()),
    refetchIntervalInBackground: true,
  };
}

/** Short-lived read URL for a ready asset, shared by every tool. */
export function useAssetUrl(asset: Asset | undefined | null): string | undefined {
  const options = assetUrlQuery(asset);
  const q = useQuery(options);
  return usableAssetUrl(options.enabled, q.data, q.dataUpdatedAt, Date.now());
}
