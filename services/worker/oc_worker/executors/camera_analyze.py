"""camera.analyze: scene image -> relative inverse depth (Depth Anything V2 Small; Mock on mock_v1).

Stores the provider's 16-bit PNG as-is (``depth``), a low-resolution copy (``depth_lowres``: area downsample to
``LOWRES_DEPTH_MAX_SIDE`` long side, its own min/max, same meta contract plus ``source_depth_asset_id``; the depth
asset's meta records it as ``lowres_asset_id``) for the compile endpoint's hole estimate, and an 8-bit preview
(near = bright). The project config is never patched here: the client applies ``depth_asset_id`` with a PATCH (lineage-checked by the API).
"""

from __future__ import annotations

import json

import numpy as np
from PIL import Image

from open_creative.camera_sketch.camera import intrinsics
from open_creative.camera_sketch.scene import (
    DAV2_SMALL,
    LOWRES_DEPTH_MAX_SIDE,
    decode_depth_png16,
    downsample_depth,
    encode_depth_png16,
)

from ..stages import JobRun, StageFailed
from .camera_common import ready_asset

DEPTH_MODEL = "depth-anything-v2-small"
SUGGESTED_FOCAL_MM = 50
SENSOR_WIDTH_MM = 36


def depth_preview(d: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """8-bit preview of inverse depth: linear ``min..max -> 0..255`` (larger inverse depth = nearer = bright)."""
    span = hi - lo
    if not span > 0:
        return np.zeros(d.shape, np.uint8)
    return np.clip(np.rint((d.astype(np.float64) - lo) / span * 255.0), 0, 255).astype(np.uint8)


def camera_analyze(run: JobRun) -> tuple[list[str], dict]:
    cfg = run.config()
    scene = ready_asset(run, cfg.scene_asset_id, "scene", "scene_required")

    def produce(stage):
        src = run.fetch(scene, "scene.png")
        files = run.provider_stage(stage, "camera_depth_v1", {"image": str(src)})
        png = next((p for p in files if p.suffix == ".png"), None)
        side = next((p for p in files if p.suffix == ".json"), None)
        if png is None or side is None:
            raise StageFailed("depth_invalid", "The depth service returned no depth map.")
        provider = json.loads(side.read_text())
        try:
            d = decode_depth_png16(png.read_bytes(), provider)
        except (ValueError, KeyError, TypeError) as e:
            raise StageFailed("depth_invalid", "The depth map could not be decoded.") from e
        if d.shape != (scene.height, scene.width):
            raise StageFailed("depth_invalid", "The depth map size does not match the scene image.")
        conv = DAV2_SMALL
        meta = {"source_asset_id": str(scene.id),
                "convention": {"kind": conv.kind, "scale": conv.scale, "offset": conv.offset, "eps": conv.eps},
                "min": float(provider["min"]), "max": float(provider["max"]), "model": DEPTH_MODEL,
                "revision": run.job.input_revision, "profile": run.job.model_profile}
        if provider.get("mock"):
            meta.update(mock=True, mock_source=provider.get("source"), provider_model=provider.get("model"))
        depth_id = run.store_asset("depth", png, "image/png", meta=meta, source_asset_id=scene.id)
        low_png, low_rng = encode_depth_png16(downsample_depth(d, LOWRES_DEPTH_MAX_SIDE))
        low = run.work_dir / "depth_lowres.png"
        low.write_bytes(low_png)
        low_id = run.store_asset("depth_lowres", low, "image/png", source_asset_id=depth_id,
                                 meta={**meta, "min": low_rng["min"], "max": low_rng["max"],
                                       "source_depth_asset_id": depth_id, "downsample": "area",
                                       "max_side": LOWRES_DEPTH_MAX_SIDE})
        depth_row = run.asset(depth_id)
        depth_row.meta_json = {**meta, "lowres_asset_id": low_id}
        run.db.commit()
        prev = run.work_dir / "depth_preview.png"
        Image.fromarray(depth_preview(d, meta["min"], meta["max"]), "L").save(prev)
        prev_id = run.store_asset("depth_preview", prev, "image/png", source_asset_id=depth_id,
                                  meta={"mapping": "inverse_depth_linear_near_bright", **(
                                      {"mock": True} if meta.get("mock") else {})})
        return [depth_id, prev_id, low_id]

    outputs = run.ensure_stage("depth", produce)
    K = intrinsics(SUGGESTED_FOCAL_MM, scene.width, scene.height, SENSOR_WIDTH_MM)
    return outputs, {
        "depth_asset_id": outputs[0], "depth_preview_asset_id": outputs[1],
        "depth_lowres_asset_id": outputs[2] if len(outputs) > 2 else None,
        "convention": run.asset(outputs[0]).meta_json["convention"],
        "intrinsics_suggestion": {"focal_length_mm_equiv": SUGGESTED_FOCAL_MM,
                                  "sensor_width_mm_assumed": SENSOR_WIDTH_MM, "width": scene.width,
                                  "height": scene.height, "K": K.tolist()},
        "applied": False, "revision": run.job.input_revision,
    }
