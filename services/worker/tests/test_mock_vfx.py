"""Mock provider of vfx_masks_v1: fixture GT masks (resampled by the PTS map) or deterministic GrabCut."""

import io
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from oc_worker.comfy.mock import MockRunner
from open_creative.media.video import encode_mp4
from open_creative.media.video_ingest import make_proxy
from open_creative.pocket_vfx.masks import build_prompts, read_masks_zip, upscale_mask
from PIL import Image

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "pocket-vfx"
MANIFEST = json.loads((FIX / "manifest.json").read_text())
STOMP_SHA = MANIFEST["files"]["stomp_landscape.mp4"]["sha256"]
PW, PH = 640, 360


def gt_mask(frame: int) -> np.ndarray:
    with zipfile.ZipFile(FIX / "stomp_masks.zip") as z:
        return np.asarray(Image.open(io.BytesIO(z.read(f"mask_{frame:04d}.png"))).convert("L")) > 127


def iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum() / max((a | b).sum(), 1))


def submit(runner, frames_path, prompts, key="k", w=PW, h=PH):
    jid = runner.submit("vfx_masks_v1", {"frames": str(frames_path), "prompts": prompts, "width": w, "height": h},
                        key)
    st = runner.inspect(jid)
    assert st["state"] == "succeeded", st
    return jid


def collect(runner, jid, out):
    files = runner.collect(jid, out, "vfx_masks_v1")
    zp = next(p for p in files if p.suffix == ".zip")
    side = json.loads(next(p for p in files if p.suffix == ".json").read_text())
    return zp, side


@pytest.fixture(scope="module")
def stomp_proxy(tmp_path_factory):
    out = tmp_path_factory.mktemp("proxy") / "proxy.mp4"
    make_proxy(FIX / "stomp_landscape.mp4", out)  # 1280x720 24 fps CFR == its own work clip
    return out


BODY = [{"frame": 43, "points": [{"x": 0.47, "y": 0.6, "label": 1}]}]


def test_fixture_clip_returns_gt_masks(tmp_path, stomp_proxy):
    pts = [i * 1000 / 24 for i in range(120)]
    prompts = build_prompts(BODY, {}, PW, PH, 120, mock_context={"source_sha256": STOMP_SHA, "source_pts_ms": pts})
    r = MockRunner(tmp_path / "m")
    zp, side = collect(r, submit(r, stomp_proxy, prompts), tmp_path / "o")
    assert side["mock"] is True and side["source"] == "fixture_gt"
    seq = read_masks_zip(zp, expect_frames=120, expect_size=(PW, PH))
    assert seq.model.startswith("Mock")
    scores = [iou(upscale_mask(seq.masks[f], 1280, 720) >= 0.5, gt_mask(f)) for f in range(0, 120, 7)]
    assert min(scores) >= 0.95, scores


def test_fixture_gt_follows_the_pts_map(tmp_path):
    n = 24
    frames = tmp_path / "trim.mp4"  # a 1 s trim starting at 2 s: work frame i shows source frame 48 + i
    encode_mp4((np.zeros((PH, PW, 3), np.uint8) for _ in range(n)), frames, PW, PH)
    pts = [2000 + i * 1000 / 24 for i in range(n)]
    prompts = build_prompts(BODY, {}, PW, PH, n, mock_context={"source_sha256": STOMP_SHA, "source_pts_ms": pts})
    r = MockRunner(tmp_path / "m")
    zp, _ = collect(r, submit(r, frames, prompts), tmp_path / "o")
    seq = read_masks_zip(zp, expect_frames=n)
    for i in (0, 10, 23):
        up = upscale_mask(seq.masks[i], 1280, 720) >= 0.5
        assert iou(up, gt_mask(48 + i)) > iou(up, gt_mask(i)) and iou(up, gt_mask(48 + i)) >= 0.95


def synthetic_clip(path, n=16):
    """A dark-red disc moving right over a light textured background."""
    rng = np.random.default_rng(0)
    bg = np.clip(200 + rng.normal(0, 6, (PH, PW, 3)), 0, 255).astype(np.uint8)
    frames = []
    for i in range(n):
        f = bg.copy()
        cv2.circle(f, (200 + 4 * i, 180), 60, (150, 30, 40), -1)
        frames.append(f)
    encode_mp4(iter(frames), path, PW, PH)
    return [((200 + 4 * i, 180), 60) for i in range(n)]


def disc(center, r):
    m = np.zeros((PH, PW), np.uint8)
    cv2.circle(m, center, r, 1, -1)
    return m.astype(bool)


