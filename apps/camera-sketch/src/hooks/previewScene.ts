"use client";

import type { Asset, CameraSketchConfig } from "@oc/contracts";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "../api/client";
import { depthBaseQuery, sceneBitmapQuery, type PreviewDeps } from "../flow/previewQueries";
import { intrinsics, type Mat3 } from "../model/camera";
import { chooseStride, edgeRelThreshold, prepareDepthBase, previewMeshForEdit, sampleGrid, type DepthBase, type PreviewMesh } from "../model/mesh";

export interface PreviewScene {
  width: number;
  height: number;
  /** Intrinsics at the (capped) depth size the mesh was unprojected with. */
  K: Mat3;
  stride: number;
  mesh: PreviewMesh;
  targetRaw: number;
}

/** Depth prep in a Web Worker; main-thread fallback when workers are unavailable or fail to start. */
function prepareDepth(png: ArrayBuffer, meta: { min: number; max: number; convention: unknown }): Promise<DepthBase> {
  let worker: Worker;
  try {
    worker = new Worker(new URL("../workers/depthPrep.worker.ts", import.meta.url), { type: "module" });
  } catch {
    return Promise.resolve().then(() => prepareDepthBase(png, meta));
  }
  const copy = png.slice(0); // the transferred buffer is gone if the worker errors and we fall back
  return new Promise<DepthBase>((resolve, reject) => {
    worker.onmessage = (e: MessageEvent<{ ok: true; base: DepthBase } | { ok: false; error: string }>) => {
      worker.terminate();
      if (e.data.ok) resolve(e.data.base);
      else reject(new Error(e.data.error));
    };
    worker.onerror = (e) => {
      e.preventDefault();
      worker.terminate();
      try {
        resolve(prepareDepthBase(copy, meta));
      } catch (err) {
        reject(err);
      }
    };
    worker.postMessage({ png, meta }, [png]);
  });
}

const deps: PreviewDeps = {
  getAsset: (id) => api.getAsset(id),
  fetch: (url) => fetch(url), // signed URL: no cookie needed
  prepareDepth,
  decodeImage: (blob) => createImageBitmap(blob, { premultiplyAlpha: "none" }),
};

/** Scene image as an ImageBitmap, keyed on the asset id (URL refreshes do not reload it). */
export function useSceneBitmap(scene: Asset | undefined) {
  const q = useQuery(sceneBitmapQuery(scene, deps));
  return { bitmap: q.data ?? null, error: q.isError };
}

/**
 * Depth asset -> `DepthBase` once (worker: decode, 2048 px cap, clip, bilateral), the mesh grid
 * samples once per base, then per editor change only the target patch median + grid-sample
 * normalization/contrast + mesh (`previewMeshForEdit`).
 */
export function usePreviewScene(depth: Asset | undefined, config: CameraSketchConfig): { scene: PreviewScene | null; error: boolean } {
  const base = useQuery(depthBaseQuery(depth, deps));
  const grid = useMemo(() => (base.data ? sampleGrid(base.data.z, base.data.width, base.data.height, chooseStride(base.data.width, base.data.height)) : null), [base.data]);
  const { u, v } = config.target;
  const focal = config.camera.focal_length_mm_equiv;
  const sensor = config.camera.sensor_width_mm_assumed;
  const { depth_contrast: contrast, edge_protection: edge } = config.scene;
  const scene = useMemo(() => {
    const b = base.data;
    if (!b || !grid) return null;
    const Ksrc = intrinsics(focal, b.srcWidth, b.srcHeight, sensor);
    const { mesh, targetRaw, K } = previewMeshForEdit(b, grid, [u, v], contrast, Ksrc, edgeRelThreshold(edge));
    return { width: b.width, height: b.height, K, stride: grid.stride, mesh, targetRaw };
  }, [base.data, grid, u, v, focal, sensor, contrast, edge]);
  return { scene, error: base.isError };
}
