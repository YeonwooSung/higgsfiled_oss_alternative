import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from oc_worker.comfy.mock import MockRunner
from open_creative.camera_sketch.scene import decode_depth_png16, rgb_pixel_hash
from open_creative.media.video import decode_frames, encode_mp4, probe

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "camera-sketch"
SCENES = json.loads((FIX / "scenes.json").read_text())["scenes"]


def depth_of(runner, jid, tmp_path, tag):
    outs = runner.collect(jid, tmp_path / tag, "camera_depth_v1")
    png = next(p for p in outs if p.suffix == ".png")
    meta = json.loads(next(p for p in outs if p.suffix == ".json").read_text())
    return png, meta


@pytest.mark.parametrize("name", sorted(SCENES))
def test_mock_depth_fixture_returns_gt_exactly(tmp_path, name):
    r = MockRunner(tmp_path / "m")
    jid = r.submit("camera_depth_v1", {"image": str(FIX / SCENES[name]["image"])}, "k")
    assert r.inspect(jid)["state"] == "succeeded"
    png, meta = depth_of(r, jid, tmp_path, "o")
    assert png.read_bytes() == (FIX / SCENES[name]["depth"]).read_bytes()
    assert meta["min"] == SCENES[name]["depth_png16"]["min"] and meta["max"] == SCENES[name]["depth_png16"]["max"]
    assert meta["mock"] is True and meta["source"] == "fixture_gt"


def test_mock_depth_matches_reencoded_png_by_pixel_hash(tmp_path):
    name = "room"
    rgb = np.asarray(Image.open(FIX / SCENES[name]["image"]).convert("RGB"))
    assert rgb_pixel_hash(rgb) == SCENES[name]["rgb_sha256"]
    re = tmp_path / "re.png"
    Image.fromarray(rgb).save(re, compress_level=0)  # different bytes, same pixels
    assert re.read_bytes() != (FIX / SCENES[name]["image"]).read_bytes()
    r = MockRunner(tmp_path / "m")
    jid = r.submit("camera_depth_v1", {"image": str(re)}, "k")
    png, meta = depth_of(r, jid, tmp_path, "o")
    assert png.read_bytes() == (FIX / SCENES[name]["depth"]).read_bytes() and meta["source"] == "fixture_gt"


def test_mock_depth_heuristic_deterministic_and_inverse_convention(tmp_path):
    rng = np.random.default_rng(5)
    img = tmp_path / "u.png"
    Image.fromarray((rng.random((60, 80, 3)) * 255).astype(np.uint8)).save(img)
    r = MockRunner(tmp_path / "m")
    outs = []
    for i in range(2):
        jid = r.submit("camera_depth_v1", {"image": str(img)}, f"k{i}")
        outs.append(depth_of(r, jid, tmp_path, f"o{i}"))
    assert outs[0][0].read_bytes() == outs[1][0].read_bytes()
    meta = outs[0][1]
    assert meta["mock"] is True and meta["source"] == "heuristic" and meta["min"] < meta["max"]
    d = decode_depth_png16(outs[0][0].read_bytes(), meta)
    assert d.shape == (60, 80) and d[-5:].mean() > d[:5].mean()  # bottom nearer = larger inverse depth


def cin_bindings(tmp_path, w=64, h=48, n=6, holes=True):
    frames = []
    for t in range(n):
        f = np.full((h, w, 3), 120, np.uint8)
        f[:, :, 0] = np.linspace(60, 200, w, dtype=np.uint8)[None, :]
        if holes:
            f[10:20, 20 + t : 36 + t] = 0
        frames.append(f)
    rgb, dep = tmp_path / "g_rgb.mp4", tmp_path / "g_depth.mp4"
    encode_mp4(iter(frames), rgb, w, h, fps=24)
    encode_mp4(iter([np.full((h, w, 3), 90, np.uint8)] * n), dep, w, h, fps=24)
    ref = tmp_path / "ref.png"
    Image.fromarray(frames[0]).save(ref)
    return {"guide_rgb": str(rgb), "guide_depth": str(dep), "reference": str(ref), "prompt": "x", "seed": 1,
            "width": w, "height": h, "frames": n, "control_strength": 0.6}, frames


