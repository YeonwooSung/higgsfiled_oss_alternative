# Product Lock workflows

The `open_weights_v1` profile runs three Comfy API graphs. This folder must contain, for each workflow,
the **API-format graph exported from ComfyUI** and a **binding manifest** written against that exact export.
Until all three pairs exist and validate, the API reports the profile as unavailable (HTTP 503) and only
the `mock_v1` profile can run. Node IDs are never guessed from documentation.

| Workflow | Graph file | Built from |
|---|---|---|
| `product_segment_v1` | `product_segment_v1.api.json` | `PLProductMask` (SAM 2.1 small, click/box prompts) |
| `product_background_image_v1` | `product_background_image_v1.api.json` | FLUX.1-schnell text-to-image, official example graph |
| `product_background_video_v1` | `product_background_video_v1.api.json` | LTX-2.3 distilled image-to-video, ComfyUI-LTXVideo example graph |

Also keep the editor-format export next to each (`*.ui.json`) so the graph can be reopened in ComfyUI.

## Export steps

1. Build the graph in ComfyUI starting from the official example of the same model version.
2. *Workflow → Export (API)* → save as `<workflow>.api.json`. *Workflow → Export* → `<workflow>.ui.json`.
3. Open the API JSON, find the node IDs of the inputs below, and write `<workflow>.bindings.json`.
4. Run `uv run pytest nodes/open_creative/tests/test_profiles.py` and check `GET /api/v1/capabilities`.
5. Run the smoke test on the deployment (shared spec §4.2) before exposing the profile.

## Binding manifest

```json
{
  "workflow": "product_background_image_v1",
  "graph": "product_background_image_v1.api.json",
  "bindings": {
    "prompt": {"node_id": "<id from export>", "input": "text", "type": "string", "max_length": 2000},
    "seed":   {"node_id": "<id>", "input": "noise_seed", "type": "int", "min": 0, "max": 2147483647},
    "width":  {"node_id": "<id>", "input": "width", "type": "int", "min": 256, "max": 2048},
    "height": {"node_id": "<id>", "input": "height", "type": "int", "min": 256, "max": 2048}
  },
  "outputs": {"image": {"node_id": "<SaveImage id>"}}
}
```

Required logical bindings (enforced by `open_creative.comfy.bindings.REQUIRED_BINDINGS`):

| Workflow | Bindings | Outputs |
|---|---|---|
| `product_segment_v1` | `image` (image), `prompts` (json → STRING) | `mask` |
| `product_background_image_v1` | `prompt`, `seed`, `width`, `height` | `image` |
| `product_background_video_v1` | `image`, `prompt`, `seed`, `width`, `height`, `frames` | `video` |

Only these values are substituted at runtime; arbitrary graph upload is not accepted. Model input sizes
follow the profile stride rules (`open_creative.comfy.profiles`): FLUX 720×1280 / 1280×720, LTX
736×1280 / 1280×736 with 121 frames; the composite stage crops/trims back to the 120-frame output.
