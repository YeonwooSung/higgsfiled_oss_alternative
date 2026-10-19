import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { Asset } from "@oc/contracts";
import { encode } from "fast-png";
import { describe, expect, it, vi } from "vitest";
import { depthBaseKey, depthBaseQuery, sceneBitmapQuery, type PreviewDeps } from "../src/flow/previewQueries";
import { intrinsics } from "../src/model/camera";
import {
  DAV2_SMALL, MAX_DEPTH_SIDE, applyDepthContrast, buildPreviewMesh, cappedSize, chooseStride, clipSmoothZ, decodeDepthPng16, depthToZ,
  normalizeZ, prepareDepthBase, previewMeshForEdit, resizeArea, sampleGrid, type DepthBase,
} from "../src/model/mesh";

const FIX_DIR = new URL("../../../fixtures/camera-sketch/", import.meta.url);
const scenes = JSON.parse(readFileSync(fileURLToPath(new URL("scenes.json", FIX_DIR)), "utf8"));
const roomMeta = scenes.scenes.room;
const roomPng = readFileSync(fileURLToPath(new URL(roomMeta.depth, FIX_DIR)));

const asset = (id: string, url: string): Asset => ({ id, state: "ready", read_url: url, meta: { min: 0, max: 1, convention: { kind: "inverse_relative" } } }) as unknown as Asset;

describe("preview queries are keyed on the asset id, not the signed URL", () => {
  it("a refreshed read URL keeps the same key; the fetcher reads the URL fresh", async () => {
    expect(depthBaseQuery(asset("d1", "/u?sig=1"), {} as PreviewDeps).queryKey).toEqual(depthBaseQuery(asset("d1", "/u?sig=2"), {} as PreviewDeps).queryKey);
    expect(depthBaseKey("d1")).toEqual(["depth-base", "d1"]);
    expect(sceneBitmapQuery(asset("s1", "/a"), {} as PreviewDeps).queryKey).toEqual(["scene-bitmap", "s1"]);
    const deps: PreviewDeps = {
      getAsset: vi.fn(async (id: string) => asset(id, "/fresh-url")),
      fetch: vi.fn(async () => new Response(new Uint8Array([1, 2, 3]))),
      prepareDepth: vi.fn(async () => ({}) as DepthBase),
      decodeImage: vi.fn(async () => ({}) as ImageBitmap),
    };
    await depthBaseQuery(asset("d1", "/stale-url"), deps).queryFn();
    expect(deps.getAsset).toHaveBeenCalledWith("d1");
    expect(deps.fetch).toHaveBeenCalledWith("/fresh-url");
    expect(deps.prepareDepth).toHaveBeenCalledWith(expect.any(ArrayBuffer), { min: 0, max: 1, convention: { kind: "inverse_relative" } });
    await sceneBitmapQuery(asset("s1", "/stale"), deps).queryFn();
    expect(deps.decodeImage).toHaveBeenCalledTimes(1);
  });
  it("is disabled until the asset is ready and surfaces fetch errors", async () => {
    expect(depthBaseQuery(undefined, {} as PreviewDeps).enabled).toBe(false);
    expect(depthBaseQuery({ ...asset("d", "/x"), state: "uploaded" }, {} as PreviewDeps).enabled).toBe(false);
    const deps = { getAsset: async () => asset("d", "/x"), fetch: async () => new Response("", { status: 403 }) } as unknown as PreviewDeps;
    await expect(sceneBitmapQuery(asset("d", "/x"), deps).queryFn()).rejects.toThrow(/403/);
  });
});

