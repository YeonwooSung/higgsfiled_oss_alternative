/**
 * The ONE place where OpenCV coordinates become Three.js coordinates.
 *
 * OpenCV (ours): x right, y down, z forward; poses are world-to-camera `T_cw`.
 * Three.js: x right, y up, camera looks down -z.
 * Conversion: flip y and z, `S = diag(1, -1, -1)`, applied to BOTH the world and the camera
 * frame, so the Three scene is y-up:
 *   point     X_three = S X_cv                    (`toThreePoint`, use for mesh vertices too)
 *   position  S c                                  (c = camera center = -R^T t)
 *   rotation  R_wc_three = S R_wc S = S R^T S      (camera-to-world)
 *   fov       vertical, `2 atan(h / (2 fy))` in degrees; aspect `w / h`.
 * Three's projection assumes a centred principal point and square pixels; `toThreeCamera` throws
 * when K violates that (our K is always `fx = fy`, `c = (w/2, h/2)`, also after cover-fit).
 */
import { Matrix4, Quaternion, Vector3 } from "three";
import { cameraCenter, type Mat3, type Mat4 } from "./camera";

export interface ThreeCameraPose {
  position: Vector3;
  quaternion: Quaternion;
  /** Vertical field of view in degrees (Three's `PerspectiveCamera.fov`). */
  fov: number;
  aspect: number;
}

/** OpenCV world point -> Three world point (flip y and z). */
export function toThreePoint(X: readonly number[]): Vector3 {
  return new Vector3(X[0], -X[1], -X[2]);
}

export function toThreeCamera(Tcw: Mat4, Kout: Mat3, w: number, h: number): ThreeCameraPose {
  const fx = Kout[0][0], fy = Kout[1][1];
  const tol = 1e-6 * Math.max(w, h);
  if (Math.abs(Kout[0][2] - w / 2) > tol || Math.abs(Kout[1][2] - h / 2) > tol) {
    throw new Error("toThreeCamera: K principal point must be centred (cx = w/2, cy = h/2)");
  }
  if (Math.abs(Kout[0][1]) > 1e-9 * fx || Math.abs(fx - fy) > 1e-9 * fy) {
    throw new Error("toThreeCamera: K must have square pixels (fx = fy) and zero skew");
  }
  const R = Tcw;
  // R_wc_three[i][j] = s_i * R_wc[i][j] * s_j with R_wc = R^T and s = (1, -1, -1)
  const s = [1, -1, -1];
  const m = (i: number, j: number) => s[i] * R[j][i] * s[j];
  const rot = new Matrix4().set(m(0, 0), m(0, 1), m(0, 2), 0, m(1, 0), m(1, 1), m(1, 2), 0, m(2, 0), m(2, 1), m(2, 2), 0, 0, 0, 0, 1);
  return {
    position: toThreePoint(cameraCenter(Tcw)),
    quaternion: new Quaternion().setFromRotationMatrix(rot),
    fov: (2 * Math.atan(h / (2 * fy)) * 180) / Math.PI,
    aspect: w / h,
  };
}
