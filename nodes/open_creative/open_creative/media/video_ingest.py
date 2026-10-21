"""User-video ingest: probe (rotation, VFR, limits), PTS-mapped CFR 24 fps normalization,
proxy, filmstrip/first frame and trimmed audio.

Time origin: ``source_start_ms`` is measured from the first video frame (``SourceInfo.start_ms``),
so the absolute source PTS of work frame ``i`` is ``start_ms + source_start_ms + i * 1000 / 24``.
Rotation is the clockwise angle the player applies (the legacy ``rotate`` tag convention; a
phone portrait clip is 90).

Commands are built from fixed argument lists; only numbers formatted from validated values
reach FFmpeg arguments or filter strings.

Long per-frame passes (the PTS probe and the decode) call an optional ``on_frame(i)`` with a running
counter so a worker can heartbeat its lease and honour cancels while a 60 s 4K clip decodes.
"""

from __future__ import annotations

import bisect
import json
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..pocket_vfx.timeline import frame_index
from .image import MediaError
from .video import FFMPEG, FFPROBE, Mp4Writer, decode_frames, probe

FPS = 24
MAX_DURATION_MS = 60_000
MAX_DIM = 4096
AUDIO_RATE = 48_000
WORK_CRF = 16
PROXY_CRF = 20
_PTS_EPS_MS = 1e-3
PROBE_TIMEOUT_S = 300
SEEK_MARGIN_MS = 2000  # decode starts at least this far before the trim start (keyframe-safe; PTS chosen in Python)

OnFrame = Callable[[int], None]


def _ticker(on_frame: OnFrame | None) -> Callable[[], None]:
    """A no-arg tick that calls ``on_frame`` with 0, 1, 2, ... across every pass it is shared by."""
    count = 0

    def tick() -> None:
        nonlocal count
        if on_frame is not None:
            on_frame(count)
        count += 1

    return tick


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


