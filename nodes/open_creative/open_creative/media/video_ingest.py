"""User-video ingest: probe (rotation, VFR, limits), PTS-mapped CFR 24 fps normalization,
proxy, filmstrip/first frame and trimmed audio.

Time origin: ``source_start_ms`` is measured from the first video frame (``SourceInfo.start_ms``),
so the absolute source PTS of work frame ``i`` is ``start_ms + source_start_ms + i * 1000 / 24``.
Rotation is the clockwise angle the player applies (the legacy ``rotate`` tag convention; a
phone portrait clip is 90).

Commands are built from fixed argument lists; only numbers formatted from validated values
reach FFmpeg arguments or filter strings.
"""

from __future__ import annotations

import bisect
import json
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..pocket_vfx.timeline import frame_index
from .image import MediaError
from .video import FFMPEG, FFPROBE, Mp4Writer, cover_fit, decode_frames, probe

FPS = 24
MAX_DURATION_MS = 60_000
MAX_DIM = 4096
AUDIO_RATE = 48_000
WORK_CRF = 16
PROXY_CRF = 20
_PTS_EPS_MS = 1e-3


@dataclass(frozen=True)
class SourceInfo:
    width: int  # coded (stored) size
    height: int
    rotation: int  # 0/90/180/270, clockwise, applied by ffmpeg autorotate
    display_w: int  # size after rotation
    display_h: int
    codec: str
    duration_ms: int
    start_ms: int  # first video frame PTS
    is_vfr: bool
    frame_count: int
    avg_fps: float
    has_audio: bool
    audio_codec: str | None
    audio_start_ms: int | None


@dataclass(frozen=True)
class PtsMap:
    source_pts_ms: list[float]  # index = work frame, value = source frame PTS shown there


def _run_json(cmd: list[str], timeout: int = 120) -> dict:
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise MediaError("unsupported_codec", "probe timed out") from e
    if out.returncode != 0:
        raise MediaError("unsupported_codec", out.stderr.decode(errors="replace")[-300:])
    try:
        return json.loads(out.stdout or b"{}")
    except json.JSONDecodeError as e:
        raise MediaError("unsupported_codec", "unreadable probe output") from e


def _rotation(stream: dict) -> int:
    deg = None
    for sd in stream.get("side_data_list") or []:
        if sd.get("side_data_type") == "Display Matrix" and "rotation" in sd:
            deg = -float(sd["rotation"])  # ffprobe reports counter-clockwise degrees
    if deg is None and "rotate" in (stream.get("tags") or {}):
        deg = float(stream["tags"]["rotate"])
    if deg is None:
        return 0
    return round(deg / 90.0) * 90 % 360


def _frame_pts_ms(path: str | Path) -> list[float]:
    """Decoded video frame PTS (ms, presentation order) of the main video stream."""
    data = _run_json([
        FFPROBE, "-v", "error", "-select_streams", "V:0",
        "-show_entries", "frame=pts_time,best_effort_timestamp_time", "-of", "json", str(path),
    ], timeout=300)
    pts = []
    for fr in data.get("frames", []):
        t = fr.get("pts_time", fr.get("best_effort_timestamp_time"))
        if t not in (None, "N/A"):
            pts.append(float(t) * 1000.0)
    return sorted(pts)


def _ms(value) -> int | None:
    if value in (None, "N/A"):
        return None
    return round(float(value) * 1000)


