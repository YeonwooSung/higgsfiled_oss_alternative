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
from typing import Self

import cv2
import numpy as np

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


class Mp4Writer:
    """Incremental H.264 yuv420p encoder (RGB uint8 frames in). Several writers can be fed from one
    streaming pass. Use as a context manager: an exception aborts (kills FFmpeg), a clean exit finalizes.
    ``close()`` / ``abort()`` are idempotent. If FFmpeg exits early, ``write``/``close`` raise
    ``RuntimeError`` carrying FFmpeg's stderr (not a bare ``BrokenPipeError``)."""

    def __init__(self, path: str | Path, w: int, h: int, fps: int = 24, crf: int = 18):
        self.w, self.h, self.count = int(w), int(h), 0
        self._done = False
        cmd = [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{self.w}x{self.h}", "-r", str(int(fps)),
            "-i", "pipe:0",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(int(crf)),
            "-preset", "medium", "-movflags", "+faststart", "-r", str(int(fps)),
            str(path),
        ]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def _failed(self) -> RuntimeError:
        self._done = True
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        err = self._proc.stderr.read()
        code = self._proc.wait()
        return RuntimeError(f"ffmpeg encode failed (exit {code}): {err.decode(errors='replace')[-500:]}")

    def write(self, f: np.ndarray) -> None:
        if self._done:
            raise RuntimeError("writer is closed")
        if f.shape != (self.h, self.w, 3) or f.dtype != np.uint8:
            raise ValueError(f"frame {self.count} has shape {f.shape}, expected {(self.h, self.w, 3)} uint8")
        try:
            self._proc.stdin.write(np.ascontiguousarray(f).tobytes())
        except (BrokenPipeError, OSError) as e:
            raise self._failed() from e
        self.count += 1

    def close(self) -> int:
        if self._done:
            return self.count
        self._done = True
        try:
            self._proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        err = self._proc.stderr.read()
        if self._proc.wait() != 0:
            raise RuntimeError(f"ffmpeg encode failed: {err.decode(errors='replace')[-500:]}")
        return self.count

    def abort(self) -> None:
        if self._done and self._proc.poll() is not None:
            return
        self._done = True
        self._proc.kill()
        self._proc.wait()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()


def encode_mp4(
    frames: Iterable[np.ndarray], path: str | Path, w: int, h: int, fps: int = 24, crf: int = 18
) -> int:
    """Encode RGB uint8 frames to H.264 yuv420p MP4. Returns the number of frames written."""
    with Mp4Writer(path, w, h, fps, crf) as writer:
        for f in frames:
            writer.write(f)
    return writer.count


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
