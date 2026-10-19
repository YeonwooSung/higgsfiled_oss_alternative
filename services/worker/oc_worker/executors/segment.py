"""product.segment: click/box prompts → mask. Results never modify project config;
the editor decides whether to apply them, so a late result cannot overwrite a newer manual mask."""

from __future__ import annotations

import numpy as np
from PIL import Image

from open_creative.media.image import to_png_bytes

from ..stages import JobRun, StageFailed


def segment(run: JobRun) -> tuple[list[str], dict]:
    cfg = run.config()
    product = run.asset(cfg.product_asset_id)
    opts = run.job.options_json

    def produce(stage):
        src = run.fetch(product, "product.png")
        prompts = {"points": opts.get("points", []), "box": opts.get("box")}
        (mask_path,) = run.provider_stage(stage, "product_segment_v1", {"image": str(src), "prompts": prompts})[:1]
        with Image.open(mask_path) as im:
            mask = np.asarray(im.convert("L"))
        if mask.shape != (product.height, product.width):
            raise StageFailed("mask_dimension_mismatch", "Segmentation returned a mask of the wrong size.")
        mask_id = run.store_asset("mask", mask_path, "image/png", source_asset_id=product.id,
                                  meta={"origin": "segmentation", "prompts": prompts,
                                        "revision": run.job.input_revision})
        rgb = np.asarray(Image.open(src).convert("RGB"))
        preview = run.work_dir / "cutout.png"
        preview.write_bytes(to_png_bytes(rgb, mask))
        preview_id = run.store_asset("cutout_preview", preview, "image/png", source_asset_id=mask_id)
        return [mask_id, preview_id]

    outputs = run.ensure_stage("segment", produce)
    return outputs, {"mask_asset_id": outputs[0], "cutout_preview_asset_id": outputs[1], "applied": False,
                     "revision": run.job.input_revision}
