import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterAll, describe, expect, it } from "vitest";
import { intrinsics, type Mat3, type Mat4 } from "../src/model/camera";
import {
  DAV2_SMALL,
  MAX_PREVIEW_TRIANGLES,
  STRETCH_VALID,
  bilateralFilter32f,
  buildPreviewMesh,
  chooseStride,
  decodeDepthPng16,
  depthToZ,
  edgeFaceStretch,
  edgeRelThreshold,
  isStretchValid,
  prepareZ,
} from "../src/model/mesh";

type Grid = (number | null)[][];
interface MeshCase {
  id: string;
  z: Grid;
  K: Mat3;
  stride: number;
  edge_rel_threshold: number;
  grid_shape: [number, number];
  n_vertices: number;
  vertices: number[][];
  uv: number[][];
  n_faces: number;
  n_edge_faces: number;
  faces: number[][];
  edge_faces: number[][];
  stretch_pose: Mat4;
  stretch: (number | null)[];
  stretch_valid_max: number;
}
interface RoomCounts { grid_shape: [number, number]; n_faces: number; n_edge_faces: number }
interface Fixture {
  prepare_z: {
    synthetic: { z: Grid; target_uv01: [number, number]; plain: { z_norm: Grid; target_raw: number }; smooth: { z_norm: Grid; target_raw: number } };
    room: { target_uv01: [number, number]; rows: number[]; cols: number[]; plain: { target_raw: number; z_norm: Grid }; smooth: { target_raw: number; z_norm: Grid } };
  };
  mesh: { grid: MeshCase[]; room: { focal_mm: number; stride: number; edge_rel_threshold: number; plain: RoomCounts; smooth: RoomCounts } };
}

const FIX_DIR = new URL("../../../fixtures/camera-sketch/", import.meta.url);
const P = JSON.parse(readFileSync(fileURLToPath(new URL("parity.v1.json", FIX_DIR)), "utf8")) as Fixture;
const scenes = JSON.parse(readFileSync(fileURLToPath(new URL("scenes.json", FIX_DIR)), "utf8"));

const flat = (g: Grid) => Float32Array.from(g.flat().map((v) => (v === null ? NaN : v)));
const room = (() => {
  const meta = scenes.scenes.room;
  const d = decodeDepthPng16(readFileSync(fileURLToPath(new URL(meta.depth, FIX_DIR))), meta.depth_png16);
  return { ...d, z: depthToZ(d.data, DAV2_SMALL) };
})();

/** Max |a-b| / max(1, |b|) over finite pairs; NaN must match NaN. */
function relErr(a: ArrayLike<number>, b: ArrayLike<number>): number {
  expect(a.length).toBe(b.length);
  let m = 0;
  for (let i = 0; i < a.length; i++) {
    if (Number.isNaN(b[i])) {
      expect(Number.isNaN(a[i])).toBe(true);
      continue;
    }
    m = Math.max(m, Math.abs(a[i] - b[i]) / Math.max(1, Math.abs(b[i])));
  }
  return m;
}
const measured: Record<string, number> = {};
afterAll(() => console.log("prepare_z / mesh preview deviation vs Python", JSON.stringify(measured)));

describe("decodeDepthPng16", () => {
  it("decodes the 16-bit room depth to float32 min..max", () => {
    expect([room.width, room.height]).toEqual([960, 540]);
    const meta = scenes.scenes.room.depth_png16;
    let lo = Infinity, hi = -Infinity;
    for (const v of room.data) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    expect(lo).toBeCloseTo(meta.min, 6);
    expect(hi).toBeCloseTo(meta.max, 6);
  });
  it("rejects a non-16-bit or colour png", () => {
    const rgb = readFileSync(fileURLToPath(new URL(scenes.scenes.room.image, FIX_DIR)));
    expect(() => decodeDepthPng16(rgb, { min: 0, max: 1 })).toThrow(/16-bit grayscale/);
  });
});

