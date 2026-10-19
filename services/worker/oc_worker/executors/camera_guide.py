"""camera.guide: compile the pinned path, then ONE streaming render pass that writes the Geometry MP4, the
fixed-range depth-guide MP4 and the 1-bit validity PNGs frame by frame (never 120 frames in memory).

- Geometry MP4: rendered RGB with holes <= 4 px inpainted (``fill_small_holes``); larger holes stay as
  rendered (black), never stretched. This is the deterministic "Path render".
- Validity: the post-fill valid mask (small inpainted holes count as valid; large holes are invalid).
- Depth guide: one ``DepthGuideRange`` for the whole shot (union of the first and last frame's 1st/99th
  percentile valid z), invalid pixels nearest-valid filled.
- The cinematic guide RGB reuses the Geometry frames; the cinematic stage fills the remaining invalid pixels
  (validity mask) with the nearest valid pixel (``GUIDE_RGB_FILL``).
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import numpy as np
from PIL import Image

from oc_api.camera_sketch.jobs import build_path_cfg, geometry_hash
from oc_api.camera_sketch.scene_load import load_scene_info, scene_info_from_meta
from open_creative.camera_sketch.guides import (
    DEPTH_FILL_STRATEGY,
    DepthGuideRange,
    depth_guide,
)
from open_creative.camera_sketch.path import compile_path
from open_creative.camera_sketch.render import (
    fill_small_holes,
    hole_ratio,
    rasterize,
    render_sequence,
    target_visibility,
)
from open_creative.camera_sketch.scene import build_mesh
from open_creative.media.video import Mp4Writer, probe

from ..stages import JobRun, StageFailed
from .camera_common import (
    GEOMETRY_LABEL,
    GEOMETRY_LABEL_KO,
    api_errors,
    compiled_from_dict,
    json_asset,
    output_shape,
    ready_asset,
    revision_config,
)

MESH_STRIDE = 2  # source pixels per mesh cell; colors come from the full-resolution texture regardless
SMALL_HOLE_PX = 4
HOLE_RATIO_WARN = 0.08  # product policy (constraints): warn above 8 % holes in any frame
VISIBILITY_WARN = 0.90  # ... or target visibility below 90 %
PATCH_GRID = 7  # target patch sampled as a 7x7 grid for visibility
FILL_STRATEGY = {
    "rgb_small_holes": f"cv2_inpaint_telea_r3_components_le_{SMALL_HOLE_PX}px",
    "rgb_guide": "nearest_valid_l2_distance_transform",
    "depth_guide": DEPTH_FILL_STRATEGY,
}


def target_patch_points(z_norm: np.ndarray, K_src: np.ndarray, uv01) -> np.ndarray:
    """World points of the target patch (same 2 % radius as the depth normalization), on the scene surface."""
    h, w = z_norm.shape
    r = max(1, round(0.02 * min(h, w)))
    cx = int(np.clip(np.floor(uv01[0] * w), 0, w - 1))
    cy = int(np.clip(np.floor(uv01[1] * h), 0, h - 1))
    xs = np.unique(np.clip(np.rint(np.linspace(cx - r, cx + r, PATCH_GRID)), 0, w - 1).astype(int))
    ys = np.unique(np.clip(np.rint(np.linspace(cy - r, cy + r, PATCH_GRID)), 0, h - 1).astype(int))
    gx, gy = np.meshgrid(xs, ys)
    z = z_norm[gy, gx].ravel().astype(np.float64)
    ok = np.isfinite(z)
    # mesh vertices sit on the pixel grid corners (build_mesh), so sample there too
    pix = np.stack([gx.ravel()[ok], gy.ravel()[ok], np.ones(ok.sum())], 1)
    return (pix @ np.linalg.inv(K_src).T) * z[ok, None]


def _png1(mask: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.asarray(mask, bool)).convert("1").save(buf, "PNG", optimize=False)
    return buf.getvalue()


def camera_guide(run: JobRun) -> tuple[list[str], dict]:
    cfg = run.config()
    user_cfg, _ = revision_config(run, run.job.input_revision)
    scene = ready_asset(run, cfg.scene_asset_id, "scene", "scene_required")
    depth = ready_asset(run, cfg.depth_asset_id, "depth", "depth_missing")
    out_w, out_h, n = output_shape(cfg)
    ghash = geometry_hash(cfg)

    def compile_stage(stage):
        with api_errors():
            info = scene_info_from_meta(scene, depth, cfg)  # lineage-checked
        compiled = compile_path(build_path_cfg(cfg), info, out_w, out_h, n)
        if compiled.status == "invalid" or len(compiled.samples) != n:
            raise StageFailed("path_invalid", "Camera path is invalid: " + ", ".join(compiled.reasons))
        art = {"revision": run.job.input_revision, "geometry_hash": ghash, "user_config": user_cfg,
               "compiled": compiled.to_dict()}
        path = run.work_dir / "path.json"
        path.write_text(json.dumps(art))
        return [run.store_asset("path_artifact", path, "application/json", source_asset_id=depth.id,
                                meta={"revision": run.job.input_revision, "geometry_hash": ghash,
                                      "status": compiled.status})]

    (path_id,) = run.ensure_stage("compile", compile_stage)

    def render_stage(stage):
        run.transition("running", "render")
        art = json_asset(run, path_id)
        compiled = compiled_from_dict(art["compiled"])
        with api_errors():
            info, extras = load_scene_info(run.ctx.storage, scene, depth, cfg, with_image=True)
        mesh = build_mesh(extras.z_norm, extras.K_src, MESH_STRIDE, extras.edge_rel_threshold)
        patch = target_patch_points(extras.z_norm, extras.K_src, info.target_uv01)
        K_out = np.asarray(compiled.K, float)
        geo_mp4, dep_mp4, val_zip = (run.work_dir / x for x in ("geometry.mp4", "guide_depth.mp4", "validity.zip"))
        holes, vis, digests = [], [], []

        def pose_frame(i: int):
            T = np.asarray(compiled.samples[i]["T_cw"], float)
            return fill_small_holes(rasterize(mesh, None, K_out, T, out_w, out_h, texture=extras.scene_rgb),
                                    SMALL_HOLE_PX)[0]

        try:  # one range for the whole shot: union of the first and last frame (push-ins do not saturate)
            rng = DepthGuideRange.from_frames(pose_frame(0), pose_frame(len(compiled.samples) - 1))
        except ValueError as e:
            raise StageFailed("guide_empty", "The shot shows no scene geometry.") from e
        with Mp4Writer(geo_mp4, out_w, out_h, fps=cfg.output.fps) as geo_w, \
                Mp4Writer(dep_mp4, out_w, out_h, fps=cfg.output.fps) as dep_w, \
                zipfile.ZipFile(val_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            frames = render_sequence(mesh, None, compiled, out_w, out_h, texture=extras.scene_rgb)
            for i, raw in enumerate(frames):
                if i % 5 == 0:
                    run.heartbeat()
                fr, _ = fill_small_holes(raw, SMALL_HOLE_PX)
                g, _ = depth_guide(fr, rng)
                geo_w.write(fr.rgb)
                dep_w.write(np.repeat(g[..., None], 3, axis=2))
                zf.writestr(f"validity_{i:03d}.png", _png1(fr.valid))
                holes.append(hole_ratio(fr))
                digests.append(hashlib.sha256(fr.rgb.tobytes()).hexdigest())
                vis.append(target_visibility(fr, info.target_world, K_out,
                                             np.asarray(compiled.samples[i]["T_cw"], float),
                                             patch_points=patch if len(patch) else None))
            if len(holes) != n:
                raise StageFailed("frame_count_mismatch", f"Rendered {len(holes)} frames, expected {n}.")
            zf.writestr("manifest.json", json.dumps({
                "frame_count": n, "width": out_w, "height": out_h, "fps": cfg.output.fps,
                "files": "validity_###.png: 1-bit, white = valid geometry; rgb_sha256 = pre-encode frame hashes",
                "hole_ratio": holes, "rgb_sha256": digests, "small_hole_px": SMALL_HOLE_PX, "fill_strategy": FILL_STRATEGY,
                "depth_guide": rng.to_dict(), "revision": run.job.input_revision, "geometry_hash": ghash}))
        run.transition("postprocessing", "encode")
        for p in (geo_mp4, dep_mp4):
            info_ = probe(p)
            if (info_["width"], info_["height"], info_["frame_count"]) != (out_w, out_h, n):
                raise StageFailed("encode_mismatch", "Encoded guide does not match the output contract.")
        diag = art["compiled"]["diagnostics"]
        warnings = list(art["compiled"]["reasons"])
        if max(holes) > HOLE_RATIO_WARN:
            warnings.append("hole_ratio_high")
        if min(vis) < VISIBILITY_WARN:
            warnings.append("target_visibility_low")
        stats = {"frame_count": n, "hole_ratio_max": float(max(holes)), "hole_ratio_mean": float(np.mean(holes)),
                 "target_visibility_min": float(min(vis)), "framing_rms_rel": diag.get("framing_rms_rel"),
                 "warnings": warnings, "fill_strategy": FILL_STRATEGY, "depth_guide": rng.to_dict(),
                 "mesh_stride": MESH_STRIDE, "path_status": art["compiled"]["status"]}
        mock = bool((depth.meta_json or {}).get("mock"))
        common = {"revision": run.job.input_revision, "geometry_hash": ghash, "guide": True,
                  **({"mock": True} if mock else {})}
        geo_id = run.store_asset("geometry_video", geo_mp4, "video/mp4", source_asset_id=path_id,
                                 meta={**common, "mode": "geometry", "label": GEOMETRY_LABEL,
                                       "label_ko": GEOMETRY_LABEL_KO, "diagnostics": stats})
        dep_id = run.store_asset("guide_depth_video", dep_mp4, "video/mp4", source_asset_id=path_id,
                                 meta={**common, "depth_guide": rng.to_dict()})
        val_id = run.store_asset("guide_validity", val_zip, "application/zip", source_asset_id=path_id,
                                 meta={**common, "frame_count": n, "small_hole_px": SMALL_HOLE_PX})
        return [geo_id, dep_id, val_id]

    geo_id, dep_id, val_id = run.ensure_stage("render", render_stage)
    geo_meta = run.asset(geo_id).meta_json
    return [path_id, geo_id, dep_id, val_id], {
        **geo_meta["diagnostics"], "mode": "geometry", "label": GEOMETRY_LABEL,
        "geometry_hash": ghash, "revision": run.job.input_revision, "mock": bool(geo_meta.get("mock")),
        "path_asset_id": path_id, "geometry_video_asset_id": geo_id, "guide_depth_video_asset_id": dep_id,
        "guide_validity_asset_id": val_id,
    }
