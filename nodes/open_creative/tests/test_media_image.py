import io

import numpy as np
import pytest
from PIL import Image

from open_creative.media.image import MediaError, normalize_image, to_png_bytes


def encode(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def test_jpeg_exif_orientation_is_applied():
    img = Image.new("RGB", (40, 20), (200, 10, 10))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW
    n = normalize_image(encode(img, "JPEG", exif=exif.tobytes()))
    assert (n.width, n.height) == (20, 40)
    assert n.rgb.shape == (40, 20, 3) and n.alpha is None
    assert n.mime == "image/jpeg"


def test_png_alpha_is_kept_separately():
    img = Image.new("RGBA", (10, 8), (0, 255, 0, 0))
    img.putpixel((3, 3), (0, 255, 0, 255))
    n = normalize_image(encode(img, "PNG"))
    assert n.alpha is not None and n.alpha.shape == (8, 10)
    assert n.alpha[3, 3] == 255 and n.alpha[0, 0] == 0
    assert n.rgb.shape == (8, 10, 3)


def test_opaque_png_alpha_is_dropped():
    img = Image.new("RGBA", (4, 4), (1, 2, 3, 255))
    assert normalize_image(encode(img, "PNG")).alpha is None


def test_webp_is_accepted():
    n = normalize_image(encode(Image.new("RGB", (6, 6), (5, 5, 5)), "WEBP"))
    assert n.mime == "image/webp"


def test_non_image_bytes_rejected():
    with pytest.raises(MediaError) as e:
        normalize_image(b"hello world, not an image")
    assert e.value.code == "unsupported_format"


def test_gif_rejected():
    with pytest.raises(MediaError) as e:
        normalize_image(encode(Image.new("RGB", (4, 4)), "GIF"))
    assert e.value.code == "unsupported_format"


def test_oversize_bytes_rejected():
    with pytest.raises(MediaError) as e:
        normalize_image(b"\x89PNG" + b"0" * 100, max_bytes=50)
    assert e.value.code == "too_large"


def test_pixel_bomb_rejected_before_decode():
    data = encode(Image.new("L", (5000, 5000)), "PNG")
    with pytest.raises(MediaError) as e:
        normalize_image(data, max_pixels=24_000_000 // 1000)
    assert e.value.code == "too_many_pixels"


def test_png_roundtrip_with_alpha():
    rgb = np.zeros((3, 4, 3), np.uint8)
    alpha = np.full((3, 4), 7, np.uint8)
    n = normalize_image(to_png_bytes(rgb, alpha))
    assert np.array_equal(n.alpha, alpha)
