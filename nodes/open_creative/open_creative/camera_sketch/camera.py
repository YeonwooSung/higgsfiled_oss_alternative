"""Camera convention core for Camera Sketch (the single reference; the TS port mirrors it).

Conventions:
- OpenCV camera axes: x right, y down, z forward.
- Poses are world-to-camera ``T_cw`` (4x4): ``X_c = R @ X_w + t``. ``T_wc`` is only ever
  formed as an explicit inverse. World frame = source camera at t=0 (identity pose).
- Pixels: continuous coordinates where pixel ``i`` covers ``[i, i+1)`` and its center is
  ``i + 0.5``; the image spans ``[0, width] x [0, height]`` and the principal point is
  ``(width/2, height/2)``. ``uv01`` is pixels divided by ``(width, height)``.
- Quaternions are ``(w, x, y, z)``, unit length.
- Pure NumPy, float64, deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

Easing = Literal["linear", "ease_in_out"]


def intrinsics(
    focal_mm: float, width: int, height: int, sensor_w_mm: float = 36.0
) -> np.ndarray:
    """Pinhole K with ``fx = fy = focal_mm / sensor_w_mm * width`` and ``c = (w/2, h/2)``."""
    f = focal_mm / sensor_w_mm * width
    return np.array([[f, 0.0, width / 2.0], [0.0, f, height / 2.0], [0.0, 0.0, 1.0]])


@dataclass(frozen=True)
class CoverFit:
    """Source pixel ``p`` maps to output pixel ``p * scale + (offset_x, offset_y)``."""

    scale: float
    offset_x: float
    offset_y: float
    out_w: int
    out_h: int


def cover_fit(src_w: int, src_h: int, out_w: int, out_h: int) -> CoverFit:
    """Scale the source uniformly to cover the output, then center-crop (offsets <= 0)."""
    if min(src_w, src_h, out_w, out_h) <= 0:
        raise ValueError("sizes must be positive")
    scale = max(out_w / src_w, out_h / src_h)
    return CoverFit(
        scale=scale,
        offset_x=(out_w - src_w * scale) / 2.0,
        offset_y=(out_h - src_h * scale) / 2.0,
        out_w=out_w,
        out_h=out_h,
    )


def output_intrinsics(K_src: np.ndarray, fit: CoverFit) -> np.ndarray:
    """K of the output frame: ``A @ K_src`` with ``A = [[s,0,ox],[0,s,oy],[0,0,1]]``."""
    A = np.array(
        [
            [fit.scale, 0.0, fit.offset_x],
            [0.0, fit.scale, fit.offset_y],
            [0.0, 0.0, 1.0],
        ]
    )
    return A @ np.asarray(K_src, dtype=float)


def project(
    K: np.ndarray, T_cw: np.ndarray, X: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Project world points ``X`` (Nx3) to pixels. Returns ``(uv Nx2, z N)``.

    ``z`` is camera-space depth; points with ``z <= 0`` get ``uv = NaN``.
    """
    X = np.asarray(X, dtype=float).reshape(-1, 3)
    T = np.asarray(T_cw, dtype=float)
    Xc = X @ T[:3, :3].T + T[:3, 3]
    z = Xc[:, 2]
    h = Xc @ np.asarray(K, dtype=float).T
    uv = np.full((len(X), 2), np.nan)
    front = z > 0
    uv[front] = h[front, :2] / h[front, 2:3]
    return uv, z


def to_uv01(px: np.ndarray, w: int, h: int) -> np.ndarray:
    """Pixels -> normalized ``[0,1]`` image coordinates (divide by ``(w, h)``)."""
    return np.asarray(px, dtype=float) / np.array([w, h], dtype=float)


def from_uv01(uv: np.ndarray, w: int, h: int) -> np.ndarray:
    """Normalized ``[0,1]`` image coordinates -> pixels (multiply by ``(w, h)``)."""
    return np.asarray(uv, dtype=float) * np.array([w, h], dtype=float)


