"""Sketch -> camera path compiler for Camera Sketch (reference; ported to TS in Task 13).

The sketch is a *framing* path: where the target should sit on screen over time, relative to
its start (``desired(t) = target_uv + (sketch(t) - sketch(0))``, in OUTPUT uv01). The camera
center follows a bounded preset curve; each keyframe's yaw/pitch is solved so the target
projects at the smoothed desired framing. All algorithms are deterministic, float64.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

from .camera import (
    cover_fit,
    ease,
    framing_rotation,
    from_uv01,
    interpolate,
    output_intrinsics,
    pose,
    project,
)
from .scene import Z_FAR_CAP, DepthConvention

SOLVER_VERSION = "cs_framing_ls_v1"
SCHEMA_VERSION = 1
PARAMS = ("lateral_ratio", "push_ratio", "arc_deg")
NEAR = 0.05
FPS = 24
MAX_RAW_POINTS = 128
MAX_CONTROL_POINTS = 5
RDP_EPS0 = 0.005
RDP_MAX_DOUBLINGS = 32
CLOSED_DIST = 0.02
CLOSED_MIN_LEN = 0.1
BACKTRACK_COS = -0.5  # turn > 120 degrees
FRAMING_LO, FRAMING_HI = 0.05, 0.95
# Reasons that a smaller motion may fix; they trigger the ``suggested_params`` search.
MOTION_REASONS = frozenset(
    {"param_out_of_bounds", "framing_unsolvable", "camera_upside_down", "camera_turned_around",
     "target_behind_camera", "target_out_of_frame"}
)

DEFAULT_PRESETS = Path(__file__).resolve().parents[4] / "presets" / "camera-sketch" / "presets.v1.json"
PACKAGED_PRESETS = Path(__file__).with_name("presets.v1.json")


def presets_path() -> Path:
    """Repo ``presets/camera-sketch`` -> copy packaged with the custom node."""
    return DEFAULT_PRESETS if DEFAULT_PRESETS.exists() else PACKAGED_PRESETS


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, dict]:
    data = json.loads(Path(path).read_text())
    return {p["id"]: p for p in data["presets"]}


def load_presets() -> dict[str, dict]:
    """``{id: {id, label_en, label_ko, bounds{param: [lo, hi]}, default{param: value}}}``."""
    return _load(str(presets_path()))


# ---- sketch simplification -------------------------------------------------------------


def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    """Distance from p to segment ab (to ``a`` when ``a == b``)."""
    ab = b - a
    den = float(ab @ ab)
    s = 0.0 if den == 0.0 else min(max(float((p - a) @ ab) / den, 0.0), 1.0)
    return float(np.hypot(*(p - (a + s * ab))))


def _rdp(P: np.ndarray, eps: float) -> list[int]:
    """Ramer-Douglas-Peucker on an Nx2 polyline; returns kept indices (sorted).

    Iterative, stack of ``(i, j)`` spans starting with ``(0, n-1)``; per span the interior
    point with the largest point-to-*segment* distance (first index on ties) is kept iff
    that distance is ``> eps`` (strict), and the span is split there.
    """
    keep = {0, len(P) - 1}
    stack = [(0, len(P) - 1)]
    while stack:
        i, j = stack.pop()
        best, best_d = -1, -1.0
        for k in range(i + 1, j):
            d = _seg_dist(P[k], P[i], P[j])
            if d > best_d:
                best, best_d = k, d
        if best >= 0 and best_d > eps:
            keep.add(best)
            stack.append((i, best))
            stack.append((best, j))
    return sorted(keep)


def simplify_sketch(
    points: list[tuple[float, float, float]],
) -> tuple[list[tuple[float, float, float]], str | None]:
    """Raw ``(t, u, v)`` sketch -> 2..5 control points ``(t, u, v)`` and an optional warning.

    1. Points are stably sorted by t (drawing direction = time order); t is otherwise ignored.
    2. More than 128 points: keep indices ``(k * (n-1)) // 127`` for ``k = 0..127``.
    3. Zero total arc length (incl. a single click) -> static ``[(0,u0,v0), (1,u0,v0)]``.
    4. RDP with ``eps = 0.005``, doubled while more than 5 points remain (<= 32 doublings,
       then only the endpoints are kept).
    5. Control point ``k`` gets ``t = L(idx_k) / L_total`` where ``L`` is the cumulative arc
       length along the (downsampled) raw polyline; first is 0.0, last exactly 1.0.
    Warning ``"unsupported_sketch"`` when the raw path closes (``|end - start| <= 0.02`` and
    ``L_total > 0.1``) or turns back: the directions of ANY two control segments ``i < j``
    (not only consecutive ones, so a U-turn split over two corners counts) differ by more
    than 120 degrees (``a.b < -0.5 |a||b|``).
    """
    if not points:
        raise ValueError("sketch has no points")
    pts = sorted(((float(t), float(u), float(v)) for t, u, v in points), key=lambda p: p[0])
    n = len(pts)
    if n > MAX_RAW_POINTS:
        pts = [pts[(k * (n - 1)) // (MAX_RAW_POINTS - 1)] for k in range(MAX_RAW_POINTS)]
    P = np.array([(u, v) for _, u, v in pts])
    seg = np.hypot(*np.diff(P, axis=0).T) if len(P) > 1 else np.zeros(0)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    if total <= 0.0:
        u0, v0 = pts[0][1], pts[0][2]
        return [(0.0, u0, v0), (1.0, u0, v0)], None
    eps, idx = RDP_EPS0, _rdp(P, RDP_EPS0)
    for _ in range(RDP_MAX_DOUBLINGS):
        if len(idx) <= MAX_CONTROL_POINTS:
            break
        eps *= 2.0
        idx = _rdp(P, eps)
    if len(idx) > MAX_CONTROL_POINTS:
        idx = [0, len(P) - 1]
    ctrl = [(float(cum[i] / total), pts[i][1], pts[i][2]) for i in idx]
    ctrl[0], ctrl[-1] = (0.0, *ctrl[0][1:]), (1.0, *ctrl[-1][1:])
    warning = None
    if float(np.hypot(*(P[-1] - P[0]))) <= CLOSED_DIST and total > CLOSED_MIN_LEN:
        warning = "unsupported_sketch"
    dirs = np.diff(P[idx], axis=0)
    for i in range(len(dirs)):
        for j in range(i + 1, len(dirs)):
            a, b = dirs[i], dirs[j]
            if float(a @ b) < BACKTRACK_COS * float(np.hypot(*a) * np.hypot(*b)):
                warning = "unsupported_sketch"
    return ctrl, warning


# ---- framing least squares -------------------------------------------------------------


def _system(n: int, lam_vel: float, lam_acc: float) -> np.ndarray:
    A = np.eye(n)
    if n >= 2:
        D1 = np.diff(np.eye(n), axis=0)
        A += lam_vel * D1.T @ D1
    if n >= 3:
        D2 = np.diff(np.eye(n), n=2, axis=0)
        A += lam_acc * D2.T @ D2
    return A


def _solve_fixed(A: np.ndarray, b: np.ndarray, fixed: dict[int, float]) -> np.ndarray:
    n = len(b)
    x = np.array([fixed.get(i, 0.0) for i in range(n)])
    free = [i for i in range(n) if i not in fixed]
    if free:
        F = list(fixed)
        x[free] = np.linalg.solve(A[np.ix_(free, free)], b[free] - A[np.ix_(free, F)] @ x[F])
    return x


def solve_framing(
    desired: np.ndarray,
    lam_vel: float = 0.01,
    lam_acc: float = 0.1,
    lo: float = FRAMING_LO,
    hi: float = FRAMING_HI,
) -> np.ndarray:
    """Smoothed, box-bounded framing ``p`` (Nx2) for desired framing ``d`` (Nx2, uv01).

    Minimizes ``sum|p-d|^2 + lam_vel sum|dp|^2 + lam_acc sum|d2p|^2`` per coordinate (u
    column, then v) with ``p[0] = d[0]`` fixed (never clamped: frame 0 is the source view).
    Normal equations ``A = I + lam_vel D1'D1 + lam_acc D2'D2``; fixed entries move to the
    right-hand side and the free block is solved with ``np.linalg.solve``. Active set: solve;
    then up to 5 rounds of {free entries outside ``[lo, hi]`` (in index order) are fixed at
    the violated bound; re-solve}, stopping early when none violate; finally free entries are
    clipped to ``[lo, hi]``.
    """
    d = np.asarray(desired, dtype=float).reshape(-1, 2)
    n = len(d)
    A = _system(n, lam_vel, lam_acc)
    out = d.copy()
    for col in range(2):
        b = d[:, col]
        fixed = {0: float(b[0])}
        x = _solve_fixed(A, b, fixed)
        for _ in range(5):
            viol = [i for i in range(1, n) if i not in fixed and (x[i] < lo or x[i] > hi)]
            if not viol:
                break
            for i in viol:
                fixed[i] = lo if x[i] < lo else hi
            x = _solve_fixed(A, b, fixed)
        free = [i for i in range(1, n) if i not in fixed]
        x[free] = np.clip(x[free], lo, hi)
        out[:, col] = x
    return out


# ---- preset curves ---------------------------------------------------------------------


def preset_center(preset: str, params: dict, s: float, target_world: np.ndarray) -> np.ndarray:
    """Camera center at eased progress ``s`` in [0, 1]; every preset starts at the origin.

    slide: ``(lateral_ratio*s, 0, 0)``; push_in: ``(0, 0, push_ratio*s)``; small_arc: the
    source camera rotated by ``arc_deg*s`` about the vertical (world y) axis through the
    target, ``c = T + Ry(a) @ (0 - T)`` with ``Ry(a) = [[cos,0,sin],[0,1,0],[-sin,0,cos]]``.
    """
    s = float(s)
    if preset == "slide":
        return np.array([float(params.get("lateral_ratio", 0.0)) * s, 0.0, 0.0])
    if preset == "push_in":
        return np.array([0.0, 0.0, float(params.get("push_ratio", 0.0)) * s])
    if preset == "small_arc":
        a = math.radians(float(params.get("arc_deg", 0.0)) * s)
        T = np.asarray(target_world, dtype=float)
        c, sn = math.cos(a), math.sin(a)
        Ry = np.array([[c, 0.0, sn], [0.0, 1.0, 0.0], [-sn, 0.0, c]])
        return T + Ry @ (-T)
    raise ValueError(f"unknown preset {preset!r}")


# ---- compile ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SceneInfo:
    K_src: np.ndarray
    src_w: int
    src_h: int
    target_world: np.ndarray  # normalized scene units (target z ~ 1)
    target_uv01: tuple[float, float]  # in the SOURCE image
    depth_convention: DepthConvention | None = None


@dataclass
class CompiledPath:
    K: list
    source_to_output: dict
    depth_mapping: dict | None
    preset: str
    params: dict
    easing: str
    control_points: list
    keyframes: list = field(default_factory=list)
    samples: list = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    status: str = "ok"
    reasons: list = field(default_factory=list)
    suggested_params: dict | None = None

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION, "solver_version": SOLVER_VERSION,
            "coordinate_convention": "opencv_T_cw", "K": self.K,
            "source_to_output": self.source_to_output, "depth_mapping": self.depth_mapping,
            "preset": self.preset, "params": self.params, "easing": self.easing,
            "fps": FPS, "control_points": self.control_points, "keyframes": self.keyframes,
            "samples": self.samples, "diagnostics": self.diagnostics, "status": self.status,
            "reasons": self.reasons, "suggested_params": self.suggested_params,
        }


def _mat(M: np.ndarray) -> list:
    return [[float(x) for x in row] for row in np.asarray(M)]


def compile_path(
    cfg: dict, scene: SceneInfo, out_w: int, out_h: int, n_frames: int = 120,
    _suggest: bool = True,
) -> CompiledPath:
    """Compile a §10 ``path`` config into keyframes and ``n_frames`` dense ``T_cw`` samples.

    ``cfg``: ``preset``, ``sketch_points`` ([{t,u,v}] or [(t,u,v)], may be empty),
    ``lateral_ratio``/``push_ratio``/``arc_deg`` (missing -> preset default), ``easing``.
    Keyframes: one per control point (no sketch -> t = 0 and 1 with constant framing). For
    key k: ``e_k = ease(t_k)``, center ``preset_center(e_k)``, R = ``framing_rotation`` toward
    the solved framing ``p_k`` (output pixels via K_out). Sample i (``t_ms = i*1000/24``,
    progress ``s_i = i/(n-1)``): orientation = ``camera.interpolate`` over keys placed at
    ``e_k`` sampled at ``ease(s_i)`` with linear easing; center = ``preset_center(ease(s_i))``
    exactly (equal to the key interpolation for slide/push_in; keeps the arc on its circle).
    Statuses: ``invalid`` (no GPU use), ``warning``, ``ok``; reasons are machine codes.
    """
    presets = load_presets()
    name = str(cfg.get("preset", ""))
    easing = cfg.get("easing", "ease_in_out")
    fit = cover_fit(scene.src_w, scene.src_h, out_w, out_h)
    K_out = output_intrinsics(scene.K_src, fit)
    conv = scene.depth_convention
    out = CompiledPath(
        K=_mat(K_out),
        source_to_output={**asdict(fit), "src_w": int(scene.src_w), "src_h": int(scene.src_h)},
        depth_mapping=None if conv is None else
        {**{k: (float(v) if k != "kind" else v) for k, v in asdict(conv).items()},
         "z_far_cap": float(Z_FAR_CAP), "target_z": 1.0},
        preset=name, params={}, easing=easing, control_points=[],
        diagnostics={"framing_rms_px": None, "framing_rms_rel": None,
                     "target_in_front_min_z": None, "target_in_frame_fraction": None,
                     "hole_ratio": None, "framing_clamped": False, "details": []},
    )
    dg = out.diagnostics

    def fail(code: str, **detail) -> None:
        if code not in out.reasons:
            out.reasons.append(code)
        dg["details"].append({"code": code, **detail})

    def finish() -> CompiledPath:
        if any(r not in ("unsupported_sketch", "framing_clamped") for r in out.reasons):
            out.status = "invalid"
        elif out.reasons:
            out.status = "warning"
        if _suggest and any(r in MOTION_REASONS for r in out.reasons):
            out.suggested_params = _suggest_params(cfg, out.params, scene, out_w, out_h, n_frames)
        return out

    if name not in presets:
        fail("unknown_preset", preset=name)
    if easing not in ("linear", "ease_in_out"):
        fail("unknown_easing", easing=str(easing))
    if out.reasons:
        return finish()
    preset = presets[name]
    params = {p: float(cfg.get(p, preset["default"][p])) for p in PARAMS}
    out.params = params
    for p, (lo, hi) in preset["bounds"].items():
        if not (math.isfinite(params[p]) and lo - 1e-12 <= params[p] <= hi + 1e-12):
            fail("param_out_of_bounds", param=p, value=params[p], bounds=[lo, hi])
    T = np.asarray(scene.target_world, dtype=float)
    if not np.isfinite(T).all() or T[2] <= NEAR:
        fail("target_behind_camera", key=0)
    if out.reasons:
        return finish()

    # Target framing in OUTPUT uv01: source pixel -> cover-fit -> output pixel.
    tu, tv = scene.target_uv01
    t_out = np.array([(tu * scene.src_w * fit.scale + fit.offset_x) / out_w,
                      (tv * scene.src_h * fit.scale + fit.offset_y) / out_h])
    raw = [(p["t"], p["u"], p["v"]) if isinstance(p, dict) else tuple(p)
           for p in cfg.get("sketch_points") or []]
    if raw:
        ctrl, warning = simplify_sketch(raw)
        if warning:
            fail(warning)
    else:
        ctrl = [(0.0, 0.5, 0.5), (1.0, 0.5, 0.5)]
    out.control_points = [[float(x) for x in c] for c in ctrl] if raw else []
    ts = np.array([c[0] for c in ctrl])
    sk = np.array([c[1:] for c in ctrl])
    requested = t_out + (sk - sk[0])
    framing = solve_framing(requested)
    unclamped = solve_framing(requested, lo=-np.inf, hi=np.inf)
    if (unclamped[1:] < FRAMING_LO).any() or (unclamped[1:] > FRAMING_HI).any():
        dg["framing_clamped"] = True
        fail("framing_clamped")

    e_keys = np.array([ease(t, easing) for t in ts])
    keys = []
    for k, (t, e) in enumerate(zip(ts, e_keys)):
        c = preset_center(name, params, e, T)
        try:
            R = framing_rotation(c, T, from_uv01(framing[k], out_w, out_h), K_out)
        except ValueError:
            fail("framing_unsolvable", key=k)
            continue
        if R[1, 1] <= 0:
            fail("camera_upside_down", key=k)
        if R[2, 2] <= 0:
            fail("camera_turned_around", key=k)
        keys.append((float(e), pose(R, c)))
        out.keyframes.append({"t": float(t), "T_cw": _mat(pose(R, c)),
                              "desired_uv": [float(x) for x in framing[k]],
                              "requested_uv": [float(x) for x in requested[k]]})
    if any(r not in ("unsupported_sketch", "framing_clamped") for r in out.reasons):
        out.keyframes = []
        return finish()

    s = np.arange(n_frames) / (n_frames - 1)
    e_s = np.array([ease(x, easing) for x in s])
    Rs = interpolate(keys, e_s, "linear")
    Ts = [pose(Ti[:3, :3], preset_center(name, params, e, T)) for Ti, e in zip(Rs, e_s)]
    want = np.stack([np.interp(e_s, e_keys, framing[:, i]) for i in range(2)], 1)
    uv = np.empty((n_frames, 2))
    z = np.empty(n_frames)
    for i, Ti in enumerate(Ts):
        uv_i, z_i = project(K_out, Ti, T)
        uv[i], z[i] = uv_i[0], z_i[0]
    if not all(np.isfinite(Ti).all() for Ti in Ts):
        fail("non_finite")
        return finish()
    front = z > NEAR
    inside = front & (uv[:, 0] >= 0) & (uv[:, 0] <= out_w) & (uv[:, 1] >= 0) & (uv[:, 1] <= out_h)
    dg["target_in_front_min_z"] = float(z.min())
    dg["target_in_frame_fraction"] = float(inside.mean())
    if front.any():
        err = uv[front] - from_uv01(want[front], out_w, out_h)
        rms = float(np.sqrt(np.mean(np.sum(err * err, axis=1))))
        dg["framing_rms_px"] = rms
        dg["framing_rms_rel"] = rms / min(out_w, out_h)
    if not front.all():
        fail("target_behind_camera", first_frame=int(np.argmin(front)))
    elif not inside.all():
        fail("target_out_of_frame", first_frame=int(np.argmin(inside)))
    out.samples = [{"t_ms": i * 1000 / FPS, "T_cw": _mat(Ti)} for i, Ti in enumerate(Ts)]
    return finish()


def _suggest_params(
    cfg: dict, params: dict, scene: SceneInfo, out_w: int, out_h: int, n_frames: int
) -> dict | None:
    """Largest ``params * 0.5**k`` (k = 1..4) whose compile has no motion reason; else None."""
    if not params:
        return None
    for k in range(1, 5):
        trial = {p: params[p] * 0.5**k for p in PARAMS}
        res = compile_path({**cfg, **trial}, scene, out_w, out_h, n_frames, _suggest=False)
        if not any(r in MOTION_REASONS for r in res.reasons):
            return trial
    return None
