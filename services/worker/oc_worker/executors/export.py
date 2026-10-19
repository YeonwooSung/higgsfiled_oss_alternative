"""export: MP4 / cover PNG reference the render outputs; recipe ZIP packages everything needed to
reproduce the ad with the same graph, models, settings and input hashes (not bit-exact pixels)."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path

from sqlalchemy import select

from oc_api.models import Job, JobStage, ProjectRevision
from oc_api.settings import REPO_ROOT
from open_creative.comfy.profiles import get_profile
from open_creative.product_lock.prompts import load_presets

from ..stages import JobRun, StageFailed

MODEL_MANIFEST = REPO_ROOT / "models" / "product-lock.manifest.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _readme(profile, revision: int, cfg: dict, integrity: dict) -> str:
    mock = "**Mock profile** — backgrounds were synthetic stand-ins, not generative output.\n\n" if profile.is_mock else ""
    verdict = "passed" if integrity.get("passed") else "did NOT pass"
    return (
        f"# Product Lock recipe\n\n{mock}"
        f"- Execution profile: {profile.label} (`{profile.id}`)\n"
        f"- Project revision: {revision}\n"
        f"- Preset: `{cfg['scene']['preset_id']}`, seed {cfg['seed']}, aspect {cfg['output']['aspect']}\n"
        f"- Pre-encode protected-core check {verdict} "
        f"(max channel error {integrity.get('preencode_max_error')} / threshold {integrity.get('threshold')}).\n\n"
        "## Reproduce\n\n"
        "1. Install this repository and the models listed in `models/product-lock.manifest.json`.\n"
        "2. Deploy the API graphs in `workflows/` with their binding manifests.\n"
        "3. Upload the inputs whose hashes are in `inputs.json` and apply `config.json`.\n\n"
        "The same graph, models, settings and inputs reproduce the same pipeline. Pixel-identical generated "
        "backgrounds are not guaranteed across GPUs or library versions. The product core is composited from "
        "the original pixels, so it does not depend on the generator.\n"
    )


def _recipe(run: JobRun, render: Job) -> str:
    rev = run.db.get(ProjectRevision, (run.project.id, render.input_revision))
    cfg = rev.config_json
    integrity = (render.result_json or {}).get("integrity", {})
    profile = get_profile(render.model_profile)
    preset = load_presets()[cfg["scene"]["preset_id"]]

    related = [render]
    bg_video_id = (render.result_json or {}).get("background_video_asset_id")
    for aid in filter(None, [cfg["scene"].get("background_asset_id"), bg_video_id]):
        producer = run.asset(aid).job_id
        if producer:
            job = run.db.get(Job, producer)
            if job and job not in related:
                related.append(job)
    jobs = []
    for j in related:
        stages = run.db.scalars(select(JobStage).where(JobStage.job_id == j.id)).all()
        jobs.append({"id": str(j.id), "kind": j.kind, "revision": j.input_revision, "profile": j.model_profile,
                     "options": j.options_json,
                     "stages": [{"name": s.stage_name, "state": s.state, "workflow_hash": s.workflow_hash,
                                 "model_manifest_hash": s.model_manifest_hash, "build_id": s.build_id}
                                for s in stages]})
    inputs = {}
    for role, aid in (("product", cfg.get("product_asset_id")), ("mask", cfg.get("mask_asset_id")),
                      ("core_mask", cfg.get("core_mask_asset_id")),
                      ("background", cfg["scene"].get("background_asset_id")),
                      ("background_video", bg_video_id)):
        if aid:
            a = run.asset(aid)
            inputs[role] = {"sha256": a.sha256, "mime": a.mime, "width": a.width, "height": a.height,
                            "original_sha256": (a.meta_json or {}).get("original_sha256")}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", _readme(profile, render.input_revision, cfg, integrity))
        z.writestr("config.json", json.dumps({"revision": render.input_revision, "config": cfg}, indent=2))
        z.writestr("preset.json", json.dumps({**preset.public(), "prompt": preset.prompt,
                                               "negative": preset.negative, "video_prompt": preset.video_prompt},
                                              indent=2))
        z.writestr("integrity.json", json.dumps(integrity, indent=2))
        z.writestr("jobs.json", json.dumps(jobs, indent=2))
        z.writestr("inputs.json", json.dumps(inputs, indent=2))
        z.write(MODEL_MANIFEST, "models/product-lock.manifest.json")
        wf_dir = Path(run.ctx.settings.workflows_dir)
        if wf_dir.is_dir():
            for f in sorted(wf_dir.glob("*.json")):
                z.write(f, f"workflows/{f.name}")
            if (wf_dir / "README.md").exists():
                z.write(wf_dir / "README.md", "workflows/README.md")
    path = run.work_dir / "recipe.zip"
    path.write_bytes(buf.getvalue())
    return run.store_asset("recipe", path, "application/zip", source_asset_id=render.output_asset_ids[0],
                           meta={"revision": render.input_revision})


def export(run: JobRun) -> tuple[list[str], dict]:
    opts = run.job.options_json
    render = run.db.get(Job, uuid.UUID(opts["render_job_id"]))
    if render is None or render.project_id != run.project.id or render.state != "succeeded":
        raise StageFailed("render_not_found", "Render job not found.")
    fmt = opts["format"]
    if fmt == "mp4":
        outputs = [render.output_asset_ids[0]]
    elif fmt == "cover_png":
        outputs = [render.output_asset_ids[1]]
    elif fmt == "recipe_zip":
        outputs = run.ensure_stage("recipe", lambda st: [_recipe(run, render)])
    else:
        raise StageFailed("invalid_format", f"unknown export format {fmt}")
    return outputs, {"format": fmt, "render_job_id": str(render.id), "revision": render.input_revision}
