"""Video ingest: probe (rotation / VFR / limits), PTS-mapped CFR 24 normalization, proxy,
filmstrip and trimmed audio. Uses the committed synthetic clips in fixtures/pocket-vfx."""

import bisect
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from open_creative.media.image import MediaError
from open_creative.media.video import decode_frames, probe
from open_creative.media.video_ingest import (
    PtsMap,
    _frame_index,
    cover_transform,
    extract_audio,
    filmstrip,
    first_frame_png,
    make_proxy,
    normalize_clip,
    probe_source,
    proxy_size,
)
from PIL import Image

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "pocket-vfx"
MANIFEST = json.loads((FIX / "manifest.json").read_text())


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True, timeout=120)


def _pcm(path):
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-f", "s16le", "-ac", "1",
         "-ar", "48000", "pipe:1"], capture_output=True, check=True, timeout=60)
    return np.frombuffer(out.stdout, np.int16).astype(np.float32) / 32768.0


def _audio_duration_ms(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=duration,sample_rate,channels,codec_name", "-of", "json", str(path)],
                         capture_output=True, check=True, timeout=30)
    return json.loads(out.stdout)["streams"][0]


def _hand_xy(frame, color):
    """Centroid of the hand disk (a saturated colour no other scene element uses)."""
    d = np.abs(frame.astype(np.int16) - np.asarray(color, np.int16)).sum(axis=2)
    ys, xs = np.nonzero(d < 60)
    assert len(xs) > 20, "hand disk not found"
    return float(xs.mean()), float(ys.mean())


def test_fixture_manifest_hashes_match():
    import hashlib

    for name, meta in MANIFEST["files"].items():
        assert hashlib.sha256((FIX / name).read_bytes()).hexdigest() == meta["sha256"], name


def test_frame_index_rule():
    assert _frame_index(3200) == 77 and _frame_index(5000) == 120 and _frame_index(1000) == 24
    assert _frame_index(20.8) == 0 and _frame_index(20.9) == 1


def test_landscape_probe_is_cfr_without_audio():
    info = probe_source(FIX / "stomp_landscape.mp4")
    assert (info.width, info.height, info.rotation) == (1280, 720, 0)
    assert (info.display_w, info.display_h) == (1280, 720)
    assert info.codec == "h264" and not info.is_vfr and info.frame_count == 120
    assert info.duration_ms == 5000 and info.start_ms == 0 and abs(info.avg_fps - 24) < 1e-6
    assert not info.has_audio and info.audio_codec is None and info.audio_start_ms is None


def test_rotated_fixture_normalizes_to_portrait(tmp_path):
    src = FIX / "rotated_portrait.mp4"
    info = probe_source(src)
    assert (info.width, info.height) == (1280, 720)
    assert info.rotation == 90 and (info.display_w, info.display_h) == (720, 1280)
    work = tmp_path / "work.mp4"
    pts = normalize_clip(src, work, info, 0, 3000, 720, 1280)
    p = probe(work)
    assert (p["width"], p["height"]) == (720, 1280)
    assert p["frame_count"] == _frame_index(3000) == len(pts.source_pts_ms)
    # display-space hand position matches the GT trajectory (rotation direction is right)
    gt = {round(r["pts_ms"], 3): r for r in MANIFEST["rotated_portrait"]["hand_display_px"]}
    color = MANIFEST["hand_color_rgb"]
    for i, f in enumerate(decode_frames(work)):
        if i % 20:
            continue
        x, y = _hand_xy(f, color)
        r = gt[round(pts.source_pts_ms[i], 3)]
        assert abs(x - r["x"]) < 3 and abs(y - r["y"]) < 3, (i, x, y, r)


def test_vfr_fixture_pts_map(tmp_path):
    src = FIX / "vfr.mp4"
    info = probe_source(src)
    assert info.is_vfr
    work = tmp_path / "work.mp4"
    pts = normalize_clip(src, work, info, 0, 5000, 1280, 720)
    assert isinstance(pts, PtsMap)
    assert len(pts.source_pts_ms) == 120 == probe(work)["frame_count"]
    assert all(b >= a for a, b in zip(pts.source_pts_ms, pts.source_pts_ms[1:]))
    for i, v in enumerate(pts.source_pts_ms):
        assert v <= i * 1000 / 24 + 1e-3


def test_trim_frame_count(tmp_path):
    src = FIX / "stomp_landscape.mp4"
    info = probe_source(src)
    pts = normalize_clip(src, tmp_path / "w.mp4", info, 1000, 3200, 1280, 720)
    assert len(pts.source_pts_ms) == 77 == probe(tmp_path / "w.mp4")["frame_count"]
    assert pts.source_pts_ms[0] == pytest.approx(1000.0)


def _read_code(frame, layout):
    bits = 0
    for b in range(layout["bits"]):
        x0 = layout["x0"] + b * layout["block"]
        cell = frame[layout["y0"] : layout["y0"] + layout["block"], x0 : x0 + layout["block"], 1]
        bits |= int(cell[8:-8, 8:-8].mean() > 128) << b
    return bits


