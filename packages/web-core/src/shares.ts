import type { ShareSummary } from "@oc/contracts";

/**
 * Share links are server state (`GET /projects/{id}/shares`): a result view derives "is this output shared" from the
 * list, so a link stays revocable after the view is left. Only the token hash is stored server-side, so the public URL
 * is known only to the view that just created it.
 */
export const sharesQueryKey = (projectId: string) => ["shares", projectId] as const;

/** The active links of one output (the server already drops revoked and expired ones; expiry is rechecked here). */
export function activeSharesFor(shares: readonly ShareSummary[] | undefined, outputAssetId: string, now = Date.now()): ShareSummary[] {
  return (shares ?? []).filter((s) => s.output_asset_id === outputAssetId && Date.parse(s.expires_at) > now);
}

export interface ShareRow {
  share: ShareSummary;
  /** Full public URL, or null when this link was not created by the current view (the server cannot rebuild it). */
  url: string | null;
}

/**
 * What a result view's share panel lists for one output: its active links, each paired with the full URL when this
 * view created it (`createdUrls` maps share id → the relative `url` returned by `POST /projects/{id}/shares`).
 */
export function shareRowsFor(
  shares: readonly ShareSummary[] | undefined,
  outputAssetId: string,
  createdUrls: Readonly<Record<string, string>>,
  origin: string,
  now = Date.now(),
): ShareRow[] {
  return activeSharesFor(shares, outputAssetId, now).map((share) => {
    const path = createdUrls[share.id];
    return { share, url: path ? `${origin}${path}` : null };
  });
}
