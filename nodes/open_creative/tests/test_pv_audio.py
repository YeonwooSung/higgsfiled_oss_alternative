"""Audio plan and mux (spec §12): original audio kept in sync, SFX aligned to the effect peak, limiter, no clipping."""

import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from open_creative.media.video import FFMPEG, probe
from open_creative.media.video_ingest import extract_audio, probe_source
from open_creative.pocket_vfx.audio import (
    FADE_IN_MS,
    RATE,
    AudioPlan,
    energy_peak_ms,
    filter_graph,
    mux,
    plan_audio,
    sfx_path,
)
from open_creative.pocket_vfx.presets import load_presets
from open_creative.pocket_vfx.timeline import EffectWindow

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "pocket-vfx"
FRAME_MS = 1000 / 24
PEAK_FRAME = 60  # 2500 ms


def cfg(keep=True, sfx=False, gain=-9.0, duration_ms=4000):
    return SimpleNamespace(audio=SimpleNamespace(keep_original=keep, sfx_enabled=sfx, sfx_gain_db=gain),
                           trim=SimpleNamespace(duration_ms=duration_ms))


WINDOW = EffectWindow(40, PEAK_FRAME, 90)


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """The with_audio fixture cut to 4 s (96 frames, silent) + its trimmed AAC."""
    d = tmp_path_factory.mktemp("audio")
    src = FIX / "with_audio.mp4"
    video, audio = d / "v.mp4", d / "a.m4a"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "0", "-i", str(src), "-t", "4", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(video)], check=True)
    assert extract_audio(src, audio, probe_source(src), 0, 4000)
    return video, audio


def decode(path):
    out = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "f32le", "-ac", "1",
                          "-ar", str(RATE), "pipe:1"], capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype="<f4")


def streams(path, kind="a"):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", kind, "-show_entries", "stream=codec_name",
                          "-of", "csv=p=0", str(path)], capture_output=True, check=True, text=True).stdout
    return out.split()


def preset(pid="impact_wave_v1"):
    return load_presets()[pid]


def test_plan_without_any_audio_is_none():
    assert plan_audio(cfg(), preset(), WINDOW, None) is None
    assert plan_audio(cfg(keep=False), preset(), WINDOW, Path("a.m4a")) is None


def test_plan_fields():
    p = preset()
    plan = plan_audio(cfg(sfx=True, gain=-6), p, WINDOW, Path("a.m4a"))
    assert plan == AudioPlan(Path("a.m4a"), sfx_path(p), 2500 - p["sfx"]["peak_offset_ms"], -6.0, 4000, 0)
    assert plan_audio(cfg(keep=False, sfx=True), p, WINDOW, Path("a.m4a")).original is None
    early = plan_audio(cfg(sfx=True), p, EffectWindow(0, 1, 5), None)  # peak 42 ms < the SFX's own peak offset
    assert early.sfx_delay_ms == 0 and early.sfx_trim_ms == p["sfx"]["peak_offset_ms"] - 42 and early.original is None


def test_bundled_sfx_peak_offsets_are_measured():
    for p in load_presets().values():
        path = sfx_path(p)
        with wave.open(str(path), "rb") as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (RATE, 1, 2)
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2") / 32768
        assert energy_peak_ms(x) == p["sfx"]["peak_offset_ms"] and np.abs(x).max() <= 0.91


def test_filter_graph_is_numbers_only():
    graph, n = filter_graph(AudioPlan(Path("x;y.m4a"), Path("s.wav"), 120, -9.0, 4000), 900)
    assert n == 2 and "x;y" not in graph and "adelay=120:all=1" in graph and "volume=-9.00dB" in graph
    assert f"afade=t=in:st=0:d={FADE_IN_MS / 1000:.3f}" in graph and "afade=t=out:st=0.780:d=0.120" in graph
    assert "amix=inputs=2:duration=longest:normalize=0" in graph and "alimiter=limit=0.89" in graph


def test_original_only_keeps_sync_and_duration(clip, tmp_path):
    video, audio = clip
    out = tmp_path / "o.mp4"
    mux(video, AudioPlan(audio, None, 0, 0.0, 4000), out)
    assert streams(out) == ["aac"] and streams(out, "v") == ["h264"]
    info = probe(out)
    assert info["frame_count"] == 96
    x = decode(out)
    assert abs(len(x) / RATE - 4.0) <= 1 / 24
    onset = int(np.argmax(np.abs(x) > 0.02)) / RATE * 1000
    assert abs(onset - 200) <= FRAME_MS  # fixture tone starts at +200 ms
    ref = decode(audio)
    rms = lambda s: float(np.sqrt(np.mean(s[RATE:3 * RATE] ** 2)))
    assert abs(rms(x) - rms(ref)) < 0.01  # the limiter does not touch a -18 dBFS tone


def test_short_audio_is_padded_to_the_video(clip, tmp_path):
    video, audio = clip
    short = tmp_path / "short.m4a"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(audio), "-t", "1", "-c:a", "aac", str(short)], check=True)
    out = tmp_path / "p.mp4"
    mux(video, AudioPlan(short, None, 0, 0.0, 4000), out)
    assert abs(len(decode(out)) / RATE - 4.0) <= 1 / 24


@pytest.mark.parametrize("pid", ["impact_wave_v1", "energy_aura_v1", "blink_out_v1"])
def test_sfx_peak_lands_on_the_effect_peak(clip, tmp_path, pid):
    video, _ = clip
    plan = plan_audio(cfg(keep=False, sfx=True, gain=-3.0), preset(pid), WINDOW, None)
    out = tmp_path / "s.mp4"
    mux(video, plan, out)
    x = decode(out)
    assert abs(len(x) / RATE - 4.0) <= 1 / 24
    assert abs(energy_peak_ms(x) - 2500) <= FRAME_MS


def test_sfx_with_trimmed_head_keeps_its_peak(clip, tmp_path):
    video, _ = clip
    p = preset()
    plan = plan_audio(cfg(keep=False, sfx=True, gain=-3.0, duration_ms=4000), p, EffectWindow(0, 1, 5), None)
    out = tmp_path / "t.mp4"
    mux(video, plan, out)
    assert abs(energy_peak_ms(decode(out)) - 42) <= FRAME_MS


def test_mixed_audio_never_clips(clip, tmp_path):
    video, audio = clip
    plan = plan_audio(cfg(sfx=True, gain=0.0), preset(), WINDOW, audio)
    out = tmp_path / "m.mp4"
    mux(video, plan, out)
    x = decode(out)
    assert np.abs(x).max() <= 0.95
    assert abs(energy_peak_ms(x) - 2500) <= FRAME_MS
