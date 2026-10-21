# Pocket VFX workflows

Spec §9 names the logical workflows. In P0 only two of them run on Comfy. Analyze, the LK point track, the
composite/render and the export run on the CPU worker (`open_creative.pocket_vfx` + `services/worker`). The `PV*`
nodes wrap that same code for the published workflow.

| Workflow | Runs on | Purpose | Bindings (`REQUIRED_BINDINGS`) | Outputs |
|---|---|---|---|---|
| `vfx_masks_v1` | Comfy | SAM 2.1 hiera small **video** predictor. It propagates the subject mask over the proxy work clip (`PVSubjectMasks`). | `frames` (video), `prompts` (json), `width`, `height` (int) | `masks` |
| `vfx_plate_v1` | Comfy | LTX-2.3 distilled v1.1 effect plate on a black background | Task 12 | Task 12 |

`vfx_masks_v1` is the GPU `masks` stage of `vfx.track`. The worker runs it only when the render needs subject masks
and the config has subject prompts or mask edits. Its reuse key is `keys.masks_key`, which excludes point
corrections. A re-track whose masks key equals that of an earlier succeeded track job of the project reuses that
job's mask assets and sends nothing to the provider.

## `vfx_masks_v1` contract

- `frames`: the **proxy** work clip (MP4, CFR 24 fps, 640×360 or 360×640, N frames). The SDK runner uploads it like an
  image asset. In the graph, a video loader feeds `PVSubjectMasks.frames`.
- `width` / `height`: the proxy size. Frames of another size are area-resized to it first.
- `prompts`: `open_creative.pocket_vfx.masks.build_prompts(...)`. All coordinates are proxy px.
  ```json
  {"schema_version": 1, "width": 640, "height": 360, "frame_count": 120, "obj_id": 1,
   "point_frames": [{"frame": 43, "points": [{"x": 320.0, "y": 150.0, "label": 1}]}],
   "mask_frames": [{"frame": 60, "png_b64": "<8-bit gray PNG at proxy size, 255 = subject>"}],
   "mock_context": {"source_sha256": "...", "source_pts_ms": [0.0, 41.667, ...]}}
  ```
  - `point_frames` come from the config's `subject_prompts` (uv01 × proxy size). `label` is 1 for positive and 0 for
    negative.
  - `mask_frames` are the user's mask edits (`config.mask_edits`, role `mask_edit` PNG of any size, resized nearest to
    the proxy). SAM 2.1 receives them through `add_new_mask`, so the propagation around an edit follows it. The worker
    also writes each edit verbatim over its frame in the result (`apply_mask_edits`).
  - `mock_context` exists **only for the mock provider**, which uses it to recognise fixture clips. It holds the
    clip's sha256 and the source PTS of every work frame. The real graph ignores it, and `prompts_hash` excludes it.
- Output `masks`: a ZIP of `mask_%04d.png` (8-bit gray `L`, proxy size, 255 = subject, one per work frame) plus a
  `manifest.json`:
  ```json
  {"schema_version": 1, "size": [640, 360], "frames": [{"frame": 0, "file": "mask_0000.png", "area": 9120, "holes": 0, "empty": false}],
   "prompts_hash": "<sha256>", "model": "sam2.1_hiera_small"}
  ```
  The worker re-reads every PNG, binarizes it at > 127, applies the mask edits and recomputes the stats and
  diagnostics. It then stores:
  - `masks_zip`: the final sequence.
  - `mask_preview`: a gray proxy MP4 for the browser overlay.
  - The diagnostics: `area_jump` (> 35 % frame-to-frame), `mask_empty`, and `holes` (> 3).

### `PVSubjectMasks` (real SAM 2.1)

- **Model.** `build_sam2_video_predictor("configs/sam2.1/sam2.1_hiera_s.yaml", <models/sam2/sam2.1_hiera_small.pt>)`
  is imported lazily, so the node pack imports without torch or sam2.
- **State per call.** Every call writes the frames to its own temporary JPEG directory and creates its own
  `init_state(video_path=..., offload_video_to_cpu=True)`. Nothing is module-global, so the video state of one job is
  never shared with another job on the same worker (spec §9).
- **Prompts.** `add_new_points_or_box` runs at each point frame and `add_new_mask` at each mask frame (`obj_id` 1).
- **Propagation.** `propagate_in_video` runs forward and then with `reverse=True`, so frames before the first prompt
  are covered. Logits > 0 count as subject. `reset_state` runs at the end.
- **Output.** The node returns `MASK [N,H,W]` and the manifest JSON. As an output node it also writes the ZIP above
  to the Comfy output directory (`ui.masks`).
- **Unverified.** Not tested on a GPU yet. Before exporting, confirm on the first real smoke how the Comfy API v2 job
  exposes the output file.

## Mock profile (`mock_v1`)

Deterministic. It is not SAM and is labelled Mock. The sidecar `masks_0.json` carries `{"mock": true, "source": ...}`.

- **Fixture clip.** When `mock_context.source_sha256` matches a clip in `fixtures/pocket-vfx/manifest.json` that has
  GT masks (`stomp_landscape`, and `rotated_portrait` whose GT is in display orientation), the mock returns the GT
  masks:
  - It resamples them to the work timeline with the PTS map (GT frame `round(pts_ms × fps / 1000)`).
  - It cover-fits them to the proxy size (area resize, then threshold).
- **Any other clip.** The mock runs OpenCV GrabCut at proxy size:
  - **Prompt frames.** Rect = bbox of the positive points padded by 30 % of its size on each side.
    The rect is at least 30 % of the short side, so a single click still has a region. Positive points are FG and
    negative points are BG. A mask frame is used as given.
  - **Every other frame.** GrabCut is initialised from the neighbouring frame's mask dilated by 8 px. Frames after
    the first prompt propagate forward and frames before it backward.
  - **Colour model.** GrabCut runs on YCrCb rather than RGB, which keeps a saturated subject apart from a dark,
    desaturated background.
  - **Determinism.** The OpenCV RNG is seeded per frame, so the output is deterministic.
  - **Quality.** GrabCut is a rough stand-in. Propagated frames drift: wall pixels next to a limb can join the mask,
    or a small fast part like the hand can drop out. Tests that need exact masks use the fixture GT.

## Export procedure from ComfyUI

1. **Build the graph.** Start from the official SAM 2 video example: a video loader → `PVSubjectMasks`, with
   `prompts`, `width` and `height` as widgets. Install this repo's custom nodes (`open_creative.comfy_nodes`).
2. **Export.** *Workflow → Export (API)* → `vfx_masks_v1.api.json`. *Workflow → Export* → `vfx_masks_v1.ui.json`.
3. **Write the bindings.** Read the real node IDs from the API JSON and write `vfx_masks_v1.bindings.json`, using
   the schema of `workflows/product-lock/README.md`.
   - `frames` is `"type": "video"` and `prompts` is `"type": "json"`.
   - The output `masks` is the `PVSubjectMasks` node.
4. **Check availability.** `open_weights_v1` stays `available=false` until both graphs (`vfx_masks_v1`,
   `vfx_plate_v1`) and their binding manifests exist and validate. `profile_status` reports the first missing one.
   Node IDs are never guessed, and no graph JSON is committed until a real export exists.

Model files and licenses: `models/pocket-vfx.manifest.json` (`sha256: null`, `verified: false` until deployed).
