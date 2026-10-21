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
