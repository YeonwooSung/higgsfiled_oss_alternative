import type { Asset } from "@oc/contracts";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import {
  ASSET_URL_MAX_AGE_MS, ASSET_URL_STALE_MS, assetUrlQuery, assetUrlRefetchDelay, usableAssetUrl,
} from "../../src/shell/assetUrl";

const ready = { id: "a1", state: "ready" } as Asset;
const signed = { read_url: "https://x/signed" } as Asset;

describe("asset read URLs never outlive the server's 300 s signature", () => {
  it("keeps a margin below the server TTL", () => {
    expect(ASSET_URL_STALE_MS).toBeLessThan(ASSET_URL_MAX_AGE_MS);
    expect(ASSET_URL_MAX_AGE_MS).toBeLessThan(300_000);
  });

  it("hands out a URL only while it is young enough", () => {
    const now = 1_000_000;
    expect(usableAssetUrl(true, signed, now - 60_000, now)).toBe("https://x/signed");
    // a background refresh is in flight: the old URL is still valid for a while, keep it (no flicker)
    expect(usableAssetUrl(true, signed, now - ASSET_URL_STALE_MS - 1, now)).toBe("https://x/signed");
    expect(usableAssetUrl(true, signed, now - ASSET_URL_MAX_AGE_MS, now)).toBeUndefined();
    expect(usableAssetUrl(true, undefined, now, now)).toBeUndefined();
  });

  it("returns nothing for a disabled query even if the cache still holds an old URL", () => {
    expect(usableAssetUrl(false, signed, 1_000, 1_000)).toBeUndefined();
  });

  it("refreshes when the URL goes stale, counted from when it was fetched (not from mount)", () => {
    const now = 1_000_000;
    expect(assetUrlRefetchDelay(0, now)).toBe(false);
    expect(assetUrlRefetchDelay(now - 60_000, now)).toBe(ASSET_URL_STALE_MS - 60_000);
    expect(assetUrlRefetchDelay(now - 10 * 60_000, now)).toBe(1_000);
  });

  it("a remount over a cached URL from ~9 minutes ago renders no URL until the refetch", () => {
    const qc = new QueryClient();
    const fetchedAt = Date.now() - 9 * 60_000;
    qc.setQueryData(["asset-url", "a1"], signed, { updatedAt: fetchedAt });
    const result = new QueryObserver(qc, assetUrlQuery(ready)).getCurrentResult();
    expect(result.data).toBe(signed); // what the old hook returned -> 403
    expect(usableAssetUrl(true, result.data, result.dataUpdatedAt, Date.now())).toBeUndefined();
    expect(result.isStale).toBe(true); // so the mount refetches it
  });

  it("is disabled until the asset is ready", () => {
    expect(assetUrlQuery({ id: "a1", state: "uploaded" } as Asset).enabled).toBe(false);
    expect(assetUrlQuery(undefined).enabled).toBe(false);
    expect(assetUrlQuery(ready).enabled).toBe(true);
    expect(assetUrlQuery(ready).queryKey).toEqual(["asset-url", "a1"]);
  });
});
