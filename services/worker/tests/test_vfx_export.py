"""vfx.export: mp4 / cover_png reuse the render's assets; recipe_zip and layers_zip are built in ensure_stage."""

import io
import json
import zipfile

import numpy as np
import pytest
from oc_api.models import Job, JobStage
from oc_worker.executors.vfx_export import effect_rgba
from open_creative.media.video import decode_frames
from open_creative.pocket_vfx.color import linear_to_srgb, srgb_to_linear
from open_creative.pocket_vfx.composite import FrameInputs, composite_frame
from open_creative.pocket_vfx.masks import read_masks_zip, upscale_mask
from open_creative.pocket_vfx.procedural import EffectLayer, render_effect
from open_creative.pocket_vfx.timeline import EffectWindow
from PIL import Image
from sqlalchemy import select
from test_vfx_audio import audio_streams, prepared
from test_vfx_executors import (  # noqa: F401 - pv is the project fixture
    BODY_PROMPT,
    Proj,
    load,
    pv,
    run_kind,
)

QUICK = {"track_job_id": None, "render_profile": "quick"}


@pytest.fixture
def proj(ctx, db_factory, storage, pv):  # noqa: F811
    return Proj(ctx, db_factory, storage, pv)


def export(proj, render, fmt, expect="succeeded"):
    return run_kind(proj.ctx, proj.db_factory, proj.project, "vfx.export",
                    {"render_job_id": str(render.id), "format": fmt}, render.input_revision, expect)


def open_zip(proj, job) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(proj.storage.get_bytes(proj.asset(job.output_asset_ids[0]).object_key)))


def png(z, name) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(z.read(name))))


@pytest.fixture
def masked(proj):
    """A behind_subject render over the stomp clip with SAM-style (mock) subject masks."""
    proj.prepare("stomp_landscape.mp4")
    proj.revise(anchor__follow=False, subject_prompts=BODY_PROMPT)
    track = proj.run("vfx.track")
    render = proj.run("vfx.render", {"track_job_id": str(track.id), "render_profile": "quick"})
    return proj, track, render


def test_mp4_and_cover_are_the_renders_own_assets(masked):
    proj, _, render = masked
    r = render.result_json
    mp4, cover = export(proj, render, "mp4"), export(proj, render, "cover_png")
    assert mp4.output_asset_ids == [r["render_video_asset_id"]] and mp4.result_json["format"] == "mp4"
    assert cover.output_asset_ids == [r["cover_asset_id"]] and proj.asset(cover.output_asset_ids[0]).role == "cover"
    assert mp4.result_json["revision"] == render.input_revision


def test_exported_mp4_of_an_audio_render_is_the_muxed_asset(proj):
    prepared(proj, "with_audio.mp4")
    render = proj.run("vfx.render", QUICK)
    r = render.result_json
    silent = _stage_assets(proj, render, "composite")  # the silent composite also has role render_video
    assert proj.asset(silent[0]).role == "render_video" and silent[0] != r["render_video_asset_id"]
    assert audio_streams(proj.path(silent[0])) == []
    mp4 = export(proj, render, "mp4")
    assert mp4.output_asset_ids == [r["render_video_asset_id"]]
    assert audio_streams(proj.path(mp4.output_asset_ids[0])) == ["aac"]


def _stage_assets(proj, job, name):
    with proj.db_factory() as db:
        st = db.scalar(select(JobStage).where(JobStage.job_id == job.id, JobStage.stage_name == name))
        return [str(a) for a in st.output_asset_ids]


