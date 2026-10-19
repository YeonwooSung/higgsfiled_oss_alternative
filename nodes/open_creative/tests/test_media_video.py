import numpy as np
import pytest

from open_creative.media.video import decode_frames, encode_mp4, fit_sequence, probe


def frames(n, w, h):
    for i in range(n):
        f = np.zeros((h, w, 3), np.uint8)
        f[..., 0] = (i * 10) % 256
        f[:, : w // 2, 1] = 200
        yield f


def test_encode_then_probe(tmp_path):
    p = tmp_path / "a.mp4"
    encode_mp4(frames(12, 64, 32), p, 64, 32, fps=24)
    info = probe(p)
    assert info["codec"] == "h264" and info["pix_fmt"] == "yuv420p"
    assert (info["width"], info["height"]) == (64, 32)
    assert info["fps"] == 24 and info["frame_count"] == 12
    assert info["duration_ms"] == 500


def test_decode_frames_roundtrip(tmp_path):
    p = tmp_path / "a.mp4"
    encode_mp4(frames(5, 64, 32), p, 64, 32)
    out = list(decode_frames(p))
    assert len(out) == 5 and out[0].shape == (32, 64, 3)
    assert abs(int(out[0][10, 5, 1]) - 200) < 6


def test_encode_rejects_wrong_frame_size(tmp_path):
    with pytest.raises(ValueError):
        encode_mp4(frames(2, 10, 10), tmp_path / "x.mp4", 64, 32)


def test_fit_sequence_cover_crops_and_trims():
    out = list(fit_sequence(frames(121, 768, 512), 720, 1280, n=120))
    assert len(out) == 120
    assert out[0].shape == (1280, 720, 3)


def test_fit_sequence_holds_last_frame_when_short():
    out = list(fit_sequence(frames(3, 64, 64), 32, 32, n=10))
    assert len(out) == 10
    assert np.array_equal(out[2], out[9])


def test_fit_sequence_empty_raises():
    with pytest.raises(ValueError):
        list(fit_sequence(iter([]), 32, 32, n=10))
