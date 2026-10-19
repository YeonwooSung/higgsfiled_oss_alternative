"""camera.cinematic: Geometry guides of a succeeded guide job -> LTX (camera_cinematic_v1) -> 120 frames.

The model shape (``background_video_shape``: 1280x736 / 736x1280, 121 frames) exists only inside this stage:
guides are edge-padded to it and the result is center-cropped / trimmed back to the output contract.
The result is AI generated and never labelled exact camera control; QA is a generated-motion report against
the Geometry render (``unknown`` when tracking fails). Geometry assets of the guide job are only read.
"""

from __future__ import annotations

import io
import json
import zipfile

import numpy as np
from PIL import Image

from oc_api.camera_sketch.jobs import geometry_hash
from open_creative.camera_sketch.guides import crop_from_model, pad_to_model
from open_creative.camera_sketch.qa import motion_report
from open_creative.camera_sketch.render import nearest_valid_index
from open_creative.comfy.profiles import (
    CONTROL_PRESETS,
    background_video_shape,
    get_profile,
)
from open_creative.media.video import decode_frames, encode_mp4, probe

from ..stages import JobRun, StageFailed
from .camera_common import (
    CINEMATIC_LABEL,
    CINEMATIC_LABEL_KO,
    json_asset,
    output_shape,
    ready_asset,
    source_job,
    target_px_out,
    with_heartbeat,
)

BASE_PROMPT = ("A smooth, steady cinematic camera move through the same scene. Keep the subject, layout, "
               "lighting and materials of the reference image unchanged. Natural, photorealistic detail.")
# mock_v1 ignores control_strength; while CONTROL_PRESETS are uncalibrated (None) the mock path sends this
# documented placeholder instead of an invented strength. open_weights_v1 never runs with it.
MOCK_CONTROL_STRENGTH = 0.0


def cinematic_prompt(extra: str) -> str:
    extra = (extra or "").strip()
    return f"{BASE_PROMPT} {extra}" if extra else BASE_PROMPT


def control_strength(profile_id: str, preset: str) -> float:
    value = CONTROL_PRESETS.get(preset)
    if value is not None:
        return float(value)
    if get_profile(profile_id).is_mock:
        return MOCK_CONTROL_STRENGTH
    raise StageFailed("control_uncalibrated", "Control strength presets are not calibrated for this profile.")


def guide_rgb_frames(geometry_mp4, validity_zip):
    """Geometry frames with the remaining invalid pixels (validity mask) set to the nearest valid pixel."""
    with zipfile.ZipFile(validity_zip) as zf:
        for i, f in enumerate(decode_frames(geometry_mp4)):
            valid = np.asarray(Image.open(io.BytesIO(zf.read(f"validity_{i:03d}.png"))).convert("1"), bool)
            if valid.shape != f.shape[:2]:
                raise StageFailed("guide_invalid", "Guide validity does not match the guide frames.")
            if valid.all() or not valid.any():
                yield f
                continue
            yield f.reshape(-1, 3)[nearest_valid_index(valid).ravel()].reshape(f.shape)


def _check_guide(run: JobRun, cfg, n: int):
    guide = source_job(run, run.job.options_json["guide_job_id"], ("camera.guide",), "guide_not_found",
                       "Guide job not found.")
    res = guide.result_json or {}
    if res.get("geometry_hash") != geometry_hash(cfg):
        raise StageFailed("guide_stale", "The camera path changed after the guide was rendered.")
    if res.get("frame_count") != n:
        raise StageFailed("frame_count_mismatch", f"The guide does not have {n} frames.")
    return guide, res


