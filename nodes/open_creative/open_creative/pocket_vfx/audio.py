"""Pocket VFX audio plan and mux (spec §12): the trimmed original audio kept in sync, an optional bundled SFX aligned
to the effect peak, mixed with a limiter and muxed into the render MP4 (video stream copied).

Every FFmpeg argument is fixed text; the only values that enter a filter string are validated numbers (integer
milliseconds, a dB gain bounded -30..0, durations) formatted as numbers.
"""

from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from open_creative.media.video import FFMPEG

from .presets import presets_path
from .timeline import FPS, EffectWindow, frame_count

RATE = 48_000
FADE_IN_MS = 10
FADE_OUT_MS = 120
LIMIT = 0.89
GAIN_RANGE_DB = (-30.0, 0.0)
PEAK_WINDOW_MS = 10


@dataclass(frozen=True)
class AudioPlan:
    original: Path | None
    sfx: Path | None
    sfx_delay_ms: int
    sfx_gain_db: float
    duration_ms: int
    sfx_trim_ms: int = 0  # head of the SFX cut off when the peak lands earlier than the SFX's own peak offset


def energy_peak_ms(samples: np.ndarray, rate: int = RATE) -> int:
    """Centre (ms) of the loudest 10 ms window of a mono signal (windows advance 1 ms); channels are averaged."""
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1)
    win, hop = rate * PEAK_WINDOW_MS // 1000, rate // 1000
    if len(x) < win:
        return 0
    c = np.concatenate([[0.0], np.cumsum(x * x)])
    starts = np.arange(0, len(x) - win + 1, hop)
    return round((starts[int(np.argmax(c[starts + win] - c[starts]))] + win / 2) * 1000 / rate)


def sfx_path(preset: dict) -> Path | None:
    """Repo file of the preset's bundled SFX (the path comes from the presets JSON, never from user input)."""
    sfx = preset.get("sfx")
    return presets_path().parent / sfx["file"] if sfx else None


def plan_audio(cfg, preset: dict, window: EffectWindow, source_audio: Path | None) -> AudioPlan | None:
    """``None``: the render has no audio stream. ``cfg``: a PocketVfxConfig; ``source_audio``: the trimmed AAC."""
    original = source_audio if cfg.audio.keep_original and source_audio is not None else None
    sfx = sfx_path(preset) if cfg.audio.sfx_enabled else None
    if original is None and sfx is None:
        return None
    gain = float(cfg.audio.sfx_gain_db)
    if not GAIN_RANGE_DB[0] <= gain <= GAIN_RANGE_DB[1]:
        raise ValueError("sfx_gain_db out of range")
    peak_ms = round(window.peak * 1000 / FPS)
    start = peak_ms - int(preset["sfx"]["peak_offset_ms"]) if sfx else 0
    return AudioPlan(original, sfx, max(0, start), gain if sfx else 0.0,
                     round(frame_count(cfg.trim.duration_ms) * 1000 / FPS), max(0, -start))


def _seconds(ms: int) -> str:
    return f"{int(ms) / 1000:.3f}"


def _wav_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() * 1000 / w.getframerate())


FORMAT = f"aformat=sample_fmts=fltp:sample_rates={RATE}:channel_layouts=stereo"


def filter_graph(plan: AudioPlan, sfx_ms: int = 0) -> tuple[str, int]:
    """``(filter_complex, input count)``; the audio inputs are numbered from 1 (input 0 is the video)."""
    chains, labels, i = [], [], 1
    if plan.original is not None:
        chains.append(f"[{i}:a]{FORMAT}[o]")
        labels.append("[o]")
        i += 1
    if plan.sfx is not None:
        keep = max(0, sfx_ms - plan.sfx_trim_ms)
        out_at = max(0, keep - FADE_OUT_MS)
        chains.append(
            f"[{i}:a]{FORMAT},atrim=start={_seconds(plan.sfx_trim_ms)},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={_seconds(FADE_IN_MS)},afade=t=out:st={_seconds(out_at)}:d={_seconds(FADE_OUT_MS)},"
            f"volume={plan.sfx_gain_db:.2f}dB,adelay={int(plan.sfx_delay_ms)}:all=1[s]")
        labels.append("[s]")
    dur = _seconds(plan.duration_ms)
    head = (f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0"
            if len(labels) > 1 else f"{labels[0]}anull")
    chains.append(f"{head},apad=whole_dur={dur},atrim=end={dur},asetpts=PTS-STARTPTS,"
                  f"alimiter=limit={LIMIT}:level=disabled[a]")
    return ";".join(chains), len(labels)


def mux(video_mp4: str | Path, plan: AudioPlan, out_mp4: str | Path) -> None:
    """Video stream copied, AAC 160k audio padded/trimmed to the exact video duration."""
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_mp4)]
    if plan.original is not None:
        cmd += ["-i", str(plan.original)]
    if plan.sfx is not None:
        cmd += ["-i", str(plan.sfx)]
    graph, _ = filter_graph(plan, _wav_ms(plan.sfx) if plan.sfx is not None else 0)
    cmd += ["-filter_complex", graph, "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-ar", str(RATE), "-shortest", "-movflags", "+faststart", str(out_mp4)]
    out = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
    if out.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed: {out.stderr.decode(errors='replace')[-500:]}")
