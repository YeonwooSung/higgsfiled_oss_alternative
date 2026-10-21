"""Blink Out with a user clean plate: plate loading, background alignment, noise dissolve and its composite."""

import io
import itertools
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from open_creative.media.video import cover_fit, decode_frames
from open_creative.pocket_vfx.blink import (
    ALIGN_MAX_ERR_PX,
    alignment_frames,
    clean_plate_alignment,
    dissolve_alpha,
    dissolve_progress,
    load_clean_plate,
)
from open_creative.pocket_vfx.composite import FrameInputs, composite_frame
from open_creative.pocket_vfx.integrity import outside_roi_report
from open_creative.pocket_vfx.masks import mask_box
from open_creative.pocket_vfx.procedural import render_effect
from open_creative.pocket_vfx.timeline import EffectWindow
from PIL import Image

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "pocket-vfx"
W, H = 1280, 720
WIN = EffectWindow(start=38, peak=43, end=72)
BLINK = {"scale": 1.0, "intensity": 0.8, "palette": "cyan"}


@pytest.fixture(scope="module")
def stomp():
    return list(decode_frames(FIX / "stomp_landscape.mp4"))


@pytest.fixture(scope="module")
def gt_masks():
    with zipfile.ZipFile(FIX / "stomp_masks.zip") as z:
        return [np.asarray(Image.open(io.BytesIO(z.read(n))).convert("L")) > 127
                for n in sorted(n for n in z.namelist() if n.endswith(".png"))]


@pytest.fixture(scope="module")
def plate():
    return load_clean_plate(FIX / "clean_plate.png", W, H)


def _gray(f):
    return cv2.cvtColor(f, cv2.COLOR_RGB2GRAY)


def _sampled(stomp, gt_masks):
    idx = alignment_frames(len(stomp))
    return [_gray(stomp[i]) for i in idx], [gt_masks[i] for i in idx]


# ---- loading ---------------------------------------------------------------------------------------------------


def test_load_clean_plate_cover_fits_like_the_work_clip(tmp_path, plate):
    assert plate.shape == (H, W, 3) and plate.dtype == np.uint8
    src = np.asarray(Image.open(FIX / "clean_plate.png").convert("RGB"))
    big = cv2.resize(src, (1920, 1080), interpolation=cv2.INTER_LINEAR)
    Image.fromarray(big).save(tmp_path / "big.png")
    assert np.array_equal(load_clean_plate(tmp_path / "big.png", W, H), cover_fit(big, W, H))
    # a portrait canvas takes the centre column of a landscape plate, like the work clip's cover-fit
    portrait = load_clean_plate(FIX / "clean_plate.png", 720, 1280)
    assert portrait.shape == (1280, 720, 3)
    assert np.array_equal(portrait, cover_fit(src, 720, 1280))


def test_load_clean_plate_applies_exif_orientation(tmp_path):
    upright = np.zeros((40, 80, 3), np.uint8)
    upright[:, :40] = 255  # left half white
    stored = np.ascontiguousarray(np.rot90(upright, 1))  # stored rotated; EXIF 6 = rotate 90 cw to display
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.fromarray(stored).save(tmp_path / "rot.jpg", quality=95, exif=exif)
    out = load_clean_plate(tmp_path / "rot.jpg", 80, 40)
    assert out[:, :30].mean() > 200 and out[:, 50:].mean() < 50


# ---- alignment -------------------------------------------------------------------------------------------------


def test_fixture_clean_plate_is_aligned(stomp, gt_masks, plate):
    frames, masks = _sampled(stomp, gt_masks)
    rep = clean_plate_alignment(plate, frames, masks)
    assert rep["status"] == "ok", rep
    assert rep["median_err_px"] <= 0.5 and rep["inliers"] >= 12 and rep["frames"] == len(frames)
    assert clean_plate_alignment(plate, frames, masks, camera_motion="fixed")["status"] == "ok"


