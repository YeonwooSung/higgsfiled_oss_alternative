"""vfx.render audio: original audio kept after trim, optional SFX at the effect peak, none -> no audio stream."""

import subprocess
from pathlib import Path

import pytest
from open_creative.pocket_vfx.presets import load_presets
from test_vfx_executors import Proj, pv  # noqa: F401 - pv is the project fixture

QUICK = {"track_job_id": None, "render_profile": "quick"}


def audio_streams(path) -> list[str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name",
                          "-of", "csv=p=0", str(path)], capture_output=True, check=True, text=True).stdout
    return out.split()


@pytest.fixture
def proj(ctx, db_factory, storage, pv):  # noqa: F811
    return Proj(ctx, db_factory, storage, pv)


def prepared(proj, name, **changes):
    proj.prepare(name, trim={"source_start_ms": 1000, "duration_ms": 2000})
    proj.revise(anchor__follow=False, effect__layer_order="in_front", **changes)
    return proj


def test_silent_clip_without_sfx_has_no_audio_stream(proj):
    prepared(proj, "stomp_landscape.mp4")
    r = proj.run("vfx.render", QUICK).result_json
    assert r["has_audio"] is False and r["audio"] is None
    assert audio_streams(proj.path(r["render_video_asset_id"])) == []
    assert proj.asset(r["render_video_asset_id"]).meta_json["has_audio"] is False


def test_original_audio_is_kept_and_muxed(proj):
    prepared(proj, "with_audio.mp4")
    r = proj.run("vfx.render", QUICK).result_json
    assert r["has_audio"] is True and r["audio"]["original_kept"] is True and r["audio"]["sfx"] is False
    video = proj.asset(r["render_video_asset_id"])
    assert audio_streams(proj.path(video.id)) == ["aac"] and video.meta_json["has_audio"] is True
    assert r["frame_count"] == 48 and r["roi_qa"]["status"] == "pass"
    assert proj.asset(r["roi_qa_asset_id"]).meta_json["has_audio"] is True


def test_keep_original_off_drops_the_audio(proj):
    prepared(proj, "with_audio.mp4", audio={"keep_original": False, "sfx_enabled": False, "sfx_gain_db": -9})
    r = proj.run("vfx.render", QUICK).result_json
    assert r["has_audio"] is False and audio_streams(proj.path(r["render_video_asset_id"])) == []


def test_sfx_on_a_silent_clip_reports_delay_and_gain(proj):
    prepared(proj, "stomp_landscape.mp4", audio={"keep_original": True, "sfx_enabled": True, "sfx_gain_db": -12})
    r = proj.run("vfx.render", QUICK).result_json
    offset = load_presets()[proj.cfg["effect"]["preset_id"]]["sfx"]["peak_offset_ms"]
    peak_ms = round(round(proj.cfg["effect"]["peak_ms"] * 24 / 1000) * 1000 / 24)
    assert r["has_audio"] is True
    assert r["audio"] == {"original_kept": False, "sfx": True, "sfx_delay_ms": max(0, peak_ms - offset),
                          "sfx_gain_db": -12.0, "duration_ms": 2000}
    assert audio_streams(proj.path(r["render_video_asset_id"])) == ["aac"]


def test_missing_sfx_file_is_a_stage_failure(proj, monkeypatch):
    prepared(proj, "stomp_landscape.mp4", audio={"keep_original": True, "sfx_enabled": True, "sfx_gain_db": -9})
    monkeypatch.setattr("open_creative.pocket_vfx.audio.sfx_path", lambda preset: Path("/no.wav"))
    job = proj.run("vfx.render", QUICK, expect="failed")
    assert job.error_code == "sfx_missing"
