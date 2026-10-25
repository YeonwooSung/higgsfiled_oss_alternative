import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import * as THREE from "three";
import { afterAll, describe, expect, it } from "vitest";
import { cameraCenter, coverFit, framingRotation, intrinsics, project, type Mat3, type Mat4, type Vec3 } from "../../src/features/camera-sketch/model/camera";
import { compilePathProvisional, sceneInfoFromTarget, simplifySketch, sketchControlPoints, solveFraming, type PathConfig } from "../../src/features/camera-sketch/model/path";
import { toThreeCamera, toThreePoint } from "../../src/features/camera-sketch/model/three";

/** Python reference output: `uv run python fixtures/camera-sketch/make_parity.py`. */
interface CompileCase {
  id: string;
  scene: { name: string; focal_mm: number; src_w: number; src_h: number; target_uv01: [number, number]; depth_scale: number };
  aspect: string;
  out_w: number;
  out_h: number;
  cfg: PathConfig;
  n_frames: number;
  K_src: Mat3;
  target_world: Vec3;
  K_out: Mat3;
  source_to_output: Record<string, number>;
  status: string;
  reasons: string[];
  suggested_params: Record<string, number> | null;
  params: Record<string, number>;
  control_points: number[][];
  keyframes: { t: number; T_cw: Mat4; desired_uv: number[]; requested_uv: number[] }[];
  diagnostics: { framing_rms_px: number | null; target_in_front_min_z: number | null; target_in_frame_fraction: number | null; framing_clamped: boolean };
  n_samples: number;
  sample_indices: number[];
  samples: { t_ms: number; T_cw: Mat4 }[];
  target_px: [number, number, number][];
}
interface Parity {
  version: number;
  tolerances: { target_px: number; pose: number; simplify: number; solve: number };
  cover_fit: { src: [number, number]; out: [number, number]; scale: number; offset_x: number; offset_y: number }[];
  simplify_sketch: { id: string; points: [number, number, number][]; control_points: number[][]; warning: string | null }[];
  sketch_control_points: { id: string; points: [number, number, number][]; control_points: number[][] | null; warning: string | null; error: boolean }[];
  solve_framing: { id: string; desired: number[][]; framing: number[][]; unclamped: number[][] }[];
  framing_rotation: { c: Vec3; target: Vec3; px: [number, number]; K: Mat3; R: Mat3; wraps: boolean }[];
  compile: CompileCase[];
}

const FIXTURE = fileURLToPath(new URL("../../../../fixtures/camera-sketch/parity.v1.json", import.meta.url));
const P = JSON.parse(readFileSync(FIXTURE, "utf8")) as Parity;
const TOL = P.tolerances;

const maxErr = { target_px: 0, pose: 0, simplify: 0, solve: 0, three_px: 0 };
afterAll(() => console.log("parity max errors", JSON.stringify(maxErr)));

/** max |a - b| over nested numeric arrays (shapes must match). */
function diff(a: unknown, b: unknown): number {
  if (Array.isArray(a) && Array.isArray(b)) {
    expect(a.length).toBe(b.length);
    return a.reduce((m: number, x, i) => Math.max(m, diff(x, b[i])), 0);
  }
  expect(typeof a).toBe("number");
  expect(typeof b).toBe("number");
  return Math.abs((a as number) - (b as number));
}

function within(kind: keyof typeof maxErr, a: unknown, b: unknown, tol: number) {
  const d = diff(a, b);
  maxErr[kind] = Math.max(maxErr[kind], d);
  expect(d).toBeLessThanOrEqual(tol);
}

describe("parity fixture", () => {
  it("is the v1 fixture with both output aspects and a portrait source", () => {
    expect(P.version).toBe(1);
    const ids = P.compile.map((c) => c.id);
    for (const s of ["room", "facade"]) for (const a of ["16x9", "9x16"]) for (const p of ["slide", "push_in", "small_arc"]) expect(ids).toContain(`${s}_${a}_${p}`);
  });

  it("cover-fit matches Python", () => {
    for (const c of P.cover_fit) {
      const f = coverFit(c.src[0], c.src[1], c.out[0], c.out[1]);
      within("pose", [f.scale, f.offset_x, f.offset_y], [c.scale, c.offset_x, c.offset_y], 1e-9);
    }
  });

  it.each(P.sketch_control_points.map((c) => [c.id, c] as const))("sketchControlPoints %s", (_id, c) => {
    if (c.error) {
      expect(() => sketchControlPoints(c.points)).toThrow();
      return;
    }
    const r = sketchControlPoints(c.points);
    expect(r.warning).toBe(c.warning);
    within("simplify", r.controlPoints, c.control_points, TOL.simplify);
  });

  it.each(P.simplify_sketch.map((c) => [c.id, c] as const))("simplifySketch %s", (_id, c) => {
    const r = simplifySketch(c.points);
    expect(r.warning).toBe(c.warning);
    within("simplify", r.controlPoints, c.control_points, TOL.simplify);
  });

  it("framingRotation matches Python, incl. steep targets whose second pitch root is wrapped", () => {
    expect(P.framing_rotation.some((c) => c.wraps)).toBe(true);
    for (const c of P.framing_rotation) within("pose", framingRotation(c.c, c.target, c.px, c.K), c.R, 1e-9);
  });

  it.each(P.solve_framing.map((c) => [c.id, c] as const))("solveFraming %s", (_id, c) => {
    within("solve", solveFraming(c.desired), c.framing, TOL.solve);
    within("solve", solveFraming(c.desired, 0.01, 0.1, -Infinity, Infinity), c.unclamped, TOL.solve);
  });
});

