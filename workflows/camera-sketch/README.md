# Camera Sketch workflows

Four logical workflows (spec §9). In P0 only two run on Comfy; the other two run on the CPU worker, so
they need no bindings (their API graphs are exported only for the published workflow ZIP).

| Workflow | Runs on | Purpose | Bindings (`REQUIRED_BINDINGS`) | Outputs |
|---|---|---|---|---|
| `camera_depth_v1` | Comfy | Depth Anything V2 Small relative **inverse** depth for the scene image (`CSDepthToScene` quantizes it) | `image` (image) | `depth` |
| `camera_guide_v1` | CPU worker | Geometry render + fixed-range depth guide (`CSRenderGuides` logic in `open_creative.camera_sketch`) | none | none |
| `camera_cinematic_v1` | Comfy | LTX-2.3 distilled + matching union IC-LoRA, depth-guided | `guide_rgb` (video), `guide_depth` (video), `reference` (image), `prompt` (string), `seed` (int), `width`, `height`, `frames` (int), `control_strength` (float) | `video` |
| `camera_compare_v1` | CPU worker | Generated-vs-geometry motion QA (`CSGeneratedMotionReport` logic) | none | none |

`guide_rgb` / `guide_depth` use binding type **`video`** (an MP4 file; uploaded like an image asset by the SDK
runner). The geometry guide frames are model-shape padded by the worker (multiples of 32, 121 frames) and the
cinematic output is cropped/trimmed back to 1280×720 or 720×1280 and 120 frames by the worker.

`control_strength` takes its value from `open_creative.comfy.profiles.CONTROL_PRESETS` (`low`/`medium`/`high`).
They are `None` until calibrated on the first real smoke; until then the camera `open_weights_v1` profile
reports `available=False, reason="control presets not calibrated"` and only `mock_v1` runs. Do not invent values.

## Output contracts

- `camera_depth_v1` → `depth`: `CSDepthToScene` returns a normalised float MASK plus scene JSON; the exported
  graph must end in a save node producing the 16-bit PNG + JSON meta contract below, which the worker consumes. A **16-bit grayscale PNG** of relative inverse depth (linear `min..max -> 0..65535`,
  larger = nearer, sky = 0) plus JSON meta `{"min": float, "max": float}`. The worker converts it with
  `decode_depth_png16(png, meta)` (Task 10). `mock_v1` writes `depth_0.png` + `depth_0.json` (extra keys
  `mock: true`, `source: fixture_gt|heuristic`).
- `camera_cinematic_v1` → `video`: H.264 MP4 at the model shape given by `width`/`height`/`frames`. `mock_v1`
  writes `video_0.mp4` + `video_0.json` (`mock: true`). Cinematic results are never labelled exact camera control.

## Mock profile

`camera_depth_v1`: if the input matches a fixture in `fixtures/camera-sketch/scenes.json` (file sha256, or decoded
pixel hash `rgb_sha256`, because normalization may re-encode the PNG) it returns the fixture's GT inverse depth;
otherwise a deterministic heuristic (0.6 × vertical gradient, bottom nearer, + 0.4 × blurred luminance).
`camera_cinematic_v1`: the guide RGB frames (MP4 or a directory of PNG frames) with black holes `cv2.inpaint`
(TELEA) filled and a mild warm grade. Both are labelled Mock and are not generative.

## Export procedure from ComfyUI (for the two Comfy workflows)

1. Build the graph from the official example of the same model version: Depth Anything V2 Small + `CSDepthToScene`
   for `camera_depth_v1`; ComfyUI-LTXVideo distilled video-to-video with the union IC-LoRA depth guide for
   `camera_cinematic_v1`. Install this repo's custom nodes (`open_creative.comfy_nodes`).
2. *Workflow → Export (API)* → `<workflow>.api.json`; *Workflow → Export* → `<workflow>.ui.json`.
3. Read the real node IDs from the API JSON and write `<workflow>.bindings.json`
   (same schema as `workflows/product-lock/README.md`; `guide_rgb`/`guide_depth` have `"type": "video"`).
4. `uv run pytest nodes/open_creative/tests/test_cs_nodes.py`, fill `CONTROL_PRESETS` from the smoke run, and check
   `GET /api/v1/capabilities`. Node IDs are never guessed; no graph JSON is committed until a real export exists.

## Guide rendering

Production guides are rendered by the CPU worker, streaming frame by frame. `CSRenderGuides` is a batch node
(Comfy IMAGE needs a full batch) that exists for the published workflow; it preallocates its output tensors and
rejects paths longer than 121 frames (`CSCompileCameraPath.frames` max is 121).
