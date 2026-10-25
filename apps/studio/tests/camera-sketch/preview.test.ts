import { describe, expect, it } from "vitest";
import { cameraCenter, pose, rotY, type Mat4 } from "../../src/features/camera-sketch/model/camera";
import { compilePathProvisional, sceneInfoFromTarget } from "../../src/features/camera-sketch/model/path";
import { IDENTITY_POSE, cameraGlyph, fitGizmo, gizmoPlane, outputSize, poseAtTime } from "../../src/features/camera-sketch/model/preview";
import { toThreePoint, toThreePositions } from "../../src/features/camera-sketch/model/three";

const scene = sceneInfoFromTarget({ focalMm: 50, srcW: 960, srcH: 540, u: 0.62, v: 0.55, depthScale: 1 });
const compiled = compilePathProvisional({ preset: "slide", lateral_ratio: 0.04, easing: "ease_in_out" }, scene, 1280, 720);

describe("poseAtTime", () => {
  it("returns the exact sample at its own time and clamps outside the shot", () => {
    const s = compiled.samples;
    expect(s.length).toBe(120);
    expect(poseAtTime(s, s[37].t_ms)).toEqual(s[37].T_cw);
    expect(poseAtTime(s, -100)).toEqual(s[0].T_cw);
    expect(poseAtTime(s, 99_999)).toEqual(s[119].T_cw);
  });
  it("interpolates between neighbouring samples", () => {
    const s = compiled.samples;
    const t = (s[50].t_ms + s[51].t_ms) / 2;
    const c = cameraCenter(poseAtTime(s, t)), a = cameraCenter(s[50].T_cw), b = cameraCenter(s[51].T_cw);
    for (let k = 0; k < 3; k++) expect(c[k]).toBeCloseTo((a[k] + b[k]) / 2, 9);
  });
  it("falls back to the source pose without samples", () => {
    expect(poseAtTime([], 1000)).toEqual(IDENTITY_POSE);
  });
});

describe("gizmo helpers", () => {
  it("top view puts forward z up, side view puts forward z right and y down", () => {
    expect(gizmoPlane([1, 2, 3], "top")).toEqual([1, -3]);
    expect(gizmoPlane([1, 2, 3], "side")).toEqual([3, 2]);
  });
  it("fits points uniformly inside the box with padding", () => {
    const f = fitGizmo([[0, 0], [1, -2]], 220, 160, 20);
    for (const p of [[0, 0], [1, -2]] as [number, number][]) {
      const [x, y] = f.map(p);
      expect(x).toBeGreaterThanOrEqual(20 - 1e-9); expect(x).toBeLessThanOrEqual(200 + 1e-9);
      expect(y).toBeGreaterThanOrEqual(20 - 1e-9); expect(y).toBeLessThanOrEqual(140 + 1e-9);
    }
    expect(f.scale).toBeCloseTo(60, 9);
  });
  it("camera glyph points along the camera's forward axis", () => {
    const g = cameraGlyph(IDENTITY_POSE, "top", 0.1);
    expect(g.c.map(Math.abs)).toEqual([0, 0]);
    const tip = [(g.wedge[1][0] + g.wedge[2][0]) / 2, (g.wedge[1][1] + g.wedge[2][1]) / 2];
    expect(tip[1]).toBeCloseTo(-0.1, 12); // forward = up in the top view
    const yawed: Mat4 = pose(rotY(0.3), [0.05, 0, 0]);
    expect(cameraGlyph(yawed, "top", 0.1).c[0]).toBeCloseTo(0.05, 12);
  });
  it("maps aspects to the fixed output sizes", () => {
    expect(outputSize("16:9")).toEqual([1280, 720]);
    expect(outputSize("9:16")).toEqual([720, 1280]);
  });
});

describe("toThreePositions", () => {
  it("applies the same flip as toThreePoint", () => {
    const out = toThreePositions(Float32Array.from([1, 2, 3, -4, 5, -6]));
    const p = toThreePoint([-4, 5, -6]);
    expect(Array.from(out)).toEqual([1, -2, -3, p.x, p.y, p.z]);
  });
});

describe("fitGizmo scale override", () => {
  it("uses the given scale and still centres the bounding box", () => {
    const f = fitGizmo([[0, 0], [1, 1]], 200, 100, 10, 0.2, 30);
    expect(f.scale).toBe(30);
    expect(f.map([0.5, 0.5])).toEqual([100, 50]);
  });
});

describe("stage zoom (CSS only)", () => {
  it("keeps the point under the cursor, clamps 1..4 and keeps the frame covered", async () => {
    const { zoomAt, NO_ZOOM } = await import("../../src/features/camera-sketch/model/preview");
    const z = zoomAt(NO_ZOOM, 200, 100, 2, 800, 450);
    expect(z).toEqual({ scale: 2, tx: -200, ty: -100 });
    // content point under the cursor: (200 - tx) / scale = 200
    expect((200 - z.tx) / z.scale).toBe(200);
    expect(zoomAt(z, 0, 0, 100, 800, 450).scale).toBe(4);
    expect(zoomAt(z, 400, 200, 0.01, 800, 450)).toEqual({ scale: 1, tx: 0, ty: 0 });
    const edge = zoomAt(NO_ZOOM, 800, 450, 3, 800, 450);
    expect(edge).toEqual({ scale: 3, tx: -1600, ty: -900 });
    expect(zoomAt(NO_ZOOM, 10, 10, Number.NaN, 800, 450)).toEqual(NO_ZOOM);
  });
});
