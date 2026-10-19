/**
 * Query definitions for the preview's heavy resources, keyed on the ASSET ID only. Signed read URLs
 * expire and are refreshed every few minutes; the URL is read inside the fetcher, so a refresh never
 * changes the key and never re-downloads, re-decodes or rebuilds the GPU mesh.
 */
import type { Asset } from "@oc/contracts";
import type { DepthBase } from "../model/mesh";

export interface PreviewDeps {
  getAsset: (id: string) => Promise<Asset>;
  fetch: (url: string) => Promise<Response>;
  /** PNG16 bytes -> DepthBase (Web Worker, or main thread as a fallback). */
  prepareDepth: (png: ArrayBuffer, meta: { min: number; max: number; convention: unknown }) => Promise<DepthBase>;
  decodeImage: (blob: Blob) => Promise<ImageBitmap>;
}

async function fetchAsset(deps: PreviewDeps, id: string): Promise<{ asset: Asset; res: Response }> {
  const asset = await deps.getAsset(id); // fresh signed URL on every (re)fetch
  if (!asset.read_url) throw new Error("asset has no read url");
  const res = await deps.fetch(asset.read_url);
  if (!res.ok) throw new Error(`asset fetch ${res.status}`);
  return { asset, res };
}

export const depthBaseKey = (id: string | undefined) => ["depth-base", id] as const;
export const sceneBitmapKey = (id: string | undefined) => ["scene-bitmap", id] as const;

export function depthBaseQuery(asset: Asset | undefined, deps: PreviewDeps) {
  return {
    queryKey: depthBaseKey(asset?.id),
    enabled: !!asset && asset.state === "ready",
    staleTime: Infinity,
    gcTime: 0, // large typed arrays: free as soon as no stage uses them
    retry: 1,
    queryFn: async (): Promise<DepthBase> => {
      const { asset: a, res } = await fetchAsset(deps, asset!.id);
      const meta = a.meta as { min?: unknown; max?: unknown; convention?: unknown };
      return deps.prepareDepth(await res.arrayBuffer(), { min: Number(meta.min), max: Number(meta.max), convention: meta.convention });
    },
  };
}

export function sceneBitmapQuery(asset: Asset | undefined, deps: PreviewDeps) {
  return {
    queryKey: sceneBitmapKey(asset?.id),
    enabled: !!asset && asset.state === "ready",
    staleTime: Infinity,
    gcTime: 0,
    retry: 1,
    queryFn: async (): Promise<ImageBitmap> => {
      const { res } = await fetchAsset(deps, asset!.id);
      return deps.decodeImage(await res.blob());
    },
  };
}
