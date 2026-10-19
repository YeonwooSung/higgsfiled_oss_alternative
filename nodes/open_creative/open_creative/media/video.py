"""FFmpeg-backed video IO. Frames stream through pipes; no full-sequence tensors.

Commands are built from fixed argument lists only; no user-provided strings are
ever placed into FFmpeg arguments or filters.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable, Iterator
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


def encode_mp4(
    frames: Iterable[np.ndarray], path: str | Path, w: int, h: int, fps: int = 24, crf: int = 18
) -> int:
    """Encode RGB uint8 frames to H.264 yuv420p MP4. Returns the number of frames written."""
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{int(w)}x{int(h)}", "-r", str(int(fps)),
        "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(int(crf)),
        "-preset", "medium", "-movflags", "+faststart", "-r", str(int(fps)),
        str(path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    count = 0
    try:
        for f in frames:
            if f.shape != (h, w, 3) or f.dtype != np.uint8:
                raise ValueError(f"frame {count} has shape {f.shape}, expected {(h, w, 3)} uint8")
            proc.stdin.write(np.ascontiguousarray(f).tobytes())
            count += 1
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    proc.stdin.close()
    err = proc.stderr.read()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg encode failed: {err.decode(errors='replace')[-500:]}")
    return count


def probe(path: str | Path) -> dict:
    cmd = [
        FFPROBE, "-v", "error", "-select_streams", "v", "-count_frames",
        "-show_entries", "stream=codec_name,pix_fmt,width,height,avg_frame_rate,r_frame_rate,nb_read_frames,duration",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, check=True, timeout=120)
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    if len(streams) != 1:
        raise ValueError(f"expected exactly one video stream, found {len(streams)}")
    s = streams[0]
    rate = Fraction(s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1")
    frame_count = int(s.get("nb_read_frames") or 0)
    duration_ms = round(frame_count / rate * 1000) if rate else None
    return {
        "codec": s.get("codec_name"),
        "pix_fmt": s.get("pix_fmt"),
        "width": int(s["width"]),
        "height": int(s["height"]),
        "fps": float(rate) if rate.denominator != 1 else int(rate),
        "fps_num": rate.numerator,
        "fps_den": rate.denominator,
        "frame_count": frame_count,
        "duration_ms": duration_ms,
    }


def decode_frames(path: str | Path) -> Iterator[np.ndarray]:
    info = probe(path)
    w, h = info["width"], info["height"]
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def cover_fit(frame: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if (w, h) == (out_w, out_h):
        return frame
    scale = max(out_w / w, out_h / h)
    nw, nh = max(out_w, round(w * scale)), max(out_h, round(h * scale))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(frame, (nw, nh), interpolation=interp)
    x0, y0 = (nw - out_w) // 2, (nh - out_h) // 2
    return np.ascontiguousarray(resized[y0 : y0 + out_h, x0 : x0 + out_w])


def fit_sequence(frames: Iterable[np.ndarray], out_w: int, out_h: int, n: int = 120) -> Iterator[np.ndarray]:
    """Cover-fit every frame to the output size and return exactly `n` frames
    (extra frames trimmed, short sequences hold the last frame)."""
    last = None
    count = 0
    for f in frames:
        if count >= n:
            break
        last = cover_fit(f, out_w, out_h)
        count += 1
        yield last
    if last is None:
        raise ValueError("background sequence is empty")
    while count < n:
        count += 1
        yield last