describe("prepareZ (port of scene.prepare_z)", () => {
  const s = P.prepare_z.synthetic;
  const z = flat(s.z);
  it("matches Python exactly without smoothing (synthetic: outlier, NaN, z<=0, far)", () => {
    const r = prepareZ(z, 20, 12, s.target_uv01, { smooth: false });
    expect(r.targetRaw).toBeCloseTo(s.plain.target_raw, 6);
    const e = relErr(r.z, flat(s.plain.z_norm));
    measured.synthetic_plain = e;
    expect(e).toBeLessThan(1e-6);
  });
  it("matches Python's cv2.bilateralFilter smoothing closely (synthetic)", () => {
    const r = prepareZ(z, 20, 12, s.target_uv01, { smooth: true });
    expect(Math.abs(r.targetRaw - s.smooth.target_raw) / s.smooth.target_raw).toBeLessThan(1e-5);
    const e = relErr(r.z, flat(s.smooth.z_norm));
    measured.synthetic_smooth = e;
    expect(e).toBeLessThan(1e-5);
  });
  for (const smooth of [false, true]) {
    it(`matches Python on the room depth (smooth=${smooth})`, () => {
      const ref = P.prepare_z.room[smooth ? "smooth" : "plain"];
      const r = prepareZ(room.z, room.width, room.height, P.prepare_z.room.target_uv01, { smooth });
      const tRel = Math.abs(r.targetRaw - ref.target_raw) / ref.target_raw;
      const got = P.prepare_z.room.rows.flatMap((y) => P.prepare_z.room.cols.map((x) => r.z[y * room.width + x]));
      const e = relErr(got, flat(ref.z_norm));
      measured[`room_${smooth ? "smooth" : "plain"}_target_rel`] = tRel;
      measured[`room_${smooth ? "smooth" : "plain"}_znorm_rel`] = e;
      expect(tRel).toBeLessThan(smooth ? 1e-5 : 1e-6);
      expect(e).toBeLessThan(smooth ? 1e-5 : 1e-6);
    });
  }
  it("raises on a map with no finite positive values", () => {
    expect(() => prepareZ(Float32Array.from([NaN, -1, 0, Infinity]), 2, 2, [0.5, 0.5])).toThrow(/no finite/);
  });
  it("keeps a constant map planar at z = 1 without dividing by zero", () => {
    const r = prepareZ(new Float32Array(64).fill(3), 8, 8, [0.5, 0.5]);
    expect(Array.from(r.z).every((v) => v === 1)).toBe(true);
  });
});

describe("bilateralFilter32f", () => {
  it("returns a copy for a constant image and preserves a step edge", () => {
    expect(Array.from(bilateralFilter32f(new Float32Array(9).fill(2), 3, 3, 5, 0.1, 3))).toEqual(new Array(9).fill(2));
    const step = Float32Array.from({ length: 64 }, (_, i) => (i % 8 < 4 ? 0 : 1));
    const out = bilateralFilter32f(step, 8, 8, 5, 0.1, 3);
    expect(Math.max(...Array.from(out).map((v, i) => Math.abs(v - step[i])))).toBeLessThan(1e-6);
  });
});