def test_recipe_references_the_revision_track_key_and_notes(masked):
    proj, track, render = masked
    job = export(proj, render, "recipe_zip")
    asset = proj.asset(job.output_asset_ids[0])
    assert asset.role == "recipe" and asset.mime == "application/zip" and asset.meta_json["mock"] is True
    z = open_zip(proj, job)
    names = set(z.namelist())
    # follow=false: no track.json (the anchor does not come from the track), masks.json describes the masks
    assert {"README.md", "config.json", "preset.json", "masks.json", "plate.json", "layers.json", "qa.json",
            "jobs.json", "inputs.json", "models/pocket-vfx.manifest.json"} <= names and "track.json" not in names
    assert "workflows/README.md" in names
    config = json.loads(z.read("config.json"))
    assert config["revision"] == render.input_revision
    assert config["track_key"] == track.result_json["track_key"]
    assert config["config"]["effect"]["preset_id"] == proj.cfg["effect"]["preset_id"]
    readme = z.read("README.md").decode()
    assert "Quick" in readme and "Mock" in readme and "segmentation, not alpha matting" in readme
    assert f"project revision {render.input_revision}" in readme and track.result_json["track_key"] in readme
    qa = json.loads(z.read("qa.json"))
    assert qa["roi_qa"]["status"] == "pass" and qa["occlusion_qa"] == 0
    masks = json.loads(z.read("masks.json"))
    assert masks["masks_key"] == track.result_json["masks"]["masks_key"] and len(masks["manifest"]["frames"]) == 120
    inputs = json.loads(z.read("inputs.json"))
    assert inputs["clip"]["sha256"] and inputs["masks_zip"]["sha256"] and "object_key" not in inputs["clip"]
    jobs = json.loads(z.read("jobs.json"))
    assert {j["kind"] for j in jobs} == {"vfx.render", "vfx.track"}
    assert all("submission_key" not in s for j in jobs for s in j["stages"])


def test_layers_zip_has_every_frame_with_alpha_only_inside_the_roi(masked):
    proj, track, render = masked
    r = render.result_json
    job = export(proj, render, "layers_zip")
    z = open_zip(proj, job)
    names = z.namelist()
    assert [n for n in names if n.startswith("effect/")] == [f"effect/{i:04d}.png" for i in range(120)]
    assert [n for n in names if n.startswith("foreground/")] == [f"foreground/{i:04d}.png" for i in range(120)]
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["frame_count"] == 120 and manifest["fps"] == 24 and manifest["size"] == [1280, 720]
    assert manifest["premultiplied"] is False and manifest["color"] == "sRGB"
    assert manifest["layer_order"] == ["plate", "effect", "foreground"] and "no alpha" in manifest["note"]
    assert "plate -> effect -> foreground" in z.read("README.md").decode()
    readme = z.read("README.md").decode()
    assert "MP4 has no alpha" in readme and "segmentation, not alpha matting" in readme
    stats = proj.json(r["roi_qa_asset_id"])["frame_stats"]
    seq = read_masks_zip(proj.path(track.result_json["masks"]["masks_zip_asset_id"]), expect_frames=120,
                         expect_size=(640, 360))
    drawn = 0
    for i in range(120):
        eff = png(z, f"effect/{i:04d}.png")
        assert eff.shape == (720, 1280, 4)
        ys, xs = np.nonzero(eff[..., 3])
        if len(xs):
            drawn += 1
            x0, y0, x1, y1 = stats["roi_bbox"][i]  # alpha lies inside the render's ROI box
            assert xs.min() >= x0 and xs.max() < x1 and ys.min() >= y0 and ys.max() < y1
        if i % 20 == 0:
            fg = png(z, f"foreground/{i:04d}.png")
            soft = upscale_mask(seq.masks[i], 1280, 720)
            assert np.array_equal(fg[..., 3], np.rint(soft * 255).astype(np.uint8))
            assert fg.shape == (720, 1280, 4)
    assert drawn > 10
    peak = max(range(120), key=lambda i: stats["roi_px"][i])
    eff = png(z, f"effect/{peak:04d}.png")
    assert eff[..., 3].max() > 0 and (eff[..., 3] > 0).sum() <= stats["roi_px"][peak]
    # occluded: no effect alpha on the subject core (render's occlusion QA is 0)
    core = upscale_mask(seq.masks[peak], 1280, 720) >= 1.0
    assert (eff[..., 3][core] == 0).all()


