"""camera.export: MP4 (the source job's video), path JSON, guides ZIP and recipe ZIP of a guide/cinematic job.

Every document references the SOURCE job's input revision (the export job runs pinned to it). The recipe packs
what reproduces the pipeline (config, compiled path, workflow graphs/bindings, model manifest, input hashes);
Geometry reproduces deterministically, Cinematic is AI generated and has no exact camera control.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from sqlalchemy import select

from oc_api.apps.camera_sketch import MODULE as CAMERA_APP
from oc_api.handoffs import handoff_readme_lines, project_handoff_meta, project_upstream_mock
from oc_api.models import Job, JobStage
from oc_api.settings import REPO_ROOT
from open_creative.comfy.profiles import PROFILES

from ..stages import JobRun, StageFailed
from .camera_common import (
    CINEMATIC_LABEL,
    CINEMATIC_LABEL_KO,
    GEOMETRY_LABEL,
    GEOMETRY_LABEL_KO,
    json_asset,
    revision_config,
    source_job,
)

MODEL_MANIFEST = REPO_ROOT / "models" / "camera-sketch.manifest.json"
WORKFLOW_APP_DIR = "camera-sketch"


def _guide_of(run: JobRun, src: Job) -> Job:
    if src.kind == "camera.guide":
        return src
    return source_job(run, (src.result_json or {}).get("guide_job_id"), ("camera.guide",), "guide_not_found",
                      "The cinematic job's guide was not found.")


def _path_doc(run: JobRun, src: Job, guide: Job) -> dict:
    art = json_asset(run, guide.result_json["path_asset_id"])
    user_cfg, _ = revision_config(run, src.input_revision)
    return {"schema": "camera-sketch.path_export.v1", "revision": src.input_revision, "source_job_id": str(src.id),
            "source_kind": src.kind, "mode": "geometry" if src.kind == "camera.guide" else "cinematic",
            "guide_job_id": str(guide.id), "guide_revision": guide.input_revision,
            "geometry_hash": guide.result_json.get("geometry_hash"), "user_config": user_cfg,
            "compiled": art["compiled"]}


def _store_json(run: JobRun, role: str, name: str, doc: dict, src: Job, source_asset_id) -> str:
    path = run.work_dir / name
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    return run.store_asset(role, path, "application/json", source_asset_id=source_asset_id,
                           meta={"revision": src.input_revision, "source_job_id": str(src.id)})


def _guides_zip(run: JobRun, src: Job) -> str:
    res = src.result_json
    dep = run.fetch(run.asset(res["guide_depth_video_asset_id"]), "guide_depth.mp4")
    val = run.fetch(run.asset(res["guide_validity_asset_id"]), "guide_validity.zip")
    with zipfile.ZipFile(val) as vz:
        validity_manifest = json.loads(vz.read("manifest.json"))
    manifest = {"revision": src.input_revision, "source_job_id": str(src.id),
                "geometry_hash": res.get("geometry_hash"), "frame_count": res["frame_count"],
                "files": {"guide_depth.mp4": "fixed-range depth guide, inverse depth, near = white",
                          "guide_validity.zip": "validity_###.png 1-bit masks (white = valid) + manifest.json"},
                "depth_guide": res.get("depth_guide"), "fill_strategy": res.get("fill_strategy"),
                "hole_ratio_max": res.get("hole_ratio_max"), "hole_ratio": validity_manifest.get("hole_ratio")}
    path = run.work_dir / "guides.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(dep, "guide_depth.mp4")
        z.write(val, "guide_validity.zip")
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    return run.store_asset("guides_export", path, "application/zip", source_asset_id=res["guide_depth_video_asset_id"],
                           meta={"revision": src.input_revision, "source_job_id": str(src.id)})


def _is_mock(src: Job, guide: Job, depth_meta: dict) -> bool:
    profiles = [PROFILES.get(j.model_profile) for j in (src, guide)]
    return bool(depth_meta.get("mock")) or any(p is not None and p.is_mock for p in profiles)


def _readme(src: Job, guide: Job, cfg: dict, path_doc: dict, inputs: dict, mock: bool,
            handoff: dict | None = None) -> str:
    cinematic = src.kind == "camera.cinematic"
    profile = PROFILES.get(src.model_profile)
    compiled = path_doc["compiled"]
    mock_line = ("**Mock profile** — depth and the cinematic video were deterministic CPU stand-ins (Mock), not "
                 "Depth Anything / LTX output.\n\n" if mock else "")
    this = (f"**This export: Cinematic — {CINEMATIC_LABEL} ({CINEMATIC_LABEL_KO}).**" if cinematic else
            f"**This export: Geometry — {GEOMETRY_LABEL} ({GEOMETRY_LABEL_KO}).**")
    gen = cfg["generation"]
    lines = [
        "# Camera Sketch recipe\n", mock_line + handoff_readme_lines(handoff) + this + "\n",
        "## Modes\n",
        (f"- Geometry = {GEOMETRY_LABEL} ({GEOMETRY_LABEL_KO}): a deterministic 2.5D render of the compiled camera "
         "path over the scene's relative depth. Same inputs, config and solver version give the same frames."),
        (f"- Cinematic = {CINEMATIC_LABEL} ({CINEMATIC_LABEL_KO}): LTX video generation guided by the Geometry "
         "render (RGB + fixed-range depth). It follows the path only approximately: no exact camera control.\n"),
        "## This result\n",
        f"- Source job: `{src.id}` ({src.kind}), project revision {src.input_revision}",
        f"- Guide job: `{guide.id}`, revision {guide.input_revision}, geometry hash `{path_doc['geometry_hash']}`",
        f"- Execution profile: {profile.label if profile else src.model_profile} (`{src.model_profile}`)",
        (f"- Path: preset `{compiled['preset']}`, params {json.dumps(compiled['params'])}, easing "
         f"`{compiled['easing']}`, solver {compiled.get('solver_version')}, status `{compiled['status']}`"),
        f"- Output: {cfg['output']['aspect']}, {cfg['output']['duration_ms']} ms @ {cfg['output']['fps']} fps",
    ]
    if cinematic:
        lines.append(f"- Generation: seed {gen['seed']}, control preset `{gen['control_preset']}`")
    lines.append("- Input hashes (sha256, also in `inputs.json`): " + ", ".join(
        f"{k} `{v['sha256']}`" for k, v in inputs.items()) + "\n")
    lines += [
        "## Reproduce\n",
        ("1. Install this repository and the models listed in `models/camera-sketch.manifest.json` "
         "(Depth Anything V2 Small; LTX-2.3 distilled with the matching union IC-LoRA)."),
        ("2. Deploy the workflows from `workflows/camera-sketch/` (copied under `workflows/` here) with their "
         "binding manifests."),
        ("3. Upload the scene image whose hash is in `inputs.json`, run analyze (or use the recorded depth map), "
         "apply `config.json`, then render the guide; `path.json` holds the user config and the compiled path."),
        "4. For Cinematic, run the cinematic job on that guide with the same seed and control preset.\n",
        ("Geometry frames are deterministic. Cinematic reproduces the same pipeline (graph, models, settings, seed); "
         "pixel-identical generated video is not guaranteed across GPUs or library versions.\n"),
    ]
    return "\n".join(lines)


def _job_doc(run: JobRun, j: Job) -> dict:
    stages = run.db.scalars(select(JobStage).where(JobStage.job_id == j.id)).all()
    return {"id": str(j.id), "kind": j.kind, "revision": j.input_revision, "profile": j.model_profile,
            "options": j.options_json,
            "stages": [{"name": s.stage_name, "state": s.state, "workflow_hash": s.workflow_hash,
                        "model_manifest_hash": s.model_manifest_hash, "build_id": s.build_id} for s in stages]}


def _recipe(run: JobRun, src: Job, guide: Job) -> str:
    cfg, _ = revision_config(run, src.input_revision)
    path_doc = _path_doc(run, src, guide)
    scene, depth = run.asset(cfg["scene_asset_id"]), run.asset(cfg["depth_asset_id"])
    depth_meta = depth.meta_json or {}
    inputs = {
        "scene": {"sha256": scene.sha256, "mime": scene.mime, "width": scene.width, "height": scene.height,
                  "original_sha256": (scene.meta_json or {}).get("original_sha256")},
        "depth": {"sha256": depth.sha256, "mime": depth.mime, "width": depth.width, "height": depth.height,
                  **{k: depth_meta.get(k) for k in ("convention", "min", "max", "model", "mock")}},
    }
    related = [src] + ([guide] if guide is not src else [])
    if depth.job_id:
        analyze = run.db.get(Job, depth.job_id)
        if analyze is not None and analyze not in related:
            related.append(analyze)
    mock = _is_mock(src, guide, depth_meta)
    handoff = project_handoff_meta(run.db, run.project)
    stored_mock = mock or project_upstream_mock(run.db, run.project)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", _readme(src, guide, cfg, path_doc, inputs, mock, handoff))
        z.writestr("config.json", json.dumps({"revision": src.input_revision, "config": cfg, "handoff": handoff}, indent=2))
        z.writestr("path.json", json.dumps(path_doc, indent=2))
        z.writestr("jobs.json", json.dumps([_job_doc(run, j) for j in related], indent=2))
        z.writestr("inputs.json", json.dumps(inputs, indent=2))
        if src.kind == "camera.cinematic":
            z.writestr("qa.json", json.dumps(src.result_json.get("qa"), indent=2))
        else:
            z.writestr("diagnostics.json", json.dumps(
                {k: src.result_json.get(k) for k in ("hole_ratio_max", "hole_ratio_mean", "target_visibility_min",
                                                     "framing_rms_rel", "warnings", "fill_strategy")}, indent=2))
        z.write(MODEL_MANIFEST, "models/camera-sketch.manifest.json")
        wf_dir = Path(run.ctx.settings.workflows_dir) / WORKFLOW_APP_DIR
        if wf_dir.is_dir():
            for f in sorted(wf_dir.glob("*.json")):
                z.write(f, f"workflows/{f.name}")
            if (wf_dir / "README.md").exists():
                z.write(wf_dir / "README.md", "workflows/README.md")
    path = run.work_dir / "recipe.zip"
    path.write_bytes(buf.getvalue())
    return run.store_asset("recipe", path, "application/zip", source_asset_id=src.output_asset_ids[0],
                           meta={"revision": src.input_revision, "source_job_id": str(src.id),
                                 **({"mock": True} if stored_mock else {})})


def camera_export(run: JobRun) -> tuple[list[str], dict]:
    opts = run.job.options_json
    src = source_job(run, opts.get("source_job_id"), tuple(CAMERA_APP.export_formats), "render_not_found",
                     "Render job not found.")
    fmt = opts["format"]
    if fmt not in CAMERA_APP.export_formats[src.kind]:
        raise StageFailed("invalid_format", f"unknown export format {fmt}")
    res = src.result_json or {}
    guide = _guide_of(run, src)
    if fmt == "mp4":
        outputs = [res["geometry_video_asset_id"] if src.kind == "camera.guide" else res["cinematic_video_asset_id"]]
    elif fmt == "path_json":
        outputs = run.ensure_stage("path_json", lambda st: [_store_json(
            run, "path_export", "path.json", _path_doc(run, src, guide), src, guide.result_json["path_asset_id"])])
    elif fmt == "guides_zip":
        outputs = run.ensure_stage("guides_zip", lambda st: [_guides_zip(run, src)])
    else:  # recipe_zip
        outputs = run.ensure_stage("recipe", lambda st: [_recipe(run, src, guide)])
    return outputs, {"format": fmt, "source_job_id": str(src.id), "revision": src.input_revision,
                     "mode": "geometry" if src.kind == "camera.guide" else "cinematic"}