def test_frame_numbered_fixture_maps_nearest_previous_pts(tmp_path):
    src = FIX / "frame_numbers.mp4"
    meta = MANIFEST["frame_numbers"]
    info = probe_source(src)
    src_pts = meta["pts_ms"]
    assert info.frame_count == len(src_pts)
    start, dur = 700, 2000
    work = tmp_path / "w.mp4"
    pts = normalize_clip(src, work, info, start, dur, info.display_w, info.display_h)
    seen = 0
    for i, f in enumerate(decode_frames(work)):
        target = info.start_ms + start + i * 1000 / 24
        k = bisect.bisect_right(src_pts, target + 1e-3) - 1
        assert _read_code(f, meta["code_layout"]) == k, i
        frame_dur = src_pts[k + 1] - src_pts[k]
        assert -1e-3 <= target - pts.source_pts_ms[i] < frame_dur + 1e-3
        seen += 1
    assert seen == _frame_index(dur)


def _onset_ms(pcm, thresh=0.05):
    idx = np.nonzero(np.abs(pcm) > thresh)[0]
    return idx[0] / 48.0 if len(idx) else None


@pytest.mark.parametrize("start", [0, 120, 500])
def test_extract_audio_trims_and_aligns_tone(tmp_path, start):
    src = FIX / "with_audio.mp4"
    info = probe_source(src)
    assert info.has_audio and info.audio_codec == "aac" and info.audio_start_ms is not None
    out = tmp_path / "a.m4a"
    assert extract_audio(src, out, info, start, 3000) is True
    s = _audio_duration_ms(out)
    assert s["codec_name"] == "aac" and int(s["sample_rate"]) == 48000 and int(s["channels"]) == 2
    assert abs(float(s["duration"]) * 1000 - 3000) <= 1000 / 24
    pcm = _pcm(out)
    assert abs(len(pcm) / 48.0 - 3000) <= 1000 / 24
    expected = max(0, MANIFEST["with_audio"]["tone_onset_ms"] - start)
    assert abs(_onset_ms(pcm) - expected) <= 5  # measured < 1 ms


def test_extract_audio_silent_clip_returns_false(tmp_path):
    src = FIX / "stomp_landscape.mp4"
    out = tmp_path / "a.m4a"
    assert extract_audio(src, out, probe_source(src), 0, 2000) is False
    assert not out.exists()


def test_two_video_streams_rejected(tmp_path):
    p = tmp_path / "two.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=s=64x64:r=24:d=1", "-f", "lavfi", "-i", "testsrc=s=32x32:r=24:d=1",
            "-map", "0:v", "-map", "1:v", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p))
    with pytest.raises(MediaError) as e:
        probe_source(p)
    assert e.value.code == "multiple_video_streams"


def test_limits_and_garbage_rejected(tmp_path):
    long_clip = tmp_path / "long.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=gray:s=16x16:r=2:d=61", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(long_clip))
    with pytest.raises(MediaError) as e:
        probe_source(long_clip)
    assert e.value.code == "too_long"
    wide = tmp_path / "wide.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=gray:s=4112x16:r=24:d=0.1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(wide))
    with pytest.raises(MediaError) as e:
        probe_source(wide)
    assert e.value.code == "too_large_dims"
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video at all" * 100)
    with pytest.raises(MediaError) as e:
        probe_source(junk)
    assert e.value.code == "unsupported_codec"


def test_cover_transform_and_proxy_size():
    assert cover_transform(1280, 720, 1280, 720) == {"scale": 1.0, "offset_x": 0.0, "offset_y": 0.0}
    t = cover_transform(1280, 720, 720, 1280)  # landscape source into portrait canvas: crop the sides
    assert t["scale"] == pytest.approx(1280 / 720)
    assert t["offset_x"] == pytest.approx((round(1280 * 1280 / 720) - 720) // 2) and t["offset_y"] == 0
    assert proxy_size(1280, 720) == (640, 360) and proxy_size(720, 1280) == (360, 640)


def test_proxy_filmstrip_first_frame(tmp_path):
    src = FIX / "stomp_landscape.mp4"
    info = probe_source(src)
    work = tmp_path / "w.mp4"
    normalize_clip(src, work, info, 0, 2000, 1280, 720)
    make_proxy(work, tmp_path / "p.mp4")
    p = probe(tmp_path / "p.mp4")
    assert (p["width"], p["height"]) == (640, 360) and p["frame_count"] == 48 and p["fps"] == 24
    idx = filmstrip(work, tmp_path / "strip.png", n=12, thumb_h=90)
    assert len(idx) == 12 and idx[0] == 0 and idx[-1] == 47 and idx == sorted(idx)
    assert Image.open(tmp_path / "strip.png").size == (12 * 160, 90)
    first_frame_png(work, tmp_path / "first.png")
    assert Image.open(tmp_path / "first.png").size == (1280, 720)