def test_mock_cinematic_fills_holes_grades_and_labels_mock(tmp_path):
    b, src = cin_bindings(tmp_path)
    r = MockRunner(tmp_path / "m")
    jid = r.submit("camera_cinematic_v1", b, "k")
    assert r.inspect(jid)["state"] == "succeeded", r.inspect(jid)
    outs = r.collect(jid, tmp_path / "o", "camera_cinematic_v1")
    mp4 = next(p for p in outs if p.suffix == ".mp4")
    meta = json.loads(next(p for p in outs if p.suffix == ".json").read_text())
    assert meta["mock"] is True and meta["frames"] == 6
    info = probe(mp4)
    assert (info["width"], info["height"]) == (64, 48)
    frames = list(decode_frames(mp4))
    assert len(frames) == 6
    hole = frames[0][12:18, 24:32].astype(int)
    assert hole.mean() > 60  # inpainted, no longer black
    guide = list(decode_frames(b["guide_rgb"]))
    assert not np.array_equal(frames[0], guide[0])  # graded


def test_mock_cinematic_deterministic_and_frame_count_follows_binding(tmp_path):
    b, _ = cin_bindings(tmp_path, n=6)
    r = MockRunner(tmp_path / "m")
    res = []
    for i, n in enumerate((6, 8)):
        jid = r.submit("camera_cinematic_v1", {**b, "frames": n}, f"k{i}")
        (mp4,) = [p for p in r.collect(jid, tmp_path / f"o{i}", "camera_cinematic_v1") if p.suffix == ".mp4"]
        res.append(list(decode_frames(mp4)))
    assert len(res[0]) == 6 and len(res[1]) == 8


def test_mock_cinematic_accepts_frame_directory(tmp_path):
    b, src = cin_bindings(tmp_path)
    d = tmp_path / "frames"
    d.mkdir()
    for i, f in enumerate(src):
        Image.fromarray(f).save(d / f"{i:04d}.png")
    r = MockRunner(tmp_path / "m")
    jid = r.submit("camera_cinematic_v1", {**b, "guide_rgb": str(d)}, "k")
    assert r.inspect(jid)["state"] == "succeeded", r.inspect(jid)


def test_mock_cinematic_missing_binding_rejected(tmp_path):
    from oc_worker.comfy.runner import ProviderRejected

    b, _ = cin_bindings(tmp_path)
    b.pop("control_strength")
    with pytest.raises(ProviderRejected):
        MockRunner(tmp_path / "m").submit("camera_cinematic_v1", b, "k")


def test_mock_cinematic_streams_guide_and_holds_last_frame():
    from oc_worker.comfy.mock import _held_frames
    from oc_worker.comfy.runner import ProviderRejected

    pulled = []

    def stream(k):
        for i in range(k):
            pulled.append(i)
            yield np.full((2, 2, 3), i, np.uint8)

    out = _held_frames(stream(3), 5)
    first = next(out)
    assert first[0, 0, 0] == 0 and pulled == [0]  # lazy: one frame decoded per frame emitted
    assert [int(f[0, 0, 0]) for f in out] == [1, 2, 2, 2]  # last guide frame held
    pulled.clear()
    assert [int(f[0, 0, 0]) for f in _held_frames(stream(10), 4)] == [0, 1, 2, 3]  # trimmed
    assert len(pulled) == 4
    with pytest.raises(ProviderRejected):
        list(_held_frames(stream(0), 3))


def test_mock_cinematic_empty_guide_is_a_provider_failure(tmp_path):
    b, _ = cin_bindings(tmp_path)
    d = tmp_path / "empty"
    d.mkdir()
    r = MockRunner(tmp_path / "m")
    jid = r.submit("camera_cinematic_v1", {**b, "guide_rgb": str(d)}, "k")
    st = r.inspect(jid)
    assert st["state"] == "failed" and "no frames" in st["error"]