def test_layers_zip_without_masks_omits_the_foreground(proj):
    prepared(proj, "stomp_landscape.mp4")
    render = proj.run("vfx.render", QUICK)
    z = open_zip(proj, export(proj, render, "layers_zip"))
    names = z.namelist()
    assert len([n for n in names if n.startswith("effect/")]) == render.result_json["frame_count"] == 48
    assert not any(n.startswith("foreground/") for n in names)
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["folders"] == ["effect"] and "omitted" in manifest["foreground"]
    assert "No `foreground/`" in z.read("README.md").decode()


def test_effect_layers_composite_back_to_the_render(proj):
    """A quick render with a bundled plate: the straight-alpha sRGB layer blended over the source in linear light
    reproduces the rendered frame (max error <= 2/255 after the 8-bit alpha / sRGB round trip)."""
    prepared(proj, "stomp_landscape.mp4")
    render = proj.run("vfx.render", QUICK)
    assert proj.json(render.result_json["layers_asset_id"])["plate"]["source"] == "bundled"  # PlateLayers in play
    z = open_zip(proj, export(proj, render, "layers_zip"))
    work = list(decode_frames(proj.path(proj.cfg["work_asset_id"])))
    silent = list(decode_frames(proj.path(_stage_assets(proj, render, "composite")[0])))
    peak = max(range(48), key=lambda i: int((silent[i].astype(int) != work[i]).any(axis=2).sum()))
    eff = png(z, f"effect/{peak:04d}.png")
    a = eff[..., 3:].astype(np.float32) / 255.0
    lin = srgb_to_linear(eff[..., :3]) * a + srgb_to_linear(work[peak]) * (1.0 - a)
    err = np.abs(linear_to_srgb(lin).astype(int) - silent[peak].astype(int))
    # the silent composite is H.264: lossy, so only a statistical bound here; the exact bound is the unit test below
    assert (a[..., 0] > 0.5).sum() > 1000 and err.mean() < 2 and np.percentile(err, 99.9) <= 12


def test_effect_rgba_blend_matches_composite_frame_exactly():
    """Pre-encode: straight-alpha sRGB RGBA over the source in linear light == composite_frame (max 2/255)."""
    w, h = 1280, 720
    window = EffectWindow(10, 20, 40)
    params = {"scale": 1.0, "intensity": 0.9, "palette": "amber", "rotation_deg": 0, "squash": 0.5}
    effect = render_effect("impact_wave_v1", params, window, 26, (600.0, 400.0), w, h, 7, None)
    alpha = np.zeros((h, w), np.float32)
    alpha[300:500, 400:900] = 0.4
    plate = EffectLayer(alpha[..., None] * np.asarray((0.2, 0.5, 0.9), np.float32), alpha, (400, 300, 900, 500))
    src = np.random.default_rng(3).integers(0, 256, (h, w, 3), dtype=np.uint8)
    out, _ = composite_frame(FrameInputs(src, effect, plate, None, None, None), "in_front")
    rgba = effect_rgba([plate, effect], None, w, h)
    a = rgba[..., 3:].astype(np.float32) / 255.0
    lin = srgb_to_linear(rgba[..., :3]) * a + srgb_to_linear(src) * (1.0 - a)
    err = np.abs(linear_to_srgb(lin).astype(int) - out.astype(int))
    assert (rgba[..., 3] > 0).sum() > 10000 and err.max() <= 2, err.max()


def _layers(proj, render):
    z = open_zip(proj, export(proj, render, "layers_zip"))
    return z, json.loads(z.read("manifest.json"))