describe("2048 px depth cap (area downsample before clip/smooth)", () => {
  it("caps the long side like scene_load (half-to-even rounding) and leaves small maps alone", () => {
    expect(cappedSize(960, 540)).toEqual([960, 540]);
    expect(cappedSize(4000, 3000)).toEqual([2048, 1536]);
    expect(cappedSize(3000, 4000)).toEqual([1536, 2048]);
    expect(cappedSize(4096, 2304)).toEqual([2048, 1152]);
    expect(Math.max(...cappedSize(5000, 17))).toBe(MAX_DEPTH_SIDE);
  });
  it("box-averages for integer factors, preserves the mean, propagates NaN", () => {
    const src = Float32Array.from({ length: 16 }, (_, i) => i);
    expect(Array.from(resizeArea(src, 4, 4, 2, 2))).toEqual([2.5, 4.5, 10.5, 12.5]);
    const r = Float32Array.from({ length: 7 * 5 }, (_, i) => Math.sin(i) + 2);
    const out = resizeArea(r, 7, 5, 3, 2);
    const mean = (a: Float32Array) => a.reduce((s, v) => s + v, 0) / a.length;
    expect(mean(out)).toBeCloseTo(mean(r), 5);
    src[0] = NaN;
    expect(Number.isNaN(resizeArea(src, 4, 4, 2, 2)[0])).toBe(true);
  });
  it("prepareDepthBase downsamples a 4000x3000 PNG16 to 2048x1536 before smoothing", () => {
    const w = 4000, h = 3000;
    const q = new Uint16Array(w * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) q[y * w + x] = (x > 1500 && x < 2500 && y > 1000 && y < 2000) ? 60000 : 10000 + ((x + y) % 500);
    const png = encode({ width: w, height: h, data: q, depth: 16, channels: 1 });
    const base = prepareDepthBase(png, { min: 0.2, max: 0.9, convention: { kind: "inverse_relative" } });
    expect([base.width, base.height, base.srcWidth, base.srcHeight]).toEqual([2048, 1536, 4000, 3000]);
    expect(base.z.length).toBe(2048 * 1536);
    expect(base.z.every((v) => v > 0)).toBe(true);
  });
  it("equals the uncapped pipeline for maps within the cap (room)", () => {
    const base = prepareDepthBase(roomPng, { ...roomMeta.depth_png16, convention: { kind: "inverse_relative" } });
    const d = decodeDepthPng16(roomPng, roomMeta.depth_png16);
    expect(Array.from(base.z)).toEqual(Array.from(clipSmoothZ(depthToZ(d.data, DAV2_SMALL), d.width, d.height)));
  });
});

describe("per-edit work touches only the grid samples (+ target patch)", () => {
  const base = prepareDepthBase(roomPng, { ...roomMeta.depth_png16, convention: { kind: "inverse_relative" } });
  const stride = chooseStride(base.width, base.height);
  const grid = sampleGrid(base.z, base.width, base.height, stride);
  const K = intrinsics(50, 960, 540);
  const uv = roomMeta.target_uv01 as [number, number];

  it("takes (base, grid, target, contrast, K, threshold) and the grid holds only grid samples", () => {
    expect(previewMeshForEdit.length).toBe(6);
    expect(grid.zg.length).toBe(Math.ceil(960 / stride + 1) * Math.ceil(540 / stride + 1));
    expect(grid.zg.length).toBeLessThan(base.z.length / 20);
  });
  it("is exact against normalize -> contrast -> build_mesh on the full map", () => {
    for (const contrast of [1, 1.3]) {
      const full = normalizeZ(base.z, base.width, base.height, uv);
      const ref = buildPreviewMesh(applyDepthContrast(full.z, contrast), base.width, base.height, K, stride, 0.1);
      const got = previewMeshForEdit(base, grid, uv, contrast, K, 0.1);
      expect(got.targetRaw).toBe(full.targetRaw);
      expect(Array.from(got.mesh.positions)).toEqual(Array.from(ref.positions));
      expect(Array.from(got.mesh.index)).toEqual(Array.from(ref.index));
      expect(Array.from(got.mesh.edgeIndex)).toEqual(Array.from(ref.edgeIndex));
    }
  });
  it("does not read the full map outside the target patch", () => {
    const r = Math.round(0.02 * 540);
    const cx = Math.floor(uv[0] * 960), cy = Math.floor(uv[1] * 540);
    const masked = new Float32Array(base.z.length).fill(NaN);
    for (let y = cy - r; y <= cy + r; y++) for (let x = cx - r; x <= cx + r; x++) masked[y * 960 + x] = base.z[y * 960 + x];
    const a = previewMeshForEdit({ ...base, z: masked }, grid, uv, 1, K, 0.08);
    const b = previewMeshForEdit(base, grid, uv, 1, K, 0.08);
    expect(Array.from(a.mesh.positions)).toEqual(Array.from(b.mesh.positions));
  });
  it("scales K to the capped size like load_scene_info(max_side)", () => {
    const capped: DepthBase = { ...base, srcWidth: 1920, srcHeight: 1080 };
    const Ks = intrinsics(50, 1920, 1080);
    const { K: Kc } = previewMeshForEdit(capped, grid, uv, 1, Ks, 0.08);
    expect(Kc[0][0]).toBeCloseTo(Ks[0][0] / 2, 12);
    expect(Kc[1][2]).toBeCloseTo(270, 12);
  });
});