def _rot_x(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def _rot_y(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def _wrap(a: float) -> float:
    return float((a + np.pi) % (2 * np.pi) - np.pi)


def framing_rotation(
    c_world: np.ndarray, target_world: np.ndarray, desired_px: np.ndarray, K: np.ndarray
) -> np.ndarray:
    """World-to-camera R (roll 0) whose ray through ``desired_px`` points exactly at the target.

    Camera-to-world is ``R_wc = Ry(yaw) @ Rx(pitch)`` (so the camera x-axis is horizontal),
    and ``R = R_wc.T``. With ``d`` = unit(target - c) and ``r`` = unit(K^-1 [u, v, 1]):
    1. pitch: ``Rx(pitch) r`` must have y = d_y, i.e. ``rho*cos(pitch + phi) = d_y`` with
       ``rho = hypot(r_y, r_z)``, ``phi = atan2(r_z, r_y)``. Of ``pitch = +-acos(d_y/rho) - phi``
       (wrapped to [-pi, pi)) take the smaller ``|pitch|`` (``+`` on ties).
    2. yaw: ``atan2(d_x, d_z) - atan2(p_x, p_z)`` where ``p = Rx(pitch) r``.
    Raises ValueError if the target coincides with the camera or ``|d_y| > rho``.
    """
    d = np.asarray(target_world, dtype=float) - np.asarray(c_world, dtype=float)
    n = np.linalg.norm(d)
    if not np.isfinite(n) or n < 1e-12:
        raise ValueError("target coincides with the camera center")
    d = d / n
    u, v = (float(x) for x in desired_px)
    r = np.linalg.solve(np.asarray(K, dtype=float), np.array([u, v, 1.0]))
    r = r / np.linalg.norm(r)
    rho = float(np.hypot(r[1], r[2]))
    ratio = d[1] / rho
    if abs(ratio) > 1.0 + 1e-12:
        raise ValueError("no roll-free rotation frames the target at desired_px")
    phi = float(np.arctan2(r[2], r[1]))
    a = float(np.arccos(np.clip(ratio, -1.0, 1.0)))
    cands = [_wrap(a - phi), _wrap(-a - phi)]
    pitch = cands[0] if abs(cands[0]) <= abs(cands[1]) else cands[1]
    p = _rot_x(pitch) @ r
    yaw = float(np.arctan2(d[0], d[2]) - np.arctan2(p[0], p[2]))
    return (_rot_y(yaw) @ _rot_x(pitch)).T


def pose(R: np.ndarray, c_world: np.ndarray) -> np.ndarray:
    """``T_cw`` from world-to-camera rotation R and camera center c: ``t = -R c``."""
    R = np.asarray(R, dtype=float)
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = -R @ np.asarray(c_world, dtype=float)
    return T


def camera_center(T_cw: np.ndarray) -> np.ndarray:
    """Camera center in world coordinates: ``c = -R^T t``."""
    T = np.asarray(T_cw, dtype=float)
    return -T[:3, :3].T @ T[:3, 3]


def ease(s: float, kind: Easing) -> float:
    """Easing on ``s`` clamped to [0,1]: linear, or cubic smoothstep ``3s^2 - 2s^3``."""
    s = min(max(float(s), 0.0), 1.0)
    if kind == "linear":
        return s
    if kind == "ease_in_out":
        return s * s * (3.0 - 2.0 * s)
    raise ValueError(f"unknown easing {kind!r}")


def rot_to_quat(R: np.ndarray) -> np.ndarray:
    """Rotation matrix -> unit quaternion ``(w, x, y, z)`` with ``w >= 0`` (Shepperd)."""
    m = np.asarray(R, dtype=float)
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = 2.0 * np.sqrt(tr + 1.0)
        q = np.array(
            [
                0.25 * s,
                (m[2, 1] - m[1, 2]) / s,
                (m[0, 2] - m[2, 0]) / s,
                (m[1, 0] - m[0, 1]) / s,
            ]
        )
    elif m[0, 0] >= m[1, 1] and m[0, 0] >= m[2, 2]:
        s = 2.0 * np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2])
        q = np.array(
            [
                (m[2, 1] - m[1, 2]) / s,
                0.25 * s,
                (m[0, 1] + m[1, 0]) / s,
                (m[0, 2] + m[2, 0]) / s,
            ]
        )
    elif m[1, 1] >= m[2, 2]:
        s = 2.0 * np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2])
        q = np.array(
            [
                (m[0, 2] - m[2, 0]) / s,
                (m[0, 1] + m[1, 0]) / s,
                0.25 * s,
                (m[1, 2] + m[2, 1]) / s,
            ]
        )
    else:
        s = 2.0 * np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1])
        q = np.array(
            [
                (m[1, 0] - m[0, 1]) / s,
                (m[0, 2] + m[2, 0]) / s,
                (m[1, 2] + m[2, 1]) / s,
                0.25 * s,
            ]
        )
    if q[0] < 0:
        q = -q
    return q / np.linalg.norm(q)


