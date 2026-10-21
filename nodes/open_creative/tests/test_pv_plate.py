"""Effect plates: shapes (Ruling R3), procedural generator, streamed matte access, placement (spec §8)."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from open_creative.media.video import decode_frames, probe
from open_creative.pocket_vfx.matte import (
    MatteConfig,
    MatteStats,
    loop_seam_ok,
    matte_qa,
    matte_sequence,
)
from open_creative.pocket_vfx.plate import (
    BUNDLED_FRAMES,
    IMPACT_FRONT_FLOOR,
    PlateFrames,
    aura_roi,
    bundled_entry,
    bundled_path,
    generated_plate_frames,
    impact_weight,
    place_plate,
    plate_envelope,
    plate_prompt,
    procedural_plate,
    restrict,
    tint_matte,
)
from open_creative.pocket_vfx.presets import load_palettes, load_presets, presets_path
from open_creative.pocket_vfx.timeline import EffectWindow

ROOT = Path(__file__).resolve().parents[3]


def test_generated_plate_frames_are_ltx_lengths():
    assert [generated_plate_frames(w) for w in (1, 9, 10, 17, 48, 49, 50, 113, 120)] == \
        [9, 9, 17, 17, 49, 49, 57, 113, 121]
    assert all((generated_plate_frames(w) - 1) % 8 == 0 and generated_plate_frames(w) >= w for w in range(1, 121))


def test_plate_prompt_fills_the_palette():
    p = load_presets()["impact_wave_v1"]
    assert plate_prompt(p, "cyan").startswith("isolated bright cyan warm dust burst")
    assert "{palette}" not in plate_prompt(p, "cyan")


def test_bundled_entry():
    p = load_presets()["energy_aura_v1"]
    assert bundled_entry(p, None)["id"] == "energy_wisps_v1" == bundled_entry(p, "energy_wisps_v1")["id"]
    assert bundled_entry(p, "dust_burst_v1") is None
    assert bundled_entry({"bundled_plates": []}, None) is None


@pytest.mark.parametrize("preset_id", ["impact_wave_v1", "energy_aura_v1", "blink_out_v1"])
def test_bundled_plate_files(preset_id):
    """Every bundled plate exists in both orientations, 49 frames 768x448 / 448x768, loops, and mattes clean."""
    manifest = json.loads((presets_path().parent / "plates" / "manifest.json").read_text())
    assert manifest["license"].startswith("MIT")
    for entry in load_presets()[preset_id]["bundled_plates"]:
        assert entry["frames"] == BUNDLED_FRAMES and entry["loopable"] is True
        for portrait, size in ((False, (768, 448)), (True, (448, 768))):
            path = bundled_path(entry, portrait)
            info = probe(path)
            assert (info["width"], info["height"], info["frame_count"], info["fps"]) == (*size, 49, 24)
            rel = entry["file_portrait" if portrait else "file"]
            assert manifest["files"][rel]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        frames = list(decode_frames(bundled_path(entry, False)))
        assert loop_seam_ok(frames[0], frames[-1])
        stats = MatteStats()
        for _ in matte_sequence(iter(frames), None, stats):
            pass
        assert matte_qa(stats.summary())["status"] == "ok"


def test_procedural_plate_is_deterministic_black_and_tintable():
    a = list(procedural_plate("dust", 3, 96, 64, 9))
    b = list(procedural_plate("dust", 3, 96, 64, 9))
    c = list(procedural_plate("dust", 4, 96, 64, 9))
    assert len(a) == 9 and a[0].shape == (64, 96, 3) and a[0].dtype == np.uint8
    assert all(np.array_equal(x, y) for x, y in zip(a, b, strict=True))
    assert any(not np.array_equal(x, y) for x, y in zip(a, c, strict=True))
    assert all((f[..., 0] == f[..., 1]).all() for f in a)  # gray emissive
    assert a[4][0, 0].tolist() == [0, 0, 0]  # pure black background
    amber = next(procedural_plate("dust", 3, 96, 64, 9, load_palettes()["amber"]))
    lit = amber.sum(axis=2) > 60
    assert lit.any() and (amber[lit][:, 0] >= amber[lit][:, 2]).all()  # amber: red over blue
    with pytest.raises(ValueError):
        next(procedural_plate("smoke", 1, 8, 8, 1))


def test_plate_frames_stream_and_restart_on_wrap():
    opened = []

    def open_frames():
        opened.append(1)
        for i in range(5):
            f = np.zeros((4, 4, 3), np.uint8)
            f[0, 0] = 40 * (i + 1)
            yield f

    pf = PlateFrames(open_frames, MatteConfig())
    ref = [rgb for rgb, _ in matte_sequence(open_frames(), MatteConfig())]
    opened.clear()
    for idx in (0, 0, 1, 3, 4, 1):
        np.testing.assert_array_equal(pf.get(idx)[0], ref[idx])
    assert len(opened) == 2  # one restart for the backwards step
    with pytest.raises(ValueError):
        pf.get(5)


def test_plate_envelope():
    w = EffectWindow(10, 14, 30)
    assert plate_envelope(w, 9) == 0.0 and plate_envelope(w, 31) == 0.0
    assert 0 < plate_envelope(w, 10) < plate_envelope(w, 12) < 1.0
    assert plate_envelope(w, 14) == 1.0 and plate_envelope(w, 20) == 1.0
    assert plate_envelope(w, 30) == 0.0 and 0 < plate_envelope(w, 27) < 1


def _disc_plate(w=96, h=56):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = np.clip(1.0 - np.hypot(xx - (w - 1) / 2, yy - (h - 1) / 2) / 20.0, 0, 1).astype(np.float32)
    return np.repeat(a[..., None], 3, axis=2) * 0.5, a


def test_place_plate_cover_fits_a_square_on_the_anchor():
    rgb, a = _disc_plate()
    layer = place_plate(rgb, a, (300.0, 200.0), 112, 640, 360, gain=0.8)
    assert layer.rgb_premult.shape == (360, 640, 3) and layer.alpha.shape == (360, 640)
    x0, y0, x1, y1 = layer.bbox
    assert 244 <= x0 and x1 <= 356 and 144 <= y0 and y1 <= 256  # inside the 112 px square
    ys, xs = np.nonzero(layer.alpha)
    wts = layer.alpha[ys, xs]
    assert abs((xs * wts).sum() / wts.sum() + 0.5 - 300) < 1.0 and abs((ys * wts).sum() / wts.sum() + 0.5 - 200) < 1.0
    # aspect kept: the 56 px plate height fills the square (scale 2), the round disc stays round
    assert abs((x1 - x0) - (y1 - y0)) <= 2 and 70 <= x1 - x0 <= 84
    assert layer.alpha.max() == pytest.approx(0.8, abs=0.05)  # gain; the disc peak falls between pixels
    assert (layer.rgb_premult <= layer.alpha[..., None] + 1e-6).all()
    # clipped at the frame edge, never wrapped
    edge = place_plate(rgb, a, (5.0, 5.0), 112, 640, 360)
    assert edge.bbox[0] == 0 and edge.bbox[1] == 0 and edge.bbox[2] < 60
    assert place_plate(rgb, a, (-200.0, 5.0), 112, 640, 360) is None
    assert place_plate(rgb, a, (300.0, 200.0), 112, 640, 360, gain=0.0) is None


def test_impact_weight_attenuates_the_front_half():
    wgt = impact_weight(200)[:, 0]
    assert wgt.shape == (200,) and (wgt[:90] == 1.0).all()
    assert wgt[-1] == pytest.approx(IMPACT_FRONT_FLOOR) and np.all(np.diff(wgt) <= 1e-7)
    rgb = np.ones((50, 50, 3), np.float32) * 0.5
    layer = place_plate(rgb, np.ones((50, 50), np.float32), (100.0, 100.0), 100, 200, 200, weight=impact_weight(100))
    assert layer.alpha[60, 100] == pytest.approx(1.0) and layer.alpha[145, 100] < 0.2  # behind kept, front faded


def test_aura_roi_is_band_union_anchor_region():
    m = np.zeros((200, 300), np.float32)
    m[60:160, 120:180] = 1.0
    roi = aura_roi(m, (40.0, 40.0), 200, 300, 200)
    assert roi.max() <= 1.0 and roi[40, 40] == pytest.approx(1.0)  # anchor centre
    assert roi[110, 150] < 0.05  # deep inside the silhouette: no fill
    assert roi[110, 182] > 0.5  # on the contour
    assert roi[190, 290] == 0.0  # far from both
    only_anchor = aura_roi(None, (150.0, 100.0), 200, 300, 200)
    assert only_anchor[100, 150] == pytest.approx(1.0) and only_anchor[0, 0] < 0.01
    rgb, a = np.ones((200, 300, 3), np.float32) * 0.5, np.ones((200, 300), np.float32)
    from open_creative.pocket_vfx.procedural import EffectLayer

    r = restrict(EffectLayer(rgb, a, (0, 0, 300, 200)), roi)
    assert r.alpha[110, 150] < 0.05 and (r.rgb_premult <= r.alpha[..., None] + 1e-6).all()


def test_tint_matte_keeps_premultiplied_and_takes_the_palette():
    a = np.array([[0.0, 0.5, 1.0]], np.float32)
    rgb = np.repeat((a * 0.8)[..., None], 3, axis=2)
    pal = load_palettes()["cyan"]
    t = tint_matte(rgb, a, pal)
    assert t.shape == (1, 3, 3) and (t <= a[..., None] + 1e-6).all() and t[0, 0].tolist() == [0, 0, 0]
    assert t[0, 2, 2] > t[0, 2, 0]  # cyan: blue over red