describe.each(P.compile.map((c) => [c.id, c] as const))("compilePathProvisional %s", (_id, c) => {
  const s = c.scene;
  const scene = sceneInfoFromTarget({ focalMm: s.focal_mm, srcW: s.src_w, srcH: s.src_h, u: s.target_uv01[0], v: s.target_uv01[1], depthScale: s.depth_scale });
  const res = compilePathProvisional(c.cfg, scene, c.out_w, c.out_h, { nFrames: c.n_frames });

  it("scene inputs, K_out and cover-fit match", () => {
    within("pose", intrinsics(s.focal_mm, s.src_w, s.src_h), c.K_src, 1e-9);
    within("pose", scene.target_world, c.target_world, 1e-12);
    within("pose", res.K, c.K_out, 1e-9);
    for (const k of ["scale", "offset_x", "offset_y", "out_w", "out_h", "src_w", "src_h"] as const) expect(res.source_to_output[k]).toBeCloseTo(c.source_to_output[k], 9);
  });

  it("status, reasons, params and suggested params match", () => {
    expect(res.status).toBe(c.status);
    expect(res.reasons).toEqual(c.reasons);
    expect(res.params).toEqual(c.params);
    if (c.suggested_params === null) expect(res.suggested_params).toBeNull();
    else {
      expect(Object.keys(res.suggested_params ?? {}).sort()).toEqual(Object.keys(c.suggested_params).sort());
      for (const k of Object.keys(c.suggested_params)) within("pose", res.suggested_params![k], c.suggested_params[k], 1e-12);
    }
    expect(res.diagnostics.framing_clamped).toBe(c.diagnostics.framing_clamped);
    for (const k of ["framing_rms_px", "target_in_front_min_z", "target_in_frame_fraction"] as const) {
      const want = c.diagnostics[k];
      if (want === null) expect(res.diagnostics[k]).toBeNull();
      else expect(Math.abs((res.diagnostics[k] as number) - want)).toBeLessThan(1e-4);
    }
  });

  it("control points and keyframes match", () => {
    within("simplify", res.control_points, c.control_points, TOL.simplify);
    expect(res.keyframes.length).toBe(c.keyframes.length);
    res.keyframes.forEach((k, i) => {
      within("pose", k.t, c.keyframes[i].t, TOL.simplify);
      within("solve", [k.desired_uv, k.requested_uv], [c.keyframes[i].desired_uv, c.keyframes[i].requested_uv], TOL.solve);
      within("pose", k.T_cw, c.keyframes[i].T_cw, TOL.pose);
    });
  });

  it("sampled poses and target projections match (<= 1e-5, <= 2 px)", () => {
    expect(res.samples.length).toBe(c.n_samples);
    c.sample_indices.forEach((idx, j) => {
      const smp = res.samples[idx];
      expect(smp.t_ms).toBeCloseTo(c.samples[j].t_ms, 9);
      within("pose", smp.T_cw, c.samples[j].T_cw, TOL.pose);
      const { uv, z } = project(res.K, smp.T_cw, scene.target_world);
      within("target_px", uv, c.target_px[j].slice(0, 2), TOL.target_px);
      within("pose", z, c.target_px[j][2], TOL.pose);
    });
  });

  it("a THREE.PerspectiveCamera from toThreeCamera projects like project() (<= 0.5 px)", () => {
    const pts: Vec3[] = [scene.target_world, [0.3, -0.2, 2.5], [-0.4, 0.25, 1.4], [0.05, 0.1, 0.7]];
    for (const idx of c.sample_indices) {
      const T = res.samples[idx].T_cw;
      const p = toThreeCamera(T, res.K, c.out_w, c.out_h);
      const cam = new THREE.PerspectiveCamera(p.fov, p.aspect, 0.01, 100);
      cam.position.copy(p.position);
      cam.quaternion.copy(p.quaternion);
      cam.updateMatrixWorld(true);
      const ctr = cameraCenter(T);
      within("pose", toThreePoint(ctr).toArray(), p.position.toArray(), 1e-12);
      for (const X of pts) {
        const { uv } = project(res.K, T, X);
        if (!Number.isFinite(uv[0])) continue;
        const ndc = toThreePoint(X).project(cam);
        const px = [((ndc.x + 1) / 2) * c.out_w, ((1 - ndc.y) / 2) * c.out_h];
        within("three_px", px, uv, 0.5);
      }
    }
  });
});
