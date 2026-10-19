import io
import json
import zipfile

from oc_api.models import Asset, Job
from oc_worker.main import run_once
from test_executors import prepared_project, run_job
from worker_helpers import add_job, load, set_revision_config


def test_exports_reference_outputs_and_build_recipe(ctx, db_factory, storage, project):
    aid, mask_id, cfg = prepared_project(ctx, db_factory, storage, project)
    bg = run_job(ctx, db_factory, project, "product.background", {"preset_id": "neon_studio_v1", "seed": 5})
    cfg["scene"]["background_asset_id"] = bg.output_asset_ids[0]
    set_revision_config(db_factory, project, cfg, revision=2)
    render = run_job(ctx, db_factory, project, "product.render", {}, revision=2)

    mp4 = run_job(ctx, db_factory, project, "export", {"render_job_id": str(render.id), "format": "mp4"},
                  uses_gpu=False)
    assert mp4.output_asset_ids == [render.output_asset_ids[0]]
    cover = run_job(ctx, db_factory, project, "export", {"render_job_id": str(render.id), "format": "cover_png"},
                    uses_gpu=False)
    assert cover.output_asset_ids == [render.output_asset_ids[1]]

    recipe = run_job(ctx, db_factory, project, "export", {"render_job_id": str(render.id), "format": "recipe_zip"},
                     uses_gpu=False)
    asset = load(db_factory, Asset, recipe.output_asset_ids[0])
    assert asset.role == "recipe" and asset.mime == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(storage.get_bytes(asset.object_key)))
    names = set(z.namelist())
    assert {"README.md", "config.json", "preset.json", "integrity.json", "jobs.json", "inputs.json",
            "models/product-lock.manifest.json"} <= names
    config = json.loads(z.read("config.json"))
    assert config["revision"] == 2 and config["config"]["scene"]["preset_id"] == "neon_studio_v1"
    jobs = json.loads(z.read("jobs.json"))
    assert {j["kind"] for j in jobs} >= {"product.render"}
    assert all("submission_key" not in s for j in jobs for s in j["stages"])
    assert "Mock" in z.read("README.md").decode()
    inputs = json.loads(z.read("inputs.json"))
    assert inputs["product"]["sha256"] and "object_key" not in inputs["product"]
