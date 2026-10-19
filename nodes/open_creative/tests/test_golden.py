"""P0 acceptance: on 5 opaque product fixtures every lossless frame passes the core check,
and background layers (shadow, particles, brightness) never touch the core."""

from pathlib import Path

import numpy as np
import pytest

from open_creative.media.image import load_mask_png, mask_to_png_bytes, normalize_image
from open_creative.media.video import decode_frames, probe
from open_creative.product_lock.composite import LayerConfig
from open_creative.product_lock.geometry import output_size
from open_creative.product_lock.integrity import postencode_metrics
from open_creative.product_lock.masks import mask_from_alpha
from open_creative.product_lock.pipeline import prepare_locked_product, render_to_mp4

FIXTURES = sorted((Path(__file__).parents[3] / "fixtures" / "product-lock").glob("*.png"))


def moving_background(w, h, n=120):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    for t in range(n):
        f = np.empty((h, w, 3), np.uint8)
        f[..., 0] = (40 + 30 * np.sin(xx / 90 + t / 12)).astype(np.uint8)
        f[..., 1] = (yy / h * 120 + t).astype(np.uint8)
        f[..., 2] = 90
        yield f


def test_there_are_five_fixtures():
    assert len(FIXTURES) == 5


def test_mask_png_roundtrip():
    m = np.array([[0.0, 0.5, 1.0]], np.float32)
    back = load_mask_png(mask_to_png_bytes(m))
    assert np.abs(back - m).max() <= 0.5 / 255 + 1e-6


@pytest.mark.slow
@pytest.mark.parametrize("aspect", ["9:16", "16:9"])
@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_every_frame_preserves_core(fixture, aspect, tmp_path):
    img = normalize_image(fixture.read_bytes())
    assert img.alpha is not None
    w, h = output_size(aspect)
    locked = prepare_locked_product(
        img.rgb, mask_from_alpha(img.alpha), None,
        center_x=0.5, center_y=0.55, width_ratio=0.36, feather_px=1.5, aspect=aspect,
    )
    cfg = LayerConfig(brightness=0.15, shadow_enabled=True, shadow_opacity=0.5, shadow_blur_px=24,
                      particles="bokeh", seed=3)
    out = tmp_path / "ad.mp4"
    result = render_to_mp4(moving_background(w, h), locked, cfg, out, w, h)
    assert result.frames == 120
    assert result.report["passed"], result.report
    assert result.report["preencode_max_error"] <= 1
    assert result.cover.shape == (h, w, 3)
    info = probe(out)
    assert (info["width"], info["height"], info["frame_count"], info["fps"]) == (w, h, 120, 24)
    metrics = postencode_metrics(decode_frames(out), locked, w, h)
    assert metrics["frames"] == 120
    assert metrics["ssim_mean"] > 0.85  # diagnostic floor; threshold calibrated from fixtures