def camera_cinematic(run: JobRun) -> tuple[list[str], dict]:
    cfg = run.config()
    out_w, out_h, n = output_shape(cfg)
    guide, gres = _check_guide(run, cfg, n)
    strength = control_strength(run.job.model_profile, cfg.generation.control_preset)
    model_w, model_h, model_n = background_video_shape(cfg.output.aspect)
    prompt = cinematic_prompt(cfg.generation.extra_prompt)
    seed = cfg.generation.seed
    scene = ready_asset(run, cfg.scene_asset_id, "scene", "scene_required")
    geo_asset = run.asset(gres["geometry_video_asset_id"])

    def bindings() -> dict:
        run.transition("preparing", "prepare")
        geo = run.fetch(geo_asset, "geometry.mp4")
        dep = run.fetch(run.asset(gres["guide_depth_video_asset_id"]), "guide_depth.mp4")
        val = run.fetch(run.asset(gres["guide_validity_asset_id"]), "validity.zip")
        rgb_model, dep_model = run.work_dir / "guide_rgb_model.mp4", run.work_dir / "guide_depth_model.mp4"
        encode_mp4(pad_to_model(with_heartbeat(run, guide_rgb_frames(geo, val)), model_w, model_h, model_n),
                   rgb_model, model_w, model_h, fps=cfg.output.fps)
        encode_mp4(pad_to_model(with_heartbeat(run, decode_frames(dep)), model_w, model_h, model_n),
                   dep_model, model_w, model_h, fps=cfg.output.fps)
        ref = run.fetch(scene, "reference.png")
        return {"guide_rgb": str(rgb_model), "guide_depth": str(dep_model), "reference": str(ref),
                "prompt": prompt, "seed": seed, "width": model_w, "height": model_h, "frames": model_n,
                "control_strength": strength}

    def generate(stage):
        # Guides are prepared only when a submission is still needed; a resumed stage inspects its provider job.
        b = bindings() if stage.provider_job_id is None else {}
        files = run.provider_stage(stage, "camera_cinematic_v1", b)
        mp4 = next((p for p in files if p.suffix == ".mp4"), None)
        if mp4 is None:
            raise StageFailed("provider_failed", "The generation service returned no video.")
        side = next((p for p in files if p.suffix == ".json"), None)
        provider = json.loads(side.read_text()) if side else {}
        run.transition("postprocessing", "postprocess")
        out = run.work_dir / "cinematic.mp4"
        try:
            encode_mp4(crop_from_model(with_heartbeat(run, decode_frames(mp4)), out_w, out_h, n), out,
                       out_w, out_h, fps=cfg.output.fps)
        except ValueError as e:
            raise StageFailed("generated_shape_mismatch", "The generated video does not match the model shape.") from e
        info = probe(out)
        if (info["width"], info["height"], info["frame_count"]) != (out_w, out_h, n):
            raise StageFailed("encode_mismatch", "The encoded video does not match the output contract.")
        mock = bool(provider.get("mock")) or get_profile(run.job.model_profile).is_mock
        meta = {"mode": "cinematic", "label": CINEMATIC_LABEL, "label_ko": CINEMATIC_LABEL_KO,
                "exact_camera_control": False, "revision": run.job.input_revision, "guide_job_id": str(guide.id),
                "guide_revision": guide.input_revision, "geometry_hash": gres["geometry_hash"], "seed": seed,
                "prompt": prompt, "control_preset": cfg.generation.control_preset, "control_strength": strength,
                "model_shape": {"width": model_w, "height": model_h, "frames": model_n},
                "guide_fill_strategy": gres.get("fill_strategy"), "profile": run.job.model_profile,
                **({"mock": True} if mock else {})}
        return [run.store_asset("cinematic_video", out, "video/mp4", source_asset_id=geo_asset.id, meta=meta)]

    (video_id,) = run.ensure_stage("generate", generate)

    def qa(stage):
        run.transition("postprocessing", "qa")
        art = json_asset(run, gres["path_asset_id"])  # stored path artifact; never recompiled
        target = target_px_out(art)
        gen = run.fetch(run.asset(video_id), "cinematic.mp4")
        geo = run.fetch(geo_asset, "geometry.mp4")
        report = motion_report(with_heartbeat(run, decode_frames(gen)), decode_frames(geo), target)
        report = {**report, "target_px_out": list(target), "reference": "geometry_video",
                  "exact_camera_control": False}
        path = run.work_dir / "motion_report.json"
        path.write_text(json.dumps(report, indent=2))
        return [run.store_asset("motion_report", path, "application/json", source_asset_id=video_id,
                                meta={"status": report["status"], "revision": run.job.input_revision})]

    (qa_id,) = run.ensure_stage("qa", qa)
    meta = run.asset(video_id).meta_json
    return [video_id, qa_id], {
        "mode": "cinematic", "label": CINEMATIC_LABEL, "exact_camera_control": False, "frame_count": n,
        "guide_job_id": str(guide.id), "qa": json_asset(run, qa_id), "cinematic_video_asset_id": video_id,
        "motion_report_asset_id": qa_id, "revision": run.job.input_revision, "mock": bool(meta.get("mock")),
        "seed": seed, "control_preset": cfg.generation.control_preset,
    }
