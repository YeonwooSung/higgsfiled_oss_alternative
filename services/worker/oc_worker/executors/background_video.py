from __future__ import annotations

from open_creative.comfy.profiles import background_video_shape
from open_creative.product_lock.prompts import build_video_prompt, load_presets

from ..stages import JobRun


def generate_background_video(run: JobRun, cfg, background_asset_id, seed: int, stage_name: str) -> str:
    background = run.asset(background_asset_id)
    preset = load_presets()[cfg.scene.preset_id]
    prompt = build_video_prompt(preset, cfg.motion)
    w, h, frames = background_video_shape(cfg.output.aspect)

    def produce(stage):
        src = run.fetch(background, "background.png")
        files = run.provider_stage(stage, "product_background_video_v1", {
            "image": str(src), "prompt": prompt, "seed": seed, "width": w, "height": h, "frames": frames})
        return [run.store_asset("background_video", files[0], "video/mp4", source_asset_id=background.id,
                                meta={"prompt": prompt, "seed": seed, "motion": cfg.motion, "preset": preset.ref,
                                      "profile": run.job.model_profile})]

    return run.ensure_stage(stage_name, produce)[0]


def background_video(run: JobRun) -> tuple[list[str], dict]:
    cfg = run.config()
    opts = run.job.options_json
    vid = generate_background_video(run, cfg, opts["background_asset_id"], opts["seed"], "background_video")
    return [vid], {"background_video_asset_id": vid}
