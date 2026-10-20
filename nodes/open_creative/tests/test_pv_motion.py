"""Pocket VFX camera stability diagnosis (background homography) on the proxy clip."""

import zipfile
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
import pytest
from open_creative.media.video import decode_frames
from open_creative.pocket_vfx.motion import camera_motion
from PIL import Image

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "pocket-vfx"
PW, PH = 640, 360


@pytest.fixture(scope="module")
def stomp():
    return [cv2.cvtColor(cv2.resize(f, (PW, PH), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
            for f in decode_frames(FIX / "stomp_landscape.mp4")]


@pytest.fixture(scope="module")
def stomp_masks():
    with zipfile.ZipFile(FIX / "stomp_masks.zip") as z:
        return [cv2.resize(np.asarray(Image.open(BytesIO(z.read(n))).convert("L")), (PW, PH),
                           interpolation=cv2.INTER_NEAREST) for n in sorted(z.namelist())]


def _texture(w, h, seed=0):
    rng = np.random.default_rng(seed)
    return cv2.GaussianBlur(rng.integers(0, 256, (h, w), np.uint8), (0, 0), 1.5)


def _crops(offsets):
    tex = _texture(PW + 200, PH + 200)
    return [np.ascontiguousarray(tex[100 + dy : 100 + dy + PH, 100 + dx : 100 + dx + PW]) for dx, dy in offsets]


def test_static_fixture_is_fixed(stomp, stomp_masks):
    rep = camera_motion(stomp, stomp_masks)
    assert rep["status"] == "fixed" and rep["max_shift_px"] <= 2.0
    assert camera_motion(stomp, None)["status"] == "fixed"  # RANSAC rejects the moving subject


def test_panned_clip_is_moving():
    rep = camera_motion(_crops([(3 * k, 0) for k in range(30)]))
    assert rep["status"] == "moving"
    assert rep["max_shift_px"] == pytest.approx(87.0, abs=1.0)


def test_small_jitter_is_handheld():
    rng = np.random.default_rng(3)
    rep = camera_motion(_crops([(0, 0)] + [tuple(rng.integers(-6, 7, 2)) for _ in range(23)]))
    assert rep["status"] == "handheld" and 2.0 < rep["max_shift_px"] <= 12.0


def test_textureless_clip_is_unknown():
    rep = camera_motion([np.full((PH, PW), 90, np.uint8)] * 10)
    assert rep == {"status": "unknown", "median_shift_px": None, "max_shift_px": None}