def test_grabcut_masks_for_other_clips_are_deterministic(tmp_path):
    gt = synthetic_clip(tmp_path / "c.mp4")
    prompts = build_prompts([{"frame": 8, "points": [{"x": 232 / PW, "y": 0.5, "label": 1},
                                                     {"x": 0.9, "y": 0.1, "label": 0}]}], {}, PW, PH, 16,
                            mock_context={"source_sha256": "0" * 64, "source_pts_ms": []})
    r = MockRunner(tmp_path / "m")
    za, side = collect(r, submit(r, tmp_path / "c.mp4", prompts, "a"), tmp_path / "a")
    zb, _ = collect(r, submit(r, tmp_path / "c.mp4", prompts, "b"), tmp_path / "b")
    assert side["mock"] is True and side["source"] == "grabcut"
    a, b = read_masks_zip(za, expect_frames=16), read_masks_zip(zb)
    assert all(np.array_equal(x, y) for x, y in zip(a.masks, b.masks, strict=True))
    for f in (0, 8, 15):  # backward and forward from the prompt frame
        assert iou(a.masks[f] > 127, disc(*gt[f])) >= 0.9, f


def test_mask_edit_is_a_prompt_frame(tmp_path):
    gt = synthetic_clip(tmp_path / "c.mp4")
    edit = (disc(*gt[12]) * 255).astype(np.uint8)  # the user paints the subject on frame 12
    # a bad point prompt on frame 2 (on the background): only the edit can recover the frames after it
    prompts = build_prompts([{"frame": 2, "points": [{"x": 0.85, "y": 0.2, "label": 1}]}], {12: edit}, PW, PH, 16)
    r = MockRunner(tmp_path / "m")
    zp, _ = collect(r, submit(r, tmp_path / "c.mp4", prompts), tmp_path / "o")
    seq = read_masks_zip(zp)
    assert np.array_equal(seq.masks[12], edit)  # the edit frame is used as given
    assert iou(seq.masks[15] > 127, disc(*gt[15])) >= 0.9  # and re-propagates forward from it
    assert iou(seq.masks[5] > 127, disc(*gt[5])) < 0.5  # frames before it still follow the (bad) point prompt


def test_masks_contract_is_enforced(tmp_path):
    synthetic_clip(tmp_path / "c.mp4", n=4)
    r = MockRunner(tmp_path / "m")
    prompts = build_prompts(BODY[:0], {}, PW, PH, 4)
    jid = r.submit("vfx_masks_v1", {"frames": str(tmp_path / "c.mp4"), "prompts": prompts, "width": PW,
                                    "height": PH}, "none")
    assert r.inspect(jid)["state"] == "failed" and "prompt" in r.inspect(jid)["error"]
    bad = build_prompts([{"frame": 0, "points": [{"x": 0.5, "y": 0.5, "label": 1}]}], {}, PW, PH, 9)
    jid = r.submit("vfx_masks_v1", {"frames": str(tmp_path / "c.mp4"), "prompts": bad, "width": PW,
                                    "height": PH}, "count")
    assert r.inspect(jid)["state"] == "failed" and "frames" in r.inspect(jid)["error"]


def test_grabcut_follows_the_portrait_scene(tmp_path):
    """Non-fixture input (unknown sha): GrabCut on the 360x640 proxy of the portrait scene, seeded by four clicks on
    one frame and propagated both ways, stays on the subject and in portrait orientation. A stand-in, not SAM: the
    propagated frames drift (wall pixels next to the arm join or the hand drops out), so their bar is low."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("pv_make_fixtures", FIX / "make_fixtures.py")
    mf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mf)
    dw, dh, pw, ph, n, f0 = 720, 1280, 360, 640, 48, 24
    bg = mf.background(dw, dh, 11)
    shots = [mf.draw(bg, i / 24) for i in range(n)]
    encode_mp4((cv2.resize(img, (pw, ph), interpolation=cv2.INTER_AREA) for img, _, _ in shots), tmp_path / "p.mp4",
               pw, ph)
    p = mf.pose(f0 / 24, dw, dh)
    u = p["u"]
    clicks = [(p["cx"], p["hip"] - 0.12 * u), (p["cx"], p["hip"] - 0.31 * u), p["hand"],
              (p["cx"] + 0.06 * u, p["ground"] - 0.02 * u)]
    prompts = build_prompts([{"frame": f0, "points": [{"x": x / dw, "y": y / dh, "label": 1} for x, y in clicks]}],
                            {}, pw, ph, n, mock_context={"source_sha256": "0" * 64, "source_pts_ms": []})
    r = MockRunner(tmp_path / "m")
    zp, side = collect(r, submit(r, tmp_path / "p.mp4", prompts, w=pw, h=ph), tmp_path / "o")
    assert side["source"] == "grabcut"
    seq = read_masks_zip(zp, expect_frames=n, expect_size=(pw, ph))
    scores = {f: iou(upscale_mask(seq.masks[f], dw, dh) >= 0.5, shots[f][1].astype(bool)) for f in (0, 12, f0, 36, 47)}
    assert scores[f0] >= 0.85 and min(scores.values()) >= 0.6, scores
