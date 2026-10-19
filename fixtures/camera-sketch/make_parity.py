"""Python -> TS parity fixture for the Camera Sketch camera math (``parity.v1.json``).

Run: uv run python fixtures/camera-sketch/make_parity.py   (also run by make_fixtures.py)
The fixture is computed only from ``open_creative.camera_sketch.{camera,path}`` and the scene
metadata in ``scenes.json`` (sizes + target uv; no pixels are decoded), so it is deterministic.
``apps/camera-sketch/tests/parity.test.ts`` replays every section through the TS port and
``nodes/open_creative/tests/test_cs_parity.py`` checks the committed file equals a fresh build.

Sections (add new ones as top-level keys of ``build()``'s dict, e.g. mesh face counts):
- ``compile``: per case the inputs (cfg, scene, output size) and Python's K_src, K_out,
  cover-fit, control points, keyframes, 10 sampled poses (by index), target projections in
  OUTPUT pixels for those samples, status, reasons and suggested params.
- ``simplify_sketch``: raw sketches -> control points + warning.
- ``solve_framing``: desired framing -> smoothed/bounded framing.
- ``framing_rotation``: direct (c, target, px, K) -> R cases incl. angle-wrap branches.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from open_creative.camera_sketch.camera import (
    cover_fit,
    framing_rotation,
    intrinsics,
    project,
)
from open_creative.camera_sketch.path import (
    SceneInfo,
    compile_path,
    simplify_sketch,
    solve_framing,
)

OUT = Path(__file__).parent
PARITY = OUT / "parity.v1.json"
N_FRAMES = 120
SAMPLE_INDICES = [round(k * (N_FRAMES - 1) / 9) for k in range(10)]  # 10 poses incl. first/last
OUTPUTS = {"16:9": (1280, 720), "9:16": (720, 1280)}
SKETCH3 = [(0.0, 0.5, 0.5), (0.5, 0.47, 0.46), (1.0, 0.45, 0.5)]  # 3 control points survive RDP


def _scene(name: str, focal_mm: float, depth_scale: float = 1.0) -> dict:
    meta = json.loads((OUT / "scenes.json").read_text())["scenes"][name]
    return {"name": name, "focal_mm": focal_mm, "src_w": meta["width"], "src_h": meta["height"],
            "target_uv01": list(meta["target_uv01"]), "depth_scale": depth_scale}


def _scene_info(s: dict) -> SceneInfo:
    """Same construction as ``oc_api.camera_sketch.scene_load.scene_info_from_meta``."""
    w, h = s["src_w"], s["src_h"]
    K = intrinsics(s["focal_mm"], w, h)
    u, v = s["target_uv01"]
    ray = np.linalg.inv(K) @ np.array([u * w, v * h, 1.0])
    return SceneInfo(K_src=K, src_w=w, src_h=h, target_world=ray * float(s["depth_scale"]),
                     target_uv01=(u, v))


def _cfg(preset: str, sketch=SKETCH3, **params) -> dict:
    return {"preset": preset, "easing": "ease_in_out",
            "sketch_points": [{"t": t, "u": u, "v": v} for t, u, v in sketch], **params}


def _compile_case(cid: str, scene: dict, aspect: str, cfg: dict) -> dict:
    out_w, out_h = OUTPUTS[aspect]
    info = _scene_info(scene)
    cp = compile_path(cfg, info, out_w, out_h, N_FRAMES).to_dict()
    case = {
        "id": cid, "scene": scene, "aspect": aspect, "out_w": out_w, "out_h": out_h, "cfg": cfg,
        "n_frames": N_FRAMES,
        "K_src": info.K_src.tolist(), "target_world": info.target_world.tolist(),
        "K_out": cp["K"], "source_to_output": cp["source_to_output"],
        "status": cp["status"], "reasons": cp["reasons"], "suggested_params": cp["suggested_params"],
        "params": cp["params"], "control_points": cp["control_points"], "keyframes": cp["keyframes"],
        "diagnostics": {k: cp["diagnostics"][k] for k in
                        ("framing_rms_px", "target_in_front_min_z", "target_in_frame_fraction",
                         "framing_clamped")},
        "n_samples": len(cp["samples"]), "sample_indices": [], "samples": [], "target_px": [],
    }
    if cp["samples"]:
        K = np.array(cp["K"])
        for i in SAMPLE_INDICES:
            s = cp["samples"][i]
            uv, z = project(K, np.array(s["T_cw"]), info.target_world)
            case["sample_indices"].append(i)
            case["samples"].append(s)
            case["target_px"].append([float(uv[0, 0]), float(uv[0, 1]), float(z[0])])
    return case


def _compile_cases() -> list[dict]:
    room, facade = _scene("room", 50.0), _scene("facade", 35.0)
    cases = []
    for scene in (room, facade):  # landscape source and portrait source
        for aspect in OUTPUTS:
            for preset, params in (("slide", {"lateral_ratio": 0.04}), ("push_in", {"push_ratio": 0.08}),
                                   ("small_arc", {"arc_deg": -5.0})):
                cid = f"{scene['name']}_{aspect.replace(':', 'x')}_{preset}"
                cases.append(_compile_case(cid, scene, aspect, _cfg(preset, **params)))
    cases.append(_compile_case("room_16x9_slide_push", room, "16:9",
                               _cfg("slide", lateral_ratio=-0.05, push_ratio=0.1)))
    cases.append(_compile_case("facade_16x9_static_linear", facade, "16:9",
                               {**_cfg("small_arc", sketch=(), arc_deg=6.0), "easing": "linear"}))
    # warning: framing pushed past the [0.05, 0.95] box
    cases.append(_compile_case("room_9x16_framing_clamped", room, "9:16",
                               _cfg("slide", sketch=[(0, 0.5, 0.5), (0.5, 0.6, 0.45), (1, 0.75, 0.5)],
                                    lateral_ratio=0.02)))
    # invalid cases (status + reasons; no samples)
    cases.append(_compile_case("invalid_param_out_of_bounds", room, "16:9", _cfg("slide", lateral_ratio=0.2)))
    cases.append(_compile_case("invalid_target_behind", _scene("room", 50.0, depth_scale=0.04), "16:9",
                               _cfg("push_in")))
    cases.append(_compile_case("invalid_unknown_preset", room, "16:9", _cfg("dolly_zoom")))
    cases.append(_compile_case("invalid_input_sketch", room, "16:9",
                               {**_cfg("slide"), "sketch_points": [{"t": 0, "u": "x", "v": 0.5}]}))
    cases.append(_compile_case("invalid_input_param", room, "16:9", _cfg("slide", push_ratio=None)))
    # geometric invalids past the keyframe stage
    cropped = _scene("facade", 35.0)  # portrait source: the 16:9 cover-fit crops the target away
    cropped["target_uv01"] = [0.5, 0.1]
    cases.append(_compile_case("invalid_target_cropped_out", cropped, "16:9", _cfg("push_in")))
    corner = _scene("facade", 20.0, 0.3)  # wide lens, target at the top-left corner
    corner["target_uv01"] = [0.02, 0.0]
    cases.append(_compile_case("invalid_framing_unsolvable", corner, "9:16",
                               _cfg("slide", sketch=[(0, 0.5, 0.5), (1, 0.05, 0.95)],
                                    lateral_ratio=0.05, push_ratio=0.1)))
    return cases


def _simplify_cases() -> list[dict]:
    n = 200
    wave = [(k / (n - 1), 0.3 + 0.4 * k / (n - 1), 0.5 + 0.08 * math.sin(6.0 * k / (n - 1)))
            for k in range(n)]
    zig = [(k / 40, 0.2 + 0.6 * k / 40, 0.5 + (0.03 if k % 2 else -0.03) * (k % 7) / 7) for k in range(41)]
    loop = [(k / 32, 0.5 + 0.1 * math.cos(2 * math.pi * k / 32), 0.5 + 0.1 * math.sin(2 * math.pi * k / 32))
            for k in range(33)]
    uturn = [(0.0, 0.3, 0.5), (0.2, 0.5, 0.5), (0.4, 0.6, 0.55), (0.6, 0.5, 0.6), (0.8, 0.3, 0.6)]
    unsorted = [(0.9, 0.6, 0.4), (0.1, 0.4, 0.5), (0.5, 0.52, 0.47)]
    raw = {"three_points": SKETCH3, "single_click": [(0.0, 0.4, 0.6)], "repeated_point": [(0, 0.5, 0.5)] * 3,
           "wave_200": wave, "zigzag_41": zig, "closed_loop": loop, "u_turn": uturn, "unsorted_t": unsorted,
           "straight": [(k / 10, 0.2 + 0.05 * k, 0.5) for k in range(11)]}
    out = []
    for name, pts in raw.items():
        ctrl, warning = simplify_sketch(pts)
        out.append({"id": name, "points": [list(map(float, p)) for p in pts],
                    "control_points": [list(map(float, c)) for c in ctrl], "warning": warning})
    return out


def _solve_cases() -> list[dict]:
    rng = np.random.default_rng(1313)
    raw = {
        "two_keys": [[0.5, 0.5], [0.45, 0.52]],
        "three_keys": [[0.62, 0.55], [0.59, 0.51], [0.57, 0.55]],
        "five_keys_smooth": [[0.5, 0.5], [0.52, 0.48], [0.55, 0.47], [0.57, 0.49], [0.6, 0.5]],
        "upper_bound": [[0.88, 0.5], [0.95, 0.5], [1.02, 0.45], [1.1, 0.4]],
        "lower_bound": [[0.1, 0.9], [0.03, 0.97], [-0.02, 1.05], [0.0, 0.99], [0.04, 0.9]],
        "start_outside_box": [[0.98, 0.02], [0.9, 0.1], [0.8, 0.2]],
        "one_key": [[0.3, 0.7]],
        "random_5": rng.uniform(-0.1, 1.1, (5, 2)).tolist(),
    }
    out = []
    for name, d in raw.items():
        out.append({"id": name, "desired": d, "framing": solve_framing(np.array(d)).tolist(),
                    "unclamped": solve_framing(np.array(d), lo=-np.inf, hi=np.inf).tolist()})
    return out


def _cover_fit_cases() -> list[dict]:
    out = []
    for sw, sh, ow, oh in ((960, 540, 1280, 720), (960, 540, 720, 1280), (576, 1024, 1280, 720),
                           (576, 1024, 720, 1280), (1000, 1000, 1280, 720), (333, 777, 720, 1280)):
        f = cover_fit(sw, sh, ow, oh)
        out.append({"src": [sw, sh], "out": [ow, oh], "scale": f.scale, "offset_x": f.offset_x,
                    "offset_y": f.offset_y})
    return out


def _framing_rotation_cases() -> list[dict]:
    """Direct ``framing_rotation`` cases, incl. steep targets where ``-a - phi < -pi`` is wrapped
    (``wraps``). Note: since ``r_z > 0`` gives ``phi`` in (0, pi), the wrapped root can never be
    strictly smaller than ``|a - phi|`` and differs from the unwrapped one by 2 pi, so floor- vs
    trunc-mod cannot change R; the TS floor-mod helper is covered by its own unit test."""
    rng = np.random.default_rng(1314)
    K = intrinsics(35.0, 1280, 720)
    out = []
    while len(out) < 24:
        c = rng.uniform(-0.1, 0.1, 3)
        d = rng.normal(size=3)
        d[2] = abs(d[2]) * (0.2 if len(out) % 2 else 1.0)  # half of them steep (mostly up/down)
        tgt = c + d / np.linalg.norm(d)
        px = rng.uniform([0.0, 0.0], [1280.0, 720.0])
        try:
            R = framing_rotation(c, tgt, px, K)
        except ValueError:
            continue
        r = np.linalg.solve(K, [px[0], px[1], 1.0])
        r = r / np.linalg.norm(r)
        dn = (tgt - c) / np.linalg.norm(tgt - c)
        phi = math.atan2(r[2], r[1])
        a = math.acos(max(-1.0, min(1.0, dn[1] / math.hypot(r[1], r[2]))))
        out.append({"c": c.tolist(), "target": tgt.tolist(), "px": px.tolist(), "K": K.tolist(),
                    "R": R.tolist(), "wraps": bool(-a - phi < -math.pi)})
    return out


def build() -> dict:
    return {
        "schema": "camera-sketch-parity", "version": 1,
        "tolerances": {"target_px": 2.0, "pose": 1e-5, "simplify": 1e-6, "solve": 1e-6},
        "cover_fit": _cover_fit_cases(),
        "simplify_sketch": _simplify_cases(),
        "solve_framing": _solve_cases(),
        "framing_rotation": _framing_rotation_cases(),
        "compile": _compile_cases(),
    }


def dumps(data: dict) -> str:
    return json.dumps(data, indent=1, allow_nan=False) + "\n"


def main() -> None:
    PARITY.write_text(dumps(build()))


if __name__ == "__main__":
    main()
