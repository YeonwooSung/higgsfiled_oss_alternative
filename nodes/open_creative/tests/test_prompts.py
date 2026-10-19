import pytest

from open_creative.product_lock.prompts import (
    build_background_prompt,
    build_video_prompt,
    color_name,
    load_presets,
)


def test_three_presets_loaded():
    presets = load_presets()
    assert set(presets) == {"soft_studio_v1", "neon_studio_v1", "botanical_v1"}
    neon = presets["neon_studio_v1"]
    assert neon.version == "1.0.0" and neon.particles == "neon"
    assert neon.allowed_motion == ("subtle", "balanced")


def test_background_prompt_uses_color_and_center_composition():
    p = load_presets()["neon_studio_v1"]
    prompt, version = build_background_prompt(p, "#965EFF", composition="center", extra_prompt="")
    assert version == "neon_studio_v1@1.0.0"
    assert "purple" in prompt
    assert "empty center" in prompt
    assert "{color}" not in prompt


def test_extra_prompt_is_appended_and_sanitized():
    p = load_presets()["soft_studio_v1"]
    prompt, _ = build_background_prompt(p, "#FFFFFF", extra_prompt="warm\x00 morning\nlight")
    assert prompt.endswith("warm morning light")


def test_extra_prompt_over_300_chars_rejected():
    p = load_presets()["soft_studio_v1"]
    with pytest.raises(ValueError):
        build_background_prompt(p, "#FFFFFF", extra_prompt="a" * 301)


def test_bad_hex_rejected():
    with pytest.raises(ValueError):
        color_name("965EFF")


@pytest.mark.parametrize("hex_, name", [("#000000", "black"), ("#FFFFFF", "white"), ("#18794E", "green"), ("#C41E3A", "red")])
def test_color_name_nearest(hex_, name):
    assert name in color_name(hex_)


def test_video_prompt_respects_allowed_motion():
    p = load_presets()["botanical_v1"]
    assert "no camera movement" in build_video_prompt(p, "subtle")
    with pytest.raises(ValueError):
        build_video_prompt(p, "orbit")
