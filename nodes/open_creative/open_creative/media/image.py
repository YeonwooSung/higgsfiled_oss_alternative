"""Image upload normalization: decoder-verified format, size limits, EXIF orientation,
sRGB conversion and metadata stripping."""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageCms, ImageOps

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
FORMAT_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class MediaError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class NormalizedImage:
    rgb: np.ndarray  # uint8 (h, w, 3), sRGB
    alpha: np.ndarray | None  # uint8 (h, w) or None when fully opaque / absent
    width: int
    height: int
    mime: str


def _to_srgb(img: Image.Image) -> Image.Image:
    icc = img.info.get("icc_profile")
    if not icc:
        return img
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        dst = ImageCms.createProfile("sRGB")
        mode = "RGBA" if img.mode == "RGBA" else "RGB"
        return ImageCms.profileToProfile(img, src, dst, outputMode=mode)
    except (ImageCms.PyCMSError, OSError):
        return img


def normalize_image(
    data: bytes, max_bytes: int = MAX_IMAGE_BYTES, max_pixels: int = MAX_IMAGE_PIXELS
) -> NormalizedImage:
    if len(data) > max_bytes:
        raise MediaError("too_large", f"image exceeds {max_bytes} bytes")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            fmt = img.format
    except (Image.UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError):
        raise MediaError("unsupported_format", "not a supported image") from None
    if fmt not in FORMAT_MIME:
        raise MediaError("unsupported_format", f"format {fmt} is not accepted")
    if img.width * img.height > max_pixels:
        raise MediaError("too_many_pixels", "image has too many pixels")
    try:
        img.load()
        img = ImageOps.exif_transpose(img)
    except (OSError, Image.DecompressionBombError, ValueError):
        raise MediaError("decode_failed", "image could not be decoded") from None

    if img.mode in ("P", "LA", "PA"):
        img = img.convert("RGBA")
    elif img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    img = _to_srgb(img)

    arr = np.asarray(img)
    alpha = None
    if arr.shape[2] == 4:
        alpha = np.ascontiguousarray(arr[..., 3])
        if alpha.min() == 255:
            alpha = None
    rgb = np.ascontiguousarray(arr[..., :3])
    return NormalizedImage(rgb=rgb, alpha=alpha, width=rgb.shape[1], height=rgb.shape[0], mime=FORMAT_MIME[fmt])


def to_png_bytes(rgb: np.ndarray, alpha: np.ndarray | None = None) -> bytes:
    """Encode without any metadata."""
    if alpha is not None:
        img = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
    else:
        img = Image.fromarray(rgb, "RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def mask_to_png_bytes(mask: np.ndarray) -> bytes:
    """Store a float [0,1] or bool mask as 8-bit grayscale PNG."""
    m = mask.astype(np.float32)
    buf = io.BytesIO()
    Image.fromarray(np.clip(np.rint(m * 255), 0, 255).astype(np.uint8), "L").save(buf, "PNG")
    return buf.getvalue()


def load_mask_png(data: bytes, max_pixels: int = MAX_IMAGE_PIXELS) -> np.ndarray:
    """Load a grayscale (or RGBA, using alpha) mask PNG as float32 [0,1]. Size is checked before decoding."""
    try:
        img = Image.open(io.BytesIO(data))
    except (Image.UnidentifiedImageError, OSError):
        raise MediaError("unsupported_format", "mask must be PNG") from None
    if img.format != "PNG":
        raise MediaError("unsupported_format", "mask must be PNG")
    if img.width * img.height > max_pixels:
        raise MediaError("too_many_pixels", "mask has too many pixels")
    if img.mode in ("RGBA", "LA"):
        arr = np.asarray(img.getchannel("A"))
    else:
        arr = np.asarray(img.convert("L"))
    return arr.astype(np.float32) / 255.0