@pytest.mark.parametrize("mode", ["in_front", "split_glow"])
def test_layers_zip_follows_the_layer_order(proj, mode):
    proj.prepare("stomp_landscape.mp4")
    proj.revise(anchor__follow=False, subject_prompts=BODY_PROMPT)
    track = proj.run("vfx.track")
    proj.revise(effect__layer_order=mode)
    render = proj.run("vfx.render", {"track_job_id": str(track.id), "render_profile": "quick"})
    z, manifest = _layers(proj, render)
    names = z.namelist()
    glow = [n for n in names if n.startswith("glow/")]
    assert manifest["layer_order_mode"] == mode and manifest["premultiplied"] is False
    assert len([n for n in names if n.startswith("foreground/")]) == 120
    readme = z.read("README.md").decode()
    if mode == "in_front":
        assert manifest["layer_order"] == ["foreground", "plate", "effect"] and not glow
        assert "foreground -> plate -> effect" in readme and "over the subject" in readme
        return
    assert manifest["layer_order"] == ["plate", "effect", "foreground", "glow"] and manifest["folders"][1] == "glow"
    assert len(glow) == 120 and "glow/%04d.png" in readme
    stats = proj.json(render.result_json["roi_qa_asset_id"])["frame_stats"]
    peak = max(range(120), key=lambda i: stats["roi_px"][i])
    g, e = png(z, f"glow/{peak:04d}.png"), png(z, f"effect/{peak:04d}.png")
    assert g[..., 3].max() > 0 and g[..., 3].max() <= round(0.35 * 255) + 1  # the effect at 35 %
    assert (g[..., 3] > 0).sum() > 0 and e[..., 3].max() > 0


def test_recipe_of_a_following_render_includes_the_track(proj):
    proj.prepare("stomp_landscape.mp4")
    proj.revise(anchor__follow=True, effect__layer_order="in_front")
    track = proj.run("vfx.track")
    render = proj.run("vfx.render", {"track_job_id": str(track.id), "render_profile": "quick"})
    z = open_zip(proj, export(proj, render, "recipe_zip"))
    assert "track.json" in z.namelist() and "masks.json" not in z.namelist()
    doc = json.loads(z.read("track.json"))
    assert doc["track_key"] == track.result_json["track_key"] and len(doc["points"]) == 120
    assert json.loads(z.read("plate.json"))["plate"]["source"] == "bundled"


def test_unknown_format_and_unfinished_render_fail(masked):
    proj, _, render = masked
    job = export(proj, render, "gif", expect="failed")
    assert job.error_code == "invalid_format"
    missing = run_kind(proj.ctx, proj.db_factory, proj.project, "vfx.export",
                       {"render_job_id": "00000000-0000-0000-0000-000000000000", "format": "mp4"}, 1,
                       expect="failed")
    assert missing.error_code == "render_not_found"


def test_layers_export_resumes_without_rerunning_the_stage(masked):
    proj, _, render = masked
    first = export(proj, render, "layers_zip")
    with proj.db_factory() as db:
        st = db.scalar(select(JobStage).where(JobStage.job_id == first.id, JobStage.stage_name == "layers_zip"))
        assert st.state == "succeeded" and st.output_asset_ids == first.output_asset_ids
    assert load(proj.db_factory, Job, first.id).result_json["format"] == "layers_zip"


def test_blink_exports_the_reveal_layer_and_credits_the_users_photo(proj):
    from test_vfx_executors import FIX, _blink

    track = _blink(proj, (FIX / "clean_plate.png").read_bytes())
    render = proj.run("vfx.render", {"track_job_id": str(track.id), "render_profile": "quick"})
    with open_zip(proj, export(proj, render, "recipe_zip")) as z:
        readme = z.read("README.md").decode()
        assert "your own empty-background photo" in readme and "not a restored or generated background" in readme
        assert "layer order `in_front`" in readme
        inputs = json.loads(z.read("inputs.json"))
        assert inputs["clean_plate"]["sha256"] == proj.asset(proj.cfg["clean_plate_asset_id"]).sha256
        assert json.loads(z.read("layers.json"))["clean_plate"]["alignment"]["status"] == "ok"
    with open_zip(proj, export(proj, render, "layers_zip")) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["layer_order"] == ["reveal", "plate", "effect"] and manifest["folders"] == ["reveal", "effect"]
        assert not any(n.startswith("foreground/") for n in z.namelist())
        assert "nothing is restored or generated" in z.read("README.md").decode()
        plate = np.asarray(Image.open(FIX / "clean_plate.png").convert("RGB"))
        assert not png(z, "reveal/0010.png")[..., 3].any()  # before the effect: nothing revealed
        last = png(z, "reveal/0110.png")
        on = last[..., 3] == 255
        assert on.sum() > 5000 and np.array_equal(last[..., :3][on], plate[on])
