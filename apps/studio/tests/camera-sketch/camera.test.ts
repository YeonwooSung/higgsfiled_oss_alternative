import { describe, expect, it } from "vitest";
import {
  cameraCenter,
  coverFit,
  ease,
  floorMod,
  framingRotation,
  interpolate,
  intrinsics,
  matMul,
  outputIntrinsics,
  pose,
  project,
  quatToRot,
  rotToQuat,
  rotX,
  rotY,
  slerp,
  transpose3,
  wrapAngle,
  type Mat3,
  type Mat4,
} from "../../src/features/camera-sketch/model/camera";
import { toThreeCamera } from "../../src/features/camera-sketch/model/three";

const close = (a: number[] | number[][], b: number[] | number[][], tol = 1e-12) =>
  expect(Math.max(...a.flat().map((x, i) => Math.abs(x - (b.flat()[i] as number))))).toBeLessThan(tol);
const I4: Mat4 = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];

describe("intrinsics / cover-fit", () => {
  it("uses fx = fy = focal/36 * width and a centred principal point", () => {
    expect(intrinsics(36, 1000, 500)).toEqual([[1000, 0, 500], [0, 1000, 250], [0, 0, 1]]);
  });
  it("covers a landscape output from a portrait source by cropping top and bottom", () => {
    const f = coverFit(576, 1024, 1280, 720);
    expect(f.scale).toBeCloseTo(1280 / 576, 12);
    expect(f.offset_x).toBeCloseTo(0, 12);
    expect(f.offset_y).toBeLessThan(0);
    // the identity pose renders the cover-fit crop: source centre -> output centre
    const K = outputIntrinsics(intrinsics(50, 576, 1024), f);
    expect(K[0][2]).toBeCloseTo(640, 9);
    expect(K[1][2]).toBeCloseTo(360, 9);
    expect(() => coverFit(0, 1, 1, 1)).toThrow();
  });
});

describe("project", () => {
  it("returns NaN uv for points at or behind the camera", () => {
    const K = intrinsics(50, 1280, 720);
    expect(project(K, I4, [0, 0, 2]).uv).toEqual([640, 360]);
    const back = project(K, I4, [0, 0, -1]);
    expect(Number.isNaN(back.uv[0])).toBe(true);
    expect(back.z).toBe(-1);
    expect(Number.isNaN(project(K, I4, [1, 0, 0]).uv[1])).toBe(true);
  });
});

describe("angle wrap", () => {
  it("uses Python floor-mod semantics (JS % keeps the dividend's sign)", () => {
    expect(floorMod(-1, 3)).toBe(2);
    expect(-1 % 3).toBe(-1);
    expect(wrapAngle(-1.5 * Math.PI)).toBeCloseTo(0.5 * Math.PI, 12);
    expect(wrapAngle(1.5 * Math.PI)).toBeCloseTo(-0.5 * Math.PI, 12);
    expect(wrapAngle(Math.PI)).toBeCloseTo(-Math.PI, 12);
  });
});

describe("framingRotation", () => {
  const K = intrinsics(35, 1280, 720);
  it.each([
    [[0, 0, 0], [0.2, -0.1, 1.0], [400, 300]],
    [[0.05, 0, 0.1], [-0.3, 0.2, 0.9], [900, 500]],
    [[0, 0, 0], [0.0, 0.0, 1.0], [640, 360]],
  ])("frames the target at the desired pixel with roll 0 (c=%j)", (c, X, px) => {
    const R = framingRotation(c, X, px, K);
    const { uv } = project(K, pose(R, c), X);
    close(uv, px, 1e-8);
    close(matMul(R, transpose3(R)), [[1, 0, 0], [0, 1, 0], [0, 0, 1]], 1e-12);
    // roll-free: the camera x axis (row 0 of R) is horizontal in the world
    expect(Math.abs(R[0][1])).toBeLessThan(1e-12);
  });
  it("throws when the target sits on the camera or no roll-free solution exists", () => {
    expect(() => framingRotation([0, 0, 1], [0, 0, 1], [640, 360], K)).toThrow();
    expect(() => framingRotation([0, 0, 0], [0, -1, 0.001], [1279, 0], K)).toThrow(/roll-free/);
  });
});

describe("ease / quaternions / interpolate", () => {
  it("smoothstep easing clamps to [0, 1]", () => {
    expect(ease(0.5, "ease_in_out")).toBe(0.5);
    expect(ease(0.25, "ease_in_out")).toBeCloseTo(0.15625, 15);
    expect(ease(-1, "linear")).toBe(0);
    expect(ease(2, "ease_in_out")).toBe(1);
    expect(() => ease(0.5, "bounce")).toThrow();
  });
  it("round-trips rotations through canonical w >= 0 quaternions", () => {
    for (const R of [rotX(0.3), rotY(-2.9), matMul(rotY(3.1), rotX(-1.2)), matMul(rotX(Math.PI), rotY(0.1))] as Mat3[]) {
      const q = rotToQuat(R);
      expect(q[0]).toBeGreaterThanOrEqual(0);
      close(quatToRot(q), R, 1e-12);
    }
  });
  it("slerp takes the shortest path and hits the endpoints", () => {
    const qa = rotToQuat(rotY(0.2));
    const qb = rotToQuat(rotY(0.6)).map((x) => -x);
    close(quatToRot(slerp(qa, qb, 0.5)), rotY(0.4), 1e-12);
    close(slerp(qa, qb, 0), qa, 1e-12);
  });
  it("interpolate clamps outside the key range and returns key poses exactly", () => {
    const A = pose(rotY(0.1), [0, 0, 0]);
    const B = pose(rotY(0.3), [0.1, 0, 0.2]);
    const [p0, p1, mid, p2] = interpolate([[0.2, A], [0.8, B]], [-1, 0.8, 0.5, 9], "linear");
    expect(p0).toEqual(A);
    expect(p1).toEqual(B);
    expect(p2).toEqual(B);
    close(cameraCenter(mid), [0.05, 0, 0.1], 1e-12);
    close(rotToQuat([[mid[0][0], mid[0][1], mid[0][2]], [mid[1][0], mid[1][1], mid[1][2]], [mid[2][0], mid[2][1], mid[2][2]]]), rotToQuat(rotY(0.2)), 1e-12);
    expect(() => interpolate([[0.5, A], [0.5, B]], [0.5], "linear")).toThrow();
  });
});

describe("toThreeCamera", () => {
  it("gives Three's vertical fov and aspect from K_out", () => {
    const K = intrinsics(50, 720, 1280);
    const c = toThreeCamera(I4, K, 720, 1280);
    expect(c.aspect).toBeCloseTo(720 / 1280, 12);
    expect(c.fov).toBeCloseTo((2 * Math.atan(640 / K[1][1]) * 180) / Math.PI, 12);
    // identity OpenCV pose looks down +z_cv, i.e. -z in Three's world (y and z flipped)
    expect(c.position.length()).toBe(0);
    expect(c.quaternion.toArray().map((x) => Math.abs(x))).toEqual([0, 0, 0, 1]);
  });
  it("rejects intrinsics with an off-centre principal point or non-square pixels", () => {
    const K = intrinsics(50, 1280, 720);
    expect(() => toThreeCamera(I4, [[K[0][0], 0, 600], K[1], K[2]], 1280, 720)).toThrow(/principal point/);
    expect(() => toThreeCamera(I4, [[K[0][0] * 1.1, 0, 640], K[1], K[2]], 1280, 720)).toThrow(/square/);
  });
});
