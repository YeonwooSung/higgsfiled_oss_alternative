from __future__ import annotations

from open_creative.comfy.profiles import background_image_size
from open_creative.product_lock.prompts import build_background_prompt, load_presets

from ..stages import JobRun


def background_image(run: JobRun) -> tuple[list[str], dict]:
    cfg = run.config()
    opts = run.job.options_json
    preset = load_presets()[opts["preset_id"]]
    prompt, preset_ref = build_background_prompt(preset, cfg.scene.accent_hex, "center", cfg.scene.extra_prompt)
    w, h = background_image_size(cfg.output.aspect)
    meta = {"preset": preset_ref, "prompt": prompt, "seed": opts["seed"], "aspect": cfg.output.aspect,
            "profile": run.job.model_profile}

    def produce(stage):
        files = run.provider_stage(stage, "product_background_image_v1",
                                   {"prompt": prompt, "seed": opts["seed"], "width": w, "height": h})
        if not files:
            raise ValueError("provider returned no image")
        return [run.store_asset("background", files[0], "image/png", meta=meta)]

    outputs = run.ensure_stage("background_image", produce)
    return outputs, {"preset": preset_ref, "prompt": prompt}
