"""asset.inspect: decoder-verified normalization of uploads (originals are preserved)."""

from __future__ import annotations

import hashlib
import io

from open_creative.media.image import MediaError, load_mask_png, mask_to_png_bytes, normalize_image, to_png_bytes
from open_creative.product_lock.masks import mask_from_alpha

from ..stages import JobRun, StageFailed


def inspect_asset(run: JobRun) -> tuple[list[str], dict]:
    asset = run.asset(run.job.options_json["asset_id"])
    if asset.state == "ready":
        return [str(asset.id)], dict(run.job.result_json or {})
    data = run.ctx.storage.get_bytes(asset.object_key)
    run.transition("postprocessing", "inspect")
    try:
        if asset.role == "product":
            return _product(run, asset, data)
        return _mask(run, asset, data)
    except MediaError as e:
        asset.state, asset.error_code = "rejected", e.code
        run.db.commit()
        raise StageFailed(e.code, _MESSAGES.get(e.code, "The file could not be used.")) from e


_MESSAGES = {
    "too_large": "Images must be 20 MB or smaller.",
    "unsupported_format": "Use a JPG, PNG or WebP image.",
    "too_many_pixels": "Images must be 24 megapixels or smaller.",
    "decode_failed": "The image could not be decoded.",
}


def _product(run: JobRun, asset, data: bytes):
    img = normalize_image(data)
    normalized_key = asset.object_key.replace("-original", "")
    if normalized_key == asset.object_key:
        normalized_key = asset.object_key + ".normalized.png"
    normalized_key = normalized_key.rsplit(".", 1)[0] + ".png"
    png = to_png_bytes(img.rgb, img.alpha)
    run.ctx.storage.put_bytes(normalized_key, png, "image/png")
    asset.meta_json = {"original_key": asset.object_key, "original_mime": img.mime,
                       "original_sha256": hashlib.sha256(data).hexdigest(), "has_alpha": img.alpha is not None,
                       "normalization": {"exif_orientation": "applied", "color": "sRGB", "metadata": "stripped"}}
    asset.object_key, asset.mime, asset.bytes = normalized_key, "image/png", len(png)
    asset.sha256 = hashlib.sha256(png).hexdigest()
    asset.width, asset.height = img.width, img.height
    asset.state = "ready"
    run.db.commit()
    outputs, result = [str(asset.id)], {"has_alpha": img.alpha is not None}
    if img.alpha is not None:
        path = run.work_dir / "alpha_mask.png"
        path.write_bytes(mask_to_png_bytes(mask_from_alpha(img.alpha)))
        mask_id = run.store_asset("mask", path, "image/png", source_asset_id=asset.id,
                                  meta={"origin": "alpha", "suggested": True})
        outputs.append(mask_id)
        result["suggested_mask_asset_id"] = mask_id
    return outputs, result


def _mask(run: JobRun, asset, data: bytes):
    from PIL import Image

    try:
        m = load_mask_png(data)
    except Exception as e:  # noqa: BLE001 - anything undecodable is rejected
        raise MediaError("unsupported_format") from e
    with Image.open(io.BytesIO(data)) as im:
        asset.width, asset.height = im.size
    asset.meta_json = {"origin": "brush", "coverage": float((m >= 0.5).mean())}
    asset.state = "ready"
    run.db.commit()
    return [str(asset.id)], {}
