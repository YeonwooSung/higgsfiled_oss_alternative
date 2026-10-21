import copy

from open_creative.pocket_vfx.keys import TRACK_MODEL_REV, masks_key, model_rev, track_key

CFG = {
    "trim": {"source_start_ms": 0, "duration_ms": 5000},
    "output": {"aspect": "16:9", "fps": 24},
    "anchor": {"frame": 43, "x": 0.5, "y": 0.7, "follow": True, "track_window_frames": None},
    "corrections": [],
    "subject_prompts": [],
    "mask_edits": [],
    "effect": {"preset_id": "impact_wave_v1", "start_ms": 1400, "peak_ms": 1800, "end_ms": 3400,
               "scale": 1.0, "intensity": 0.8},
    "plate": {"mode": "bundled", "seed": 12345},
}
SHA, REV = "a" * 64, model_rev("mock_v1")


def changed(path, value):
    c = copy.deepcopy(CFG)
    target = c
    for p in path[:-1]:
        target = target[p]
    target[path[-1]] = value
    return c


def test_model_rev_combines_constant_and_profile():
    assert TRACK_MODEL_REV == "lk_fb_median_v1+smooth_s1_b0.5+sam2.1_hiera_small+keys_v2"
    assert model_rev("mock_v1") != model_rev("open_weights_v1") and TRACK_MODEL_REV in model_rev("mock_v1")


def test_track_key_is_stable_hex():
    k = track_key(CFG, SHA, REV)
    assert k == track_key(copy.deepcopy(CFG), SHA, REV) and len(k) == 64 and int(k, 16) >= 0


def test_track_key_changes_with_review_focus_fields():
    base = track_key(CFG, SHA, REV)
    for path, value in ((("trim", "source_start_ms"), 500), (("trim", "duration_ms"), 4000),
                        (("output", "aspect"), "9:16"), (("anchor", "frame"), 44), (("anchor", "x"), 0.51),
                        (("anchor", "y"), 0.2), (("anchor", "follow"), False),
                        (("anchor", "track_window_frames"), 24),
                        (("corrections",), [{"frame": 60, "x": 0.4, "y": 0.4}]),
                        (("subject_prompts",), [{"frame": 43, "points": [{"x": 0.5, "y": 0.4, "label": 1}]}]),
                        (("mask_edits",), [{"frame": 50, "asset_id": "0" * 32}])):
        assert track_key(changed(path, value), SHA, REV) != base, path
    assert track_key(CFG, "b" * 64, REV) != base
    assert track_key(CFG, SHA, model_rev("open_weights_v1")) != base


def test_track_key_ignores_effect_plate_and_audio():
    base = track_key(CFG, SHA, REV)
    for path, value in ((("effect", "intensity"), 0.2), (("effect", "scale"), 1.7), (("effect", "peak_ms"), 2000),
                        (("plate", "seed"), 7), (("output", "fps"), 24)):
        assert track_key(changed(path, value), SHA, REV) == base, path


def test_masks_key_ignores_corrections_and_anchor():
    """The masks prompts use only subject prompts + mask edits (+ trim/aspect/source/model), so point corrections and
    every anchor field leave the masks key unchanged (R26) while they still change the track key."""
    base = masks_key(CFG, SHA, REV)
    for path, value in ((("corrections",), [{"frame": 60, "x": 0.4, "y": 0.4}]), (("anchor", "frame"), 44),
                        (("anchor", "x"), 0.51), (("anchor", "y"), 0.2), (("anchor", "follow"), False),
                        (("anchor", "track_window_frames"), 24)):
        assert masks_key(changed(path, value), SHA, REV) == base, path
    for path, value in ((("mask_edits",), [{"frame": 50, "asset_id": "0" * 32}]),
                        (("subject_prompts",), [{"frame": 43, "points": [{"x": 0.5, "y": 0.4, "label": 1}]}]),
                        (("trim", "duration_ms"), 4000), (("output", "aspect"), "9:16")):
        assert masks_key(changed(path, value), SHA, REV) != base, path
    assert masks_key(CFG, "b" * 64, REV) != base and masks_key(CFG, SHA, model_rev("open_weights_v1")) != base
    assert masks_key(CFG, SHA, REV) != track_key(CFG, SHA, REV)


def test_track_model_rev_names_the_track_smoothing():
    """Changing the smoothing constants changes the track output, so the revision (and every track key) must change
    with them; this pins the revision string to the constants the worker and the node use."""
    from open_creative.pocket_vfx.track import ALGORITHM, SMOOTH_MAX_SHIFT_PX, SMOOTH_SIGMA_FRAMES

    assert TRACK_MODEL_REV.startswith(f"{ALGORITHM}+smooth_s{SMOOTH_SIGMA_FRAMES:g}_b{SMOOTH_MAX_SHIFT_PX:g}+")