def probe_source(path: str | Path) -> SourceInfo:
    data = _run_json([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    streams = data.get("streams", [])
    videos = [s for s in streams
              if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if not videos or not videos[0].get("codec_name") or not videos[0].get("width"):
        raise MediaError("unsupported_codec", "no decodable video stream")
    if len(videos) > 1:
        raise MediaError("multiple_video_streams", f"found {len(videos)} video streams")
    v = videos[0]
    w, h = int(v["width"]), int(v["height"])
    if max(w, h) > MAX_DIM:
        raise MediaError("too_large_dims", f"{w}x{h} exceeds {MAX_DIM}")
    duration_ms = _ms(v.get("duration")) or _ms((data.get("format") or {}).get("duration"))
    if duration_ms is not None and duration_ms > MAX_DURATION_MS:
        raise MediaError("too_long", f"{duration_ms} ms exceeds {MAX_DURATION_MS} ms")
    pts = _frame_pts_ms(path)
    if not pts:
        raise MediaError("unsupported_codec", "no decodable video frames")
    deltas = np.diff(pts)
    is_vfr = bool(len(deltas) > 1 and float(deltas.max() - deltas.min()) > 1.0 + 1e-6)
    raw_rate = v.get("avg_frame_rate") or "0/1"
    rate = Fraction(0) if raw_rate.endswith("/0") else Fraction(raw_rate)
    if duration_ms is None:
        step = float(np.median(deltas)) if len(deltas) else 1000 / FPS
        duration_ms = round(pts[-1] - pts[0] + step)
        if duration_ms > MAX_DURATION_MS:
            raise MediaError("too_long", f"{duration_ms} ms exceeds {MAX_DURATION_MS} ms")
    avg_fps = float(rate) if rate else len(pts) * 1000.0 / max(duration_ms, 1)
    rotation = _rotation(v)
    dw, dh = (h, w) if rotation in (90, 270) else (w, h)
    a = audios[0] if audios else None
    return SourceInfo(
        width=w, height=h, rotation=rotation, display_w=dw, display_h=dh, codec=v["codec_name"],
        duration_ms=duration_ms, start_ms=round(pts[0]), is_vfr=is_vfr, frame_count=len(pts),
        avg_fps=avg_fps, has_audio=a is not None, audio_codec=a.get("codec_name") if a else None,
        audio_start_ms=(_ms(a.get("start_time")) or 0) if a else None,
    )


def _select_frames(pts: list[float], start_abs_ms: float, n: int) -> list[int]:
    """For each work frame, the source frame with the greatest PTS <= its time (nearest
    previous); times before the first frame take the first frame."""
    out = []
    for i in range(n):
        t = start_abs_ms + i * 1000.0 / FPS
        out.append(max(0, bisect.bisect_right(pts, t + _PTS_EPS_MS) - 1))
    return out


def _decode_display_rgb(src: str | Path, w: int, h: int) -> Iterator[np.ndarray]:
    """Every decoded frame of the main video stream, rotation applied, in presentation order."""
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(src), "-map", "0:V:0",
           "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
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


def normalize_clip(
    src: str | Path, out_path: str | Path, info: SourceInfo, source_start_ms: int, duration_ms: int,
    out_w: int, out_h: int,
) -> PtsMap:
    """Rotated, CFR 24 fps, cover-fit work clip of exactly ``frame_index(duration_ms)`` frames.
    Streams decode -> select -> fit -> encode; at most one source frame is held at a time."""
    if source_start_ms < 0 or duration_ms <= 0:
        raise ValueError("invalid trim")
    n = frame_index(duration_ms)
    pts = _frame_pts_ms(src)
    if not pts:
        raise MediaError("unsupported_codec", "no decodable video frames")
    sel = _select_frames(pts, info.start_ms + source_start_ms, n)
    written = 0
    with Mp4Writer(out_path, out_w, out_h, fps=FPS, crf=WORK_CRF) as writer:
        for k, frame in enumerate(_decode_display_rgb(src, info.display_w, info.display_h)):
            if sel[written] != k:
                continue
            fitted = cover_fit(frame, out_w, out_h)
            while written < n and sel[written] == k:
                writer.write(fitted)
                written += 1
            if written == n:
                break
        if written != n:
            raise MediaError("unsupported_codec", f"decoded fewer frames than probed ({written}/{n})")
    return PtsMap(source_pts_ms=[pts[k] for k in sel])


def cover_transform(src_w: int, src_h: int, out_w: int, out_h: int) -> dict:
    """Source px -> output px mapping used by ``cover_fit``: ``out = src * scale - offset``."""
    scale = max(out_w / src_w, out_h / src_h)
    nw, nh = max(out_w, round(src_w * scale)), max(out_h, round(src_h * scale))
    return {"scale": float(scale), "offset_x": float((nw - out_w) // 2), "offset_y": float((nh - out_h) // 2)}


def proxy_size(out_w: int, out_h: int) -> tuple[int, int]:
    return (640, 360) if out_w >= out_h else (360, 640)


def _ffmpeg(args: list[str], timeout: int = 600) -> None:
    out = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
                         capture_output=True, timeout=timeout, check=False)
    if out.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {out.stderr.decode(errors='replace')[-500:]}")


def make_proxy(work_path: str | Path, proxy_path: str | Path) -> None:
    info = probe(work_path)
    pw, ph = proxy_size(info["width"], info["height"])
    _ffmpeg(["-i", str(work_path), "-map", "0:v:0", "-vf", f"scale={pw}:{ph}:flags=area",
             "-fps_mode", "passthrough", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-crf", str(PROXY_CRF), "-preset", "medium", "-movflags", "+faststart", str(proxy_path)])


def filmstrip(work_path: str | Path, out_png: str | Path, n: int = 12, thumb_h: int = 90) -> list[int]:
    """One horizontal PNG of ``n`` evenly spaced thumbnails; returns their frame indices."""
    count = probe(work_path)["frame_count"]
    if count <= 0:
        raise MediaError("unsupported_codec", "empty work clip")
    idx = [round(k * (count - 1) / (n - 1)) for k in range(n)] if n > 1 else [0]
    wanted = set(idx)
    thumbs: dict[int, np.ndarray] = {}
    for i, f in enumerate(decode_frames(work_path)):
        if i in wanted:
            h, w = f.shape[:2]
            tw = max(1, round(thumb_h * w / h))
            thumbs[i] = cv2.resize(f, (tw, thumb_h), interpolation=cv2.INTER_AREA)
        if i >= idx[-1]:
            break
    strip = np.concatenate([thumbs[i] for i in idx], axis=1)
    Image.fromarray(strip, "RGB").save(out_png)
    return idx


def first_frame_png(work_path: str | Path, out_png: str | Path) -> None:
    for f in decode_frames(work_path):
        Image.fromarray(f, "RGB").save(out_png)
        return
    raise MediaError("unsupported_codec", "empty work clip")


def extract_audio(
    src: str | Path, out_m4a: str | Path, info: SourceInfo, source_start_ms: int, duration_ms: int,
) -> bool:
    """Trimmed AAC 48 kHz stereo aligned to the work clip; False (no file written) without audio.
    Audio that starts after the trim start is padded with leading silence; a short tail is padded
    so the result is exactly ``duration_ms`` long."""
    if not info.has_audio:
        return False
    # Where the audio stream begins relative to the trim start (positive: pad, negative: skip).
    offset_ms = (info.audio_start_ms or 0) - (info.start_ms + source_start_ms)
    skip = max(0, round(-offset_ms * AUDIO_RATE / 1000))
    pad = max(0, round(offset_ms * AUDIO_RATE / 1000))
    total = round(duration_ms * AUDIO_RATE / 1000)
    chain = [f"aresample={AUDIO_RATE}", "asetpts=PTS-STARTPTS",
             f"atrim=start_sample={skip}", "asetpts=PTS-STARTPTS"]
    if pad:
        chain.append(f"adelay={pad}S:all=1")
    chain += ["apad", f"atrim=end_sample={total}"]
    _ffmpeg(["-i", str(src), "-map", "0:a:0", "-vn", "-sn", "-dn", "-af", ",".join(chain),
             "-ac", "2", "-ar", str(AUDIO_RATE), "-c:a", "aac", "-b:a", "160k",
             "-movflags", "+faststart", str(out_m4a)])
    return True
