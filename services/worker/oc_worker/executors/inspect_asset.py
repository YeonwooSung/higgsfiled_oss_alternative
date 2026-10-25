"""asset.inspect: decoder-verified normalization of uploads (originals are preserved)."""

from __future__ import annotations

import hashlib
import io
from dataclasses import asdict

from open_creative.media.image import MediaError, load_mask_png, mask_to_png_bytes, normalize_image, to_png_bytes
from open_creative.media.video_ingest import SourceInfo, filmstrip, normalize_clip, probe_source, proxy_size
from open_creative.product_lock.masks import mask_from_alpha

from ..stages import Cancelled, JobRun, LeaseLost, StageFailed
from .camera_common import frame_checkpoint

_VIDEO_ROLES = frozenset({"clip"})


def inspect_asset(run: JobRun) -> tuple[list[str], dict]:
    asset = run.asset(run.job.options_json["asset_id"])
    if asset.state == "ready":
        return [str(asset.id)], dict(run.job.result_json or {})
    run.transition("postprocessing", "inspect")
    try:
        if asset.role in _VIDEO_ROLES:
            return _clip(run, asset)
        data = run.ctx.storage.get_bytes(asset.object_key)
        if asset.role == "product":
            return _product(run, asset, data)
        if asset.role in ("scene", "clean_plate"):
            return _product(run, asset, data, suggest_mask=False)
        return _mask(run, asset, data)
    except MediaError as e:
        asset.state, asset.error_code = "rejected", e.code
        run.db.commit()
        messages = {**_MESSAGES, **_MESSAGES_VIDEO} if asset.role in _VIDEO_ROLES else _MESSAGES
        raise StageFailed(e.code, messages.get(e.code, "The file could not be used.")) from e


_MESSAGES = {
    "too_large": "Images must be 20 MB or smaller.",
    "unsupported_format": "Use a JPG, PNG or WebP image.",
    "too_many_pixels": "Images must be 24 megapixels or smaller.",
    "decode_failed": "The image could not be decoded.",
}
_MESSAGES_VIDEO = {
    "too_large": "Videos must be 100 MB or smaller.",
    "unsupported_format": "Use an MP4, MOV or WebM video.",
    "too_long": "Clips must be 60 seconds or shorter.",
    "unsupported_codec": "This video codec is not supported.",
    "multiple_video_streams": "This video codec is not supported.",
    "too_large_dims": "Videos must be 4096 pixels or smaller on the longest side.",
}


def _with_handoff(asset, meta: dict) -> dict:
    """The inspector replaces the meta; a hand-off input keeps its provenance (and upstream-mock flag)."""
    handoff = (asset.meta_json or {}).get("handoff")
    return {**meta, "handoff": handoff} if handoff is not None else meta


def _product(run: JobRun, asset, data: bytes, suggest_mask: bool = True):
    img = normalize_image(data)
    normalized_key = asset.object_key.replace("-original", "")
    if normalized_key == asset.object_key:
        normalized_key = asset.object_key + ".normalized.png"
    normalized_key = normalized_key.rsplit(".", 1)[0] + ".png"
    png = to_png_bytes(img.rgb, img.alpha)
    run.ctx.storage.put_bytes(normalized_key, png, "image/png")
    asset.meta_json = _with_handoff(asset, {
        "original_key": asset.object_key, "original_mime": img.mime,
        "original_sha256": hashlib.sha256(data).hexdigest(), "has_alpha": img.alpha is not None,
        "normalization": {"exif_orientation": "applied", "color": "sRGB", "metadata": "stripped"}})
    asset.object_key, asset.mime, asset.bytes = normalized_key, "image/png", len(png)
    asset.sha256 = hashlib.sha256(png).hexdigest()
    asset.width, asset.height = img.width, img.height
    asset.state = "ready"
    run.db.commit()
    outputs, result = [str(asset.id)], {"has_alpha": img.alpha is not None}
    if suggest_mask and img.alpha is not None:
        path = run.work_dir / "alpha_mask.png"
        path.write_bytes(mask_to_png_bytes(mask_from_alpha(img.alpha)))
        mask_id = run.store_asset("mask", path, "image/png", source_asset_id=asset.id,
                                  meta={"origin": "alpha", "suggested": True})
        outputs.append(mask_id)
        result["suggested_mask_asset_id"] = mask_id
    return outputs, result


def _mask(run: JobRun, asset, data: bytes):
    from PIL import Image

    if len(data) > 20 * 1024 * 1024:
        raise MediaError("too_large")
    try:
        m = load_mask_png(data)
    except MediaError:
        raise
    except Exception as e:  # noqa: BLE001 - anything undecodable is rejected
        raise MediaError("unsupported_format") from e
    with Image.open(io.BytesIO(data)) as im:
        asset.width, asset.height = im.size
    asset.meta_json = {"origin": "brush", "coverage": float((m >= 0.5).mean())}
    asset.state = "ready"
    run.db.commit()
    return [str(asset.id)], {}


def _preview_size(info: SourceInfo) -> tuple[int, int]:
    """Display aspect fitted inside the proxy box, even dimensions (aspect preserved, no letterbox)."""
    bw, bh = proxy_size(info.display_w, info.display_h)
    scale = min(bw / info.display_w, bh / info.display_h, 1.0)
    return (max(2, round(info.display_w * scale / 2) * 2), max(2, round(info.display_h * scale / 2) * 2))


def _clip(run: JobRun, asset):
    """Probe the uploaded clip, then store a proxy-size CFR 24 preview of the whole source and a filmstrip.
    The original stays untouched; the preview is never the original."""
    src = run.fetch(asset)

    def on_frame(i: int) -> None:  # a 60 s 4K clip decodes for longer than the lease: heartbeat + cancel per frame
        frame_checkpoint(run, i)

    try:
        info = probe_source(src, on_frame=on_frame)
    except (MediaError, Cancelled, LeaseLost):
        raise
    except Exception as e:  # noqa: BLE001 - ffprobe failures on undecodable bytes
        raise MediaError("unsupported_codec") from e
    run.heartbeat(force=True)
    pw, ph = _preview_size(info)
    preview = run.work_dir / "clip_preview.mp4"
    try:
        normalize_clip(src, preview, info, 0, info.duration_ms, pw, ph, on_frame=on_frame)
    except (MediaError, Cancelled, LeaseLost):
        raise
    except Exception as e:  # noqa: BLE001
        raise MediaError("unsupported_codec") from e
    strip = run.work_dir / "clip_filmstrip.png"
    indices = filmstrip(preview, strip)
    # Derivatives first, then `ready`: a client stops polling at `ready` and must find the preview and filmstrip; a
    # crash in between leaves the clip `uploaded`, so the retry makes them again (the early `ready` return is safe).
    preview_id = run.store_asset("clip_preview", preview, "video/mp4", source_asset_id=asset.id,
                                 meta={"fps": 24, "rotation_applied": info.rotation})
    strip_id = run.store_asset("clip_filmstrip", strip, "image/png", source_asset_id=asset.id,
                               meta={"frame_indices": indices})
    asset.meta_json = _with_handoff(asset, {"source": asdict(info),
                                            "original_sha256": asset.sha256 or _sha256_file(src)})
    asset.width, asset.height = info.display_w, info.display_h
    asset.duration_ms, asset.frame_count = info.duration_ms, info.frame_count
    asset.state = "ready"
    run.db.commit()
    return [str(asset.id), preview_id, strip_id], {"preview_asset_id": preview_id, "filmstrip_asset_id": strip_id}


def _sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()