def test_shifted_clean_plate_is_a_mismatch(stomp, gt_masks, plate):
    frames, masks = _sampled(stomp, gt_masks)
    shifted = np.ascontiguousarray(np.roll(plate, 20, axis=1))
    rep = clean_plate_alignment(shifted, frames, masks)
    assert rep["status"] == "mismatch" and rep["reason"] == "offset"
    assert rep["median_err_px"] == pytest.approx(20.0, abs=1.5)


def test_panned_clip_is_a_mismatch(plate):
    big = cv2.resize(plate, (W + 300, H + 170), interpolation=cv2.INTER_LINEAR)
    frames = [_gray(np.ascontiguousarray(big[80:80 + H, 6 * k:6 * k + W])) for k in range(40)]
    first = np.ascontiguousarray(big[80:80 + H, 0:W])
    rep = clean_plate_alignment(first, frames[:: max(1, len(frames) // 6)], None)
    assert rep["status"] == "mismatch" and rep["median_err_px"] > ALIGN_MAX_ERR_PX
    # the analysis already knows the camera moves: no alignment is attempted
    rep = clean_plate_alignment(first, frames[:1], None, camera_motion="moving")
    assert rep == {"status": "mismatch", "reason": "camera_not_fixed", "median_err_px": None, "inliers": None,
                   "frames": 0}
    assert clean_plate_alignment(first, frames[:1], None, camera_motion="unknown")["reason"] == "camera_not_fixed"


def test_unrelated_or_textureless_plate_is_unknown(stomp, gt_masks):
    frames, masks = _sampled(stomp, gt_masks)
    flat = np.full((H, W, 3), 128, np.uint8)
    rep = clean_plate_alignment(flat, frames, masks)
    assert rep["status"] == "unknown" and rep["reason"] == "too_few_inliers"
    assert clean_plate_alignment(flat, [], None)["status"] == "unknown"


def test_alignment_frames_are_spread_over_the_clip():
    assert alignment_frames(120) == [0, 24, 48, 71, 95, 119]
    assert alignment_frames(3) == [0, 1, 2]
    assert alignment_frames(1) == [0]


# ---- dissolve --------------------------------------------------------------------------------------------------


def _soft(m):
    return m.astype(np.float32)


def test_dissolve_progress_runs_from_peak_to_end():
    assert dissolve_progress(WIN, WIN.start) == 0.0 and dissolve_progress(WIN, WIN.peak) == 0.0
    assert dissolve_progress(WIN, WIN.end) == 1.0 and dissolve_progress(WIN, 119) == 1.0
    assert 0.0 < dissolve_progress(WIN, (WIN.peak + WIN.end) // 2) < 1.0
    assert dissolve_progress(EffectWindow(40, 50, 50), 50) == 1.0


def test_dissolve_alpha_rises_from_peak_to_end(gt_masks):
    m = _soft(gt_masks[60])
    assert not dissolve_alpha(m, WIN.peak - 1, WIN, 7).any()
    assert not dissolve_alpha(m, WIN.peak, WIN, 7).any()
    end = dissolve_alpha(m, WIN.end, WIN, 7)
    assert end.dtype == np.float32 and end.shape == (H, W)
    assert np.all(end[gt_masks[60]] == 1.0)  # fully revealed inside the mask
    assert np.array_equal(dissolve_alpha(m, 100, WIN, 7), end)  # stays revealed after the end
    sums = [float(dissolve_alpha(m, f, WIN, 7)[gt_masks[60]].mean()) for f in range(WIN.peak, WIN.end + 1)]
    assert all(b >= a - 1e-6 for a, b in itertools.pairwise(sums))
    mid = dissolve_alpha(m, (WIN.peak + WIN.end) // 2, WIN, 7)[gt_masks[60]]
    assert 0.3 < float(mid.mean()) < 0.7
    assert ((mid > 0) & (mid < 1)).mean() < 0.5  # a noise threshold, not a uniform cross-fade
    # never outside the (slightly dilated) subject
    far = cv2.dilate(gt_masks[60].astype(np.uint8), np.ones((31, 31), np.uint8)) == 0
    assert not end[far].any()


def test_dissolve_alpha_is_seeded(gt_masks):
    m = _soft(gt_masks[60])
    f = (WIN.peak + WIN.end) // 2
    assert np.array_equal(dissolve_alpha(m, f, WIN, 7), dissolve_alpha(m, f, WIN, 7))
    assert not np.array_equal(dissolve_alpha(m, f, WIN, 7), dissolve_alpha(m, f, WIN, 8))


# ---- composite -------------------------------------------------------------------------------------------------


def _blink_inputs(frame, mask, plate, i, seed=7):
    soft = _soft(mask)
    effect = render_effect("blink_out_v1", BLINK, WIN, i, None, W, H, seed, soft)
    return FrameInputs(source=frame, effect=effect, plate=None, subject_mask=None, front_glow=None, roi_limit=None,
                       clean_plate=plate, dissolve=dissolve_alpha(soft, i, WIN, seed)), effect


def test_subject_is_replaced_by_the_clean_plate_at_the_end(stomp, gt_masks, plate):
    i = WIN.end
    inp, effect = _blink_inputs(stomp[i], gt_masks[i], plate, i)
    out, roi = composite_frame(inp, "in_front")
    core = cv2.erode(gt_masks[i].astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    clear = core & (effect.alpha < 1.0 / 1024 if effect is not None else True)
    assert clear.sum() > 1000
    diff = np.abs(out[clear].astype(np.int16) - plate[clear].astype(np.int16)).max()
    assert diff <= 1
    assert outside_roi_report(stomp[i], out, roi)["max_err"] <= 1
    assert roi[gt_masks[i]].all()  # the dissolve region is inside the ROI


def test_blink_roi_holds_on_every_frame_and_covers_the_particles(stomp, gt_masks, plate):
    for i in range(WIN.peak - 2, WIN.end + 3, 3):
        inp, effect = _blink_inputs(stomp[i], gt_masks[i], plate, i)
        out, roi = composite_frame(inp, "in_front")
        assert outside_roi_report(stomp[i], out, roi)["max_err"] <= 1
        if i < WIN.peak:
            assert np.array_equal(out, stomp[i])  # untouched before the dissolve starts (no particles yet)
        if effect is not None:
            hit = effect.alpha > 1.0 / 1024
            assert roi[hit].all()


def test_without_a_dissolve_the_composite_is_unchanged(stomp, gt_masks, plate):
    i = 60
    a = composite_frame(FrameInputs(source=stomp[i], effect=None, plate=None, subject_mask=None, front_glow=None,
                                    roi_limit=None, clean_plate=plate, dissolve=None), "in_front")
    assert np.array_equal(a[0], stomp[i]) and not a[1].any()
    zero = np.zeros((H, W), np.float32)
    b = composite_frame(FrameInputs(source=stomp[i], effect=None, plate=None, subject_mask=None, front_glow=None,
                                    roi_limit=None, clean_plate=plate, dissolve=zero), "in_front")
    assert np.array_equal(b[0], stomp[i]) and not b[1].any()
    with pytest.raises(ValueError):
        composite_frame(FrameInputs(source=stomp[i], effect=None, plate=None, subject_mask=None, front_glow=None,
                                    roi_limit=None, clean_plate=plate[:10], dissolve=zero + 1), "in_front")


def test_dissolve_box_is_the_dilated_subject(gt_masks):
    m = _soft(gt_masks[60])
    x0, y0, x1, y1 = mask_box(dissolve_alpha(m, WIN.end, WIN, 7) > 0)
    gx0, gy0, gx1, gy1 = mask_box(gt_masks[60])
    assert gx0 - 10 <= x0 <= gx0 and gy0 - 10 <= y0 <= gy0 and gx1 <= x1 <= gx1 + 10 and gy1 <= y1 <= gy1 + 10
