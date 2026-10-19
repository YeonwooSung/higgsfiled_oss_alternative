import pytest

from open_creative.product_lock.geometry import Placement, output_size, place_product


def test_output_size_portrait_and_landscape():
    assert output_size("9:16") == (720, 1280)
    assert output_size("16:9") == (1280, 720)


def test_output_size_draft_is_quarter_area():
    assert output_size("9:16", draft=True) == (360, 640)
    assert output_size("16:9", draft=True) == (640, 360)


def test_output_size_rejects_unknown_aspect():
    with pytest.raises(ValueError):
        output_size("1:1")


def test_place_product_keeps_source_aspect():
    p = place_product(1000, 2000, 720, 1280, center_x=0.5, center_y=0.55, width_ratio=0.36)
    assert (p.w, p.h) == (259, 518)
    assert p.x0 == round(0.5 * 720 - 259 / 2)
    assert p.y0 == round(0.55 * 1280 - 518 / 2)


@pytest.mark.parametrize("ratio", [0.1, 0.8])
def test_place_product_rejects_width_out_of_range(ratio):
    with pytest.raises(ValueError):
        place_product(100, 100, 720, 1280, 0.5, 0.5, ratio)


def test_place_product_rejects_rotation():
    with pytest.raises(ValueError):
        place_product(100, 100, 720, 1280, 0.5, 0.5, 0.36, rotation_deg=5)


def test_visible_clips_product_partly_outside_frame():
    p = place_product(400, 400, 720, 1280, center_x=0.98, center_y=0.5, width_ratio=0.5)
    fy, fx, py, px = p.visible(720, 1280)
    assert fx.stop == 720
    assert fx.stop - fx.start == px.stop - px.start
    assert fy.stop - fy.start == py.stop - py.start
    assert px.start == 0 and px.stop < p.w


def test_visible_fully_outside_is_empty():
    p = Placement(x0=800, y0=0, w=100, h=100)
    fy, fx, py, px = p.visible(720, 1280)
    assert fx.stop - fx.start == 0