def _frame_pts_ms(
    path: str | Path, tick: Callable[[], None] | None = None, read_ms: tuple[float, float] | None = None,
) -> list[float]:
    """Decoded video frame PTS (ms, presentation order) of the main video stream, streamed one frame per line
    (``tick`` per frame). ``read_ms`` = absolute (from, to) limits the pass to that interval (ffprobe starts at the
    keyframe at or before ``from``)."""
    cmd = [FFPROBE, "-v", "error", "-select_streams", "V:0"]
    if read_ms is not None:
        cmd += ["-read_intervals", f"{read_ms[0] / 1000:.6f}%{read_ms[1] / 1000:.6f}"]
    cmd += ["-show_entries", "frame=pts_time,best_effort_timestamp_time", "-of", "compact=p=0", str(path)]
    deadline = time.monotonic() + PROBE_TIMEOUT_S
    pts = []
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
        try:
            for raw in proc.stdout:
                if time.monotonic() > deadline:
                    raise MediaError("unsupported_codec", "probe timed out")
                fields = dict(kv.split("=", 1) for kv in raw.decode(errors="replace").strip().split("|") if "=" in kv)
                t = fields.get("pts_time")
                if t in (None, "N/A"):
                    t = fields.get("best_effort_timestamp_time")
                if t in (None, "N/A"):
                    continue
                pts.append(float(t) * 1000.0)
                if tick is not None:
                    tick()
            proc.wait(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as e:
            raise MediaError("unsupported_codec", "probe timed out") from e
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if proc.returncode != 0:
            err.seek(0)
            raise MediaError("unsupported_codec", err.read().decode(errors="replace")[-300:])
    return sorted(pts)


def _ms(value) -> int | None:
    if value in (None, "N/A"):
        return None
    return round(float(value) * 1000)


def probe_source(path: str | Path, on_frame: OnFrame | None = None) -> SourceInfo:
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
    pts = _frame_pts_ms(path, _ticker(on_frame))
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


def _cover_geometry(src_w: int, src_h: int, out_w: int, out_h: int) -> tuple[float, int, int, int, int]:
    """``cover_fit``'s geometry: (scale, resized w, resized h, crop x0, crop y0)."""
    scale = max(out_w / src_w, out_h / src_h)
    nw, nh = max(out_w, round(src_w * scale)), max(out_h, round(src_h * scale))
    return scale, nw, nh, (nw - out_w) // 2, (nh - out_h) // 2


def _cover_filter(src_w: int, src_h: int, out_w: int, out_h: int) -> str | None:
    """The decode-side equivalent of ``cover_fit`` (area downscale / bilinear upscale, then the centre crop), built
    only from integers derived from probed and validated sizes. ``None`` when no resize is needed."""
    if (src_w, src_h) == (out_w, out_h):
        return None
    scale, nw, nh, x0, y0 = _cover_geometry(src_w, src_h, out_w, out_h)
    flags = "area" if scale < 1 else "bilinear"
    return f"scale={int(nw)}:{int(nh)}:flags={flags},format=rgb24,crop={int(out_w)}:{int(out_h)}:{int(x0)}:{int(y0)}"


def _decode_display_rgb(
    src: str | Path, w: int, h: int, vf: str | None = None, seek_ms: float | None = None,
) -> Iterator[np.ndarray]:
    """Decoded frames of the main video stream, rotation applied, in presentation order, ``w``x``h`` after ``vf``.
    ``seek_ms`` (absolute PTS) starts at the first frame with PTS >= it (accurate input seek from the keyframe
    before it)."""
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error"]
    if seek_ms is not None:
        cmd += ["-seek_timestamp", "1", "-ss", f"{seek_ms / 1000:.6f}"]
    cmd += ["-i", str(src), "-map", "0:V:0"]
    if vf is not None:
        cmd += ["-vf", vf]
    cmd += ["-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
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


def _seek_plan(src: str | Path, info: SourceInfo, start_abs_ms: float, end_abs_ms: float,
               tick: Callable[[], None]) -> tuple[list[float], float | None]:
    """Source PTS to select from and the decoder seek point. A trim starting more than ``SEEK_MARGIN_MS`` after the
    first frame probes only from a keyframe before ``start - margin`` and seeks to the midpoint between the last frame
    before ``start - margin`` and the next one (unambiguous for ffmpeg's accurate seek); otherwise, or when the
    windowed probe does not cover that point, the whole stream from its first frame (no seek)."""
    target = start_abs_ms - SEEK_MARGIN_MS
    if target > info.start_ms:
        pts = _frame_pts_ms(src, tick, (max(0.0, target - 1000.0), end_abs_ms + 1000.0))
        j0 = bisect.bisect_left(pts, target)
        if 1 <= j0 < len(pts) and pts[j0] - pts[j0 - 1] > 0.01:
            return pts[j0:], (pts[j0 - 1] + pts[j0]) / 2
    return _frame_pts_ms(src, tick), None


def normalize_clip(
    src: str | Path, out_path: str | Path, info: SourceInfo, source_start_ms: int, duration_ms: int,
    out_w: int, out_h: int, on_frame: OnFrame | None = None,
) -> PtsMap:
    """Rotated, CFR 24 fps, cover-fit work clip of exactly ``frame_index(duration_ms)`` frames.
    Streams decode (scaled and cropped by ffmpeg) -> select -> encode; at most one source frame is held at a time.
    ``on_frame`` is called with a running counter for every probed and every decoded frame."""
    if source_start_ms < 0 or duration_ms <= 0:
        raise ValueError("invalid trim")
    n = frame_index(duration_ms)
    tick = _ticker(on_frame)
    start_abs = info.start_ms + source_start_ms
    pts, seek_ms = _seek_plan(src, info, start_abs, start_abs + duration_ms, tick)
    if not pts:
        raise MediaError("unsupported_codec", "no decodable video frames")
    sel = _select_frames(pts, start_abs, n)
    vf = _cover_filter(info.display_w, info.display_h, out_w, out_h)
    written = 0
    with Mp4Writer(out_path, out_w, out_h, fps=FPS, crf=WORK_CRF) as writer:
        for k, frame in enumerate(_decode_display_rgb(src, out_w, out_h, vf, seek_ms)):
            tick()
            if sel[written] != k:
                continue
            while written < n and sel[written] == k:
                writer.write(frame)
                written += 1
            if written == n:
                break
        if written != n:
            raise MediaError("unsupported_codec", f"decoded fewer frames than probed ({written}/{n})")
    return PtsMap(source_pts_ms=[pts[k] for k in sel])


def cover_transform(src_w: int, src_h: int, out_w: int, out_h: int) -> dict:
    """Source px -> output px mapping used by ``cover_fit``: ``out = src * scale - offset``."""
    scale, _, _, x0, y0 = _cover_geometry(src_w, src_h, out_w, out_h)
    return {"scale": float(scale), "offset_x": float(x0), "offset_y": float(y0)}


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