describe("buildPreviewMesh (port of scene.build_mesh)", () => {
  for (const c of P.mesh.grid) {
    it(`${c.id}: same grid, vertices, uv, regular faces and the exact edge-face set`, () => {
      const [gh, gw] = c.grid_shape;
      const h = c.z.length, w = c.z[0].length;
      const m = buildPreviewMesh(flat(c.z), w, h, c.K, c.stride, c.edge_rel_threshold);
      expect([m.gridH, m.gridW]).toEqual([gh, gw]);
      expect(m.positions.length / 3).toBe(c.n_vertices);
      expect(m.index.length / 3).toBe(c.n_faces);
      expect(m.edgeIndex.length / 3).toBe(c.n_edge_faces);
      expect(Array.from(m.index)).toEqual(c.faces.flat());
      expect(Array.from(m.edgeIndex)).toEqual(c.edge_faces.flat());
      expect(relErr(m.positions, c.vertices.flat())).toBeLessThan(1e-6);
      expect(relErr(m.uvs, c.uv.flat())).toBe(0);
    });
    it(`${c.id}: edge-face stretch equals render.rasterize's A / A_ref`, () => {
      const h = c.z.length, w = c.z[0].length;
      const m = buildPreviewMesh(flat(c.z), w, h, c.K, c.stride, c.edge_rel_threshold);
      const s = edgeFaceStretch(m.positions, m.edgeIndex, c.stretch_pose, c.K);
      expect(relErr(s, c.stretch.map((v) => (v === null ? NaN : v)))).toBeLessThan(1e-4);
      expect(c.stretch_valid_max).toBe(STRETCH_VALID);
      const valid = c.stretch.map((v) => v !== null && v > 0 && v <= STRETCH_VALID);
      expect(Array.from(s).map(isStretchValid)).toEqual(valid);
    });
  }
  it("has both valid and invalid edge faces in the stretch fixture", () => {
    const all = P.mesh.grid.flatMap((c) => c.stretch);
    expect(all.some((v) => v !== null && v > 0 && v <= 1.5)).toBe(true);
    expect(all.some((v) => v !== null && v > 1.5)).toBe(true);
    expect(all.some((v) => v !== null && v <= 0)).toBe(true);
  });
  for (const smooth of [false, true]) {
    it(`room depth at the preview stride: face counts vs Python (smooth=${smooth})`, () => {
      const R = P.mesh.room;
      const ref = R[smooth ? "smooth" : "plain"];
      const zn = prepareZ(room.z, room.width, room.height, P.prepare_z.room.target_uv01, { smooth }).z;
      const K = intrinsics(R.focal_mm, room.width, room.height);
      const m = buildPreviewMesh(zn, room.width, room.height, K, R.stride, R.edge_rel_threshold);
      expect([m.gridH, m.gridW]).toEqual(ref.grid_shape);
      const dF = Math.abs(m.index.length / 3 - ref.n_faces), dE = Math.abs(m.edgeIndex.length / 3 - ref.n_edge_faces);
      measured[`room_mesh_${smooth ? "smooth" : "plain"}_face_diff`] = dF;
      measured[`room_mesh_${smooth ? "smooth" : "plain"}_edge_diff`] = dE;
      if (!smooth) expect([dF, dE]).toEqual([0, 0]);
      else expect(dE).toBeLessThanOrEqual(Math.ceil(0.02 * ref.n_edge_faces));
    });
  }
  it("rejects stride < 1", () => {
    expect(() => buildPreviewMesh(new Float32Array(4), 2, 2, [[1, 0, 1], [0, 1, 1], [0, 0, 1]], 0)).toThrow(/stride/);
  });
});

describe("stretch validity", () => {
  it("is valid only in (0, 1.5]", () => {
    expect([1, 1.5, 0.01].map(isStretchValid)).toEqual([true, true, true]);
    expect([1.5000001, 0, -1, NaN, Infinity].map(isStretchValid)).toEqual([false, false, false, false, false]);
  });
  it("is 1 for every edge face at the identity pose", () => {
    const c = P.mesh.grid[0];
    const m = buildPreviewMesh(flat(c.z), 16, 9, c.K, 1, 0.08);
    const I: Mat4 = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
    for (const v of edgeFaceStretch(m.positions, m.edgeIndex, I, c.K)) expect(v).toBeCloseTo(1, 6);
  });
});

describe("chooseStride / edgeRelThreshold", () => {
  it("keeps regular triangles <= 60k with the smallest stride", () => {
    expect(chooseStride(960, 540)).toBe(5);
    for (const [w, h] of [[1280, 720], [720, 1280], [4000, 3000], [64, 64], [1, 1]]) {
      const s = chooseStride(w, h);
      const tris = (s: number) => 2 * Math.ceil(w / s) * Math.ceil(h / s);
      expect(tris(s)).toBeLessThanOrEqual(MAX_PREVIEW_TRIANGLES);
      if (s > 1) expect(tris(s - 1)).toBeGreaterThan(MAX_PREVIEW_TRIANGLES);
    }
  });
  it("maps edge protection like scene_load (0.5 -> 0.08)", () => {
    expect(edgeRelThreshold(0.5)).toBeCloseTo(0.08, 12);
    expect(edgeRelThreshold(0)).toBeCloseTo(0.12, 12);
  });
});