def quat_to_rot(q: np.ndarray) -> np.ndarray:
    """Unit quaternion ``(w, x, y, z)`` -> 3x3 rotation matrix (input is renormalized)."""
    w, x, y, z = np.asarray(q, dtype=float) / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )


def slerp(qa: np.ndarray, qb: np.ndarray, s: float) -> np.ndarray:
    """Shortest-path spherical interpolation of ``(w,x,y,z)`` quaternions at ``s`` in [0,1].

    If ``dot(qa, qb) < 0`` qb is negated first; if ``dot > 0.9995`` normalized lerp is used.
    """
    qa = np.asarray(qa, dtype=float)
    qb = np.asarray(qb, dtype=float)
    dot = float(qa @ qb)
    if dot < 0.0:
        qb, dot = -qb, -dot
    if dot > 0.9995:
        q = qa + s * (qb - qa)
        return q / np.linalg.norm(q)
    theta = np.arccos(min(dot, 1.0))
    q = (np.sin((1.0 - s) * theta) * qa + np.sin(s * theta) * qb) / np.sin(theta)
    return q / np.linalg.norm(q)


def interpolate(
    keys: list[tuple[float, np.ndarray]], times: np.ndarray, easing: Easing
) -> list[np.ndarray]:
    """Sample ``T_cw`` poses at ``times`` from keyframes ``[(t_i, T_cw_i)]``.

    ``t_i`` must be strictly increasing; times are clamped to ``[t_0, t_last]``. For the
    segment ``i`` = largest index with ``t_i <= t`` (capped at n-2),
    ``e = ease((t - t_i)/(t_{i+1} - t_i))``; the camera center is ``lerp(c_i, c_{i+1}, e)`` and the orientation
    ``slerp(q_i, q_{i+1}, e)``. ``e == 0`` / ``e == 1`` return the key pose itself (exact).
    """
    if not keys:
        raise ValueError("at least one key is required")
    ts = np.array([float(t) for t, _ in keys])
    if np.any(np.diff(ts) <= 0):
        raise ValueError("key times must be strictly increasing")
    Ts = [np.asarray(T, dtype=float) for _, T in keys]
    centers = [camera_center(T) for T in Ts]
    quats = [rot_to_quat(T[:3, :3]) for T in Ts]
    out: list[np.ndarray] = []
    for t in np.asarray(times, dtype=float).ravel():
        t = min(max(float(t), ts[0]), ts[-1])
        if len(ts) == 1:
            out.append(Ts[0].copy())
            continue
        i = int(np.clip(np.searchsorted(ts, t, side="right") - 1, 0, len(ts) - 2))
        e = ease((t - ts[i]) / (ts[i + 1] - ts[i]), easing)
        if e == 0.0:
            out.append(Ts[i].copy())
        elif e == 1.0:
            out.append(Ts[i + 1].copy())
        else:
            c = centers[i] + e * (centers[i + 1] - centers[i])
            R = quat_to_rot(slerp(quats[i], quats[i + 1], e))
            out.append(pose(R, c))
    return out
