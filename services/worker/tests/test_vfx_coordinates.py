"""End-to-end coordinate mapping (spec 6.1/6.2, 14 "Mask 좌표"): source display space -> work clip (rotation, PTS-mapped
CFR 24 fps, cover-fit) -> proxy (tracking) -> output (render).

The fixture manifest gives the GT hand position in display px per *source* frame PTS. Work frame ``i`` shows the
source frame at ``work_clip.meta_json.pts_map[i]``; display px map to output px with the work clip's cover
transform (``out = display * scale - offset``). The anchor is clicked at the GT uv of work frame 43 (uv is the same
in proxy and output space); the track must stay within 5 output px of GT, and the rendered effect at the peak (also
frame 43) must be centred on the anchor.
"""

import json
from pathlib import Path

import numpy as np
import pytest
from oc_api.models import Asset
from open_creative.media.video import decode_frames
from open_creative.pocket_vfx.timeline import frame_index
from test_vfx_executors import Proj, pv, vfx_render_mod  # noqa: F401 - pv is the project fixture

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "pocket-vfx"
MANIFEST = json.loads((FIX / "manifest.json").read_text())
ANCHOR_FRAME = 43
MAX_ERR_PX = 5.0

CASES = {
    # rotated_portrait is a 4 s source: 96 work frames, so its last frame (95) stands in for 119
    "rotated_portrait": {"file": "rotated_portrait.mp4", "trim": {"source_start_ms": 0, "duration_ms": 4000},
                         "aspect": "9:16", "size": (720, 1280), "frames": (0, 60, 95)},
    "vfr": {"file": "vfr.mp4", "trim": {"source_start_ms": 0, "duration_ms": 5000}, "aspect": "16:9",
            "size": (1280, 720), "frames": (0, 60, 119)},
}


def gt_output_px(name: str, work: Asset) -> np.ndarray:
    """GT hand position in output px for every work frame (via pts_map and the cover transform)."""
    gt = {round(h["pts_ms"], 3): (h["x"], h["y"]) for h in MANIFEST[name]["hand_display_px"]}
    keys = np.array(sorted(gt))
    t = work.transform_json
    out = []
    for pts in work.meta_json["pts_map"]:
        k = keys[np.argmin(np.abs(keys - pts))]
        assert abs(k - pts) <= 0.01, f"no GT source frame at PTS {pts}"
        x, y = gt[k]
        out.append((x * t["scale"] - t["offset_x"], y * t["scale"] - t["offset_y"]))
    return np.array(out)


def effect_centroid(render_path, work_path, frame: int) -> tuple[float, float]:
    """Luminance-gain-weighted centroid of what the render added over the work clip at ``frame``."""
    for i, (out, src) in enumerate(zip(decode_frames(render_path), decode_frames(work_path), strict=True)):
        if i == frame:
            gain = np.clip(out.astype(np.float64).sum(axis=2) - src.astype(np.float64).sum(axis=2), 0, None)
            gain[gain < 12] = 0  # ignore re-encode noise
            ys, xs = np.nonzero(gain)
            w = gain[ys, xs]
            return float((xs * w).sum() / w.sum()), float((ys * w).sum() / w.sum())
    raise AssertionError(f"render has no frame {frame}")


@pytest.mark.parametrize("name", CASES)
def test_gt_point_tracks_and_renders_in_output_space(ctx, db_factory, storage, pv, name, monkeypatch):  # noqa: F811
    # the centroid measures the procedural effect: the (asymmetric, edge-clipped) bundled dust plate would pull it
    # off the anchor; plate placement around the anchor is covered by test_vfx_plate
    monkeypatch.setattr(vfx_render_mod.PlateLayers, "at", lambda self, i, anchor, subject: None)
    case = CASES[name]
    proj = Proj(ctx, db_factory, storage, pv)
    analyze = proj.prepare(case["file"], trim=case["trim"], output={"aspect": case["aspect"], "fps": 24})
    work = proj.asset(analyze.result_json["work_asset_id"])
    w, h = case["size"]
    assert (work.width, work.height) == (w, h)
    gt = gt_output_px(name, work)
    assert len(gt) == frame_index(case["trim"]["duration_ms"])

    u, v = gt[ANCHOR_FRAME] / (w, h)  # the user's click, in uv of the (proxy) frame
    proj.revise(anchor={**proj.cfg["anchor"], "frame": ANCHOR_FRAME, "x": float(u), "y": float(v), "follow": True},
                # in front: the centroid measures the effect itself (the hand is part of the subject mask)
                effect={**proj.cfg["effect"], "start_ms": 1400, "peak_ms": 1800, "end_ms": 3400,
                        "layer_order": "in_front"})
    track = proj.run("vfx.track")
    doc = proj.json(track.result_json["track_asset_id"])
    assert all(doc["points"][f]["valid"] for f in case["frames"])
    errors = {p["frame"]: float(np.hypot(p["x"] * w - gt[p["frame"], 0], p["y"] * h - gt[p["frame"], 1]))
              for p in doc["points"] if p["valid"]}
    assert max(errors[f] for f in case["frames"]) <= MAX_ERR_PX, {f: errors[f] for f in case["frames"]}
    # every tracked frame, not only the sampled ones: the smoothed track must follow the displayed motion
    assert max(errors.values()) <= MAX_ERR_PX, max(errors.items(), key=lambda kv: kv[1])

    render = proj.run("vfx.render", {"track_job_id": str(track.id), "render_profile": "quick"}).result_json
    peak = frame_index(proj.cfg["effect"]["peak_ms"])
    assert peak == ANCHOR_FRAME
    cx, cy = effect_centroid(proj.path(render["render_video_asset_id"]), proj.path(work.id), peak)
    assert np.hypot(cx - u * w, cy - v * h) <= MAX_ERR_PX, (cx, cy, u * w, v * h)
    assert np.hypot(cx - gt[peak, 0], cy - gt[peak, 1]) <= MAX_ERR_PX
