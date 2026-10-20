from open_creative.pocket_vfx.presets import load_palettes, load_presets
from open_creative.pocket_vfx.timeline import (
    EffectWindow,
    effect_window,
    frame_count,
    frame_index,
    timing_errors,
)


def test_frame_index_rule():
    assert frame_index(1800) == 43 and frame_index(5000) == 120
    assert frame_index(20.84) == 1 and frame_index(20.8333) == 0  # exact half is 20.8333.. ms and frame_index(20.8) == 0 and frame_index(0) == 0
    assert frame_count(5000) == 120


def test_effect_window():
    e = {"start_ms": 1400, "peak_ms": 1800, "end_ms": 3400}
    assert effect_window(e, 5000) == EffectWindow(34, 43, 82)
    assert effect_window({"start_ms": 0, "peak_ms": 2500, "end_ms": 5000}, 5000).end == 119  # clamp to N-1


def test_timing_errors():
    ok = {"start_ms": 0, "peak_ms": 1000, "end_ms": 5000}
    assert timing_errors(ok, 5000) == []
    assert timing_errors({"start_ms": 2000, "peak_ms": 1000, "end_ms": 3000}, 5000) == [
        {"code": "timing_order", "field": "start_ms"}
    ]
    assert timing_errors({"start_ms": 0, "peak_ms": 4000, "end_ms": 3000}, 5000)[0]["code"] == "timing_order"
    assert timing_errors({"start_ms": 0, "peak_ms": 1000, "end_ms": 5001}, 5000) == [
        {"code": "timing_range", "field": "end_ms"}
    ]
    assert timing_errors({"start_ms": -1, "peak_ms": 1000, "end_ms": 2000}, 5000) == [
        {"code": "timing_range", "field": "start_ms"}
    ]


def test_presets_shape():
    presets = load_presets()
    assert set(presets) == {"impact_wave_v1", "energy_aura_v1", "blink_out_v1"}
    for p in presets.values():
        assert p["plate_frames"] == 49 and p["invalid_policy"] == "hide"
        assert p["controls"]["scale"] == [0.25, 2.0] and p["controls"]["rotation_deg"] == [-180, 180]
        assert ("squash" in p["controls"]) == (p["id"] == "impact_wave_v1")
        assert p["defaults"]["palette"] in load_palettes()
        for k in ("pre_peak", "post_peak"):
            assert p["defaults"]["durations_ms"][k] > 0
    assert presets["energy_aura_v1"]["bundled_plates"][0]["id"] == "energy_wisps_v1"
    assert presets["impact_wave_v1"]["defaults"]["durations_ms"] == {"pre_peak": 400, "post_peak": 1600}
    for pal in load_palettes().values():
        assert set(pal) == {"core", "mid", "edge"}
        assert all(len(v) == 3 and all(0 <= c <= 1 for c in v) for v in pal.values())
