"use client";

import type { Asset, CameraSketchConfig } from "@oc/contracts";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "../api/client";
import { intrinsics, type Mat3 } from "../model/camera";
import {
  applyDepthContrast,
  buildPreviewMesh,
  chooseStride,
  clipSmoothZ,
  conventionFromMeta,
  decodeDepthPng16,
  depthToZ,
  edgeRelThreshold,
  normalizeZ,
  type PreviewMesh,
} from "../model/mesh";

export interface PreviewScene {
  width: number;
  height: number;
  /** Source intrinsics the mesh was unprojected with. */
  K: Mat3;
  stride: number;
  mesh: PreviewMesh;
  targetRaw: number;
}

/**
 * Depth asset -> clipped + smoothed z (once per depth asset; the bilateral pass is the slow part),
 * then per editor change: normalize to the target, depth contrast, preview mesh at the stride that
 * keeps <= 60k regular triangles. Mirrors `scene_load.load_scene_info` at full depth resolution.
 */
export function usePreviewScene(depth: Asset | undefined, config: CameraSketchConfig): { scene: PreviewScene | null; loading: boolean; error: boolean } {
  const base = useQuery({
    queryKey: ["depth-z", depth?.id],
    enabled: !!depth && depth.state === "ready",
    staleTime: Infinity,
    gcTime: 60_000,
    retry: 1,
    queryFn: async () => {
      const a = await api.getAsset(depth!.id);
      if (!a.read_url) throw new Error("depth has no read url");
      const res = await fetch(a.read_url); // signed URL: no cookie needed (also works cross-origin)
      if (!res.ok) throw new Error(`depth fetch ${res.status}`);
      const meta = a.meta as { min?: unknown; max?: unknown; convention?: unknown };
      const d = decodeDepthPng16(await res.arrayBuffer(), { min: Number(meta.min), max: Number(meta.max) });
      const z = clipSmoothZ(depthToZ(d.data, conventionFromMeta(meta.convention)), d.width, d.height);
      return { z, width: d.width, height: d.height };
    },
  });
  const { u, v } = config.target;
  const focal = config.camera.focal_length_mm_equiv;
  const sensor = config.camera.sensor_width_mm_assumed;
  const { depth_contrast: contrast, edge_protection: edge } = config.scene;
  const scene = useMemo(() => {
    const b = base.data;
    if (!b) return null;
    const { z, targetRaw } = normalizeZ(b.z, b.width, b.height, [u, v]);
    const K = intrinsics(focal, b.width, b.height, sensor);
    const stride = chooseStride(b.width, b.height);
    const mesh = buildPreviewMesh(applyDepthContrast(z, contrast), b.width, b.height, K, stride, edgeRelThreshold(edge));
    return { width: b.width, height: b.height, K, stride, mesh, targetRaw };
  }, [base.data, u, v, focal, sensor, contrast, edge]);
  return { scene, loading: base.isPending && !!depth, error: base.isError };
}
