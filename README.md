# Open Creative Apps — Product Lock

Product Lock turns one product photo into a 5-second ad **without asking a generative model to redraw the
product**. The product is cut out once from the original pixels and composited over a generated background;
every frame is checked so the protected label area matches the original before encoding.

Specs: [`docs/00_SHARED_IMPLEMENTATION_SPEC.md`](docs/00_SHARED_IMPLEMENTATION_SPEC.md),
[`docs/01_PRODUCT_LOCK_SPEC.md`](docs/01_PRODUCT_LOCK_SPEC.md). Implementation plan:
[`docs/superpowers/plans/2026-10-19-product-lock.md`](docs/superpowers/plans/2026-10-19-product-lock.md).

## What works today

| Area | Status |
|---|---|
| Deterministic composite + protected-core check (≤ 1/255 on every lossless frame) | Done, tested on 5 fixtures × 2 ratios |
| Upload → normalization (EXIF, sRGB, limits) → alpha mask suggestion | Done |
| Mask editing: alpha PNG, click segmentation (mock: OpenCV GrabCut), brush, label boxes | Done |
| Background image → background video → render → MP4 / cover / recipe ZIP / share link | Done on the **Mock** profile |
| Jobs: idempotency, quotas, revision pinning, SSE + polling fallback, cancel, `submission_unknown` | Done, tested on SQLite and PostgreSQL 16 |
| `open_weights_v1` profile (SAM 2.1 small, FLUX.1-schnell, LTX-2.3 on a Comfy deployment) | **Not yet integrated** — see below |

### Mock vs. open weights

The `mock_v1` profile runs everything on CPU with deterministic stand-ins (synthetic studio backdrops,
GrabCut masks, a slow light drift instead of LTX). Its results are labelled **Mock** in the UI and in the
recipe. It exists so the whole product can be exercised without a GPU; it is not the finished demo.

`open_weights_v1` becomes available only after the three API graphs are exported from ComfyUI and their
binding manifests validate (see [`workflows/product-lock/README.md`](workflows/product-lock/README.md)).
Until then `GET /api/v1/capabilities` reports it unavailable and generation requests get HTTP 503.
The runner for it is written against `comfy-sdk==0.5.0`'s actual submit/idempotency semantics, but it has
not been run against a real deployment yet.

## Layout

```
apps/product-lock      Next.js editor (App Router, Tailwind, Radix, Zustand, TanStack Query, react-konva)
apps/camera-sketch     Next.js editor for Camera Sketch (Three.js preview, react-konva sketch overlay)
apps/pocket-vfx        Next.js editor for Pocket VFX (video timeline, point track, mask edit, before/after compare)
packages/ui            shared UI primitives        packages/contracts   API types
packages/web-core      API client, job watcher, autosave, history, i18n base (used by camera-sketch)
services/api           FastAPI + SQLAlchemy + Alembic (sessions, projects, assets, jobs, shares)
services/worker        DB-lease job worker, stage runner, Comfy adapter (mock + comfy-sdk)
nodes/open_creative    imaging core (NumPy/OpenCV/FFmpeg) + ComfyUI custom nodes (PL*)
workflows/<app>        binding-manifest contract for the exported Comfy graphs
presets/<app>          background / camera presets models/              model manifests (no weights)
fixtures/<app>         synthetic test inputs       infra/               Dockerfiles, compose
```

## Run locally

Requirements: Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20 + pnpm 10, FFmpeg.

```bash
uv sync && pnpm install
cp .env.example .env            # OC_DEFAULT_PROFILE=mock_v1 runs without a GPU

# three terminals (SQLite + local storage for development)
mkdir -p .data
export OC_DATABASE_URL=sqlite+pysqlite:///.data/oc.db OC_COOKIE_SECURE=false OC_DEFAULT_PROFILE=mock_v1
uv run uvicorn oc_api.app:create_app --factory --port 8000
uv run python -m oc_worker.main
pnpm dev:product-lock          # http://localhost:3000 → "Try sample"
pnpm dev:camera-sketch         # http://localhost:3001 (same API on :8000)
pnpm dev:pocket-vfx            # http://localhost:3002 (same API on :8000)
```

Or everything in Docker (PostgreSQL, migrations, API, worker, the three web apps — :3000, :3001 and :3002):

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml --env-file .env up --build
```

## Tests

```bash
uv run pytest                    # Python: imaging core, API, worker (SQLite)
uv run pytest -m slow            # full 120-frame renders for all fixtures (~1 min)
OC_TEST_DATABASE_URL=postgresql+psycopg://oc:oc@127.0.0.1:5432/oc_test uv run pytest services   # PostgreSQL
pnpm test && pnpm typecheck      # web logic (vitest) and types
```

## Using the Comfy nodes

Copy or symlink `nodes/open_creative` into `ComfyUI/custom_nodes/open_creative` and install
`numpy pillow opencv-python-headless`. The nodes (`PLProductMask`, `PLLockedProduct`, `PLBackgroundPrompt`,
`PLCompositeSequence`, `PLCoreIntegrityCheck`) use the same code as the worker, so a graph built from them
gives the same composite and the same integrity report.

## Known limits

- Opaque products only. Glass, liquids and thin handles need manual mask work; results are not guaranteed.
- The product is fixed in place for the whole clip; no relighting, rotation or new viewpoints.
- The preservation guarantee is about the composite before encoding. H.264 changes pixels slightly;
  the UI reports that separately as a diagnostic ("Video compression check").
- Generated backgrounds are not bit-exact across GPUs or library versions; the recipe reproduces the
  same graph, models, settings and inputs.
- Deleting a project removes its storage and asset rows once its jobs have stopped. Provider-side artifacts
  (open-weights profile) are recorded as `cleanup_tasks(target=provider)` but not deleted automatically yet.
- No prices are hard-coded. Estimates show reserved GPU-second caps and "price unknown".
- Code is MIT. Model weights keep their own licenses (LTX-2.3 uses a community license).

---

# Camera Sketch

Camera Sketch takes one photo, lets you click a target and draw how the framing of that target should move,
and renders a 5-second clip in two ways. Spec: [`docs/02_CAMERA_SKETCH_SPEC.md`](docs/02_CAMERA_SKETCH_SPEC.md).
Dev server: `pnpm dev:camera-sketch` on **http://localhost:3001**, API on **:8000**, Product Lock on **:3000**
(the same API and worker serve both apps).

## Two modes

| Mode | Badge | What it is | What it is not |
|---|---|---|---|
| **Geometry** | Path render / 경로 렌더 | Deterministic 2.5D render of the photo's estimated depth along the compiled camera path (z-buffer, holes kept as invalid pixels). | Follows the input path, but large viewpoint changes are not photoreal: newly revealed areas stay holes. |
| **Cinematic** | AI generated / AI 생성 | The Geometry RGB and a fixed-range depth guide condition an LTX video model. | No exact camera pose guarantee. Depth/edge conditioning steers the motion; it is not a camera-matrix API. |

Cinematic results are never labelled as exact camera control. Motion QA that cannot track returns `unknown`.

## P0 limits

- One still image per project. Output 1280x720 or 720x1280, H.264, 24 fps, 5 s = **120 frames**
  (model-internal padding, e.g. 121 frames / multiples of 32, is applied and removed inside the cinematic stage).
- Sketch = where the **target sits on screen** over time (relative to its start), reduced to 2-5 control points.
  It is not a free 3D translation command. A single click gives a static path.
- Camera bounds: lateral +/-0.05 scene units, push 0..0.10 of target depth, small_arc up to +/-6 degrees
  around the target, roll fixed 0, synthetic focal length 35-70 mm-equivalent (36 mm sensor assumed).
- Depth is Depth Anything V2 **Small** relative inverse depth, normalised so the target patch median is 1 scene unit; it is not metric.
- Not supported: 360 degree orbit, multi-photo SfM / NeRF / 3DGS, moving-person pose, lens change within a shot,
  long videos, free 6DoF.
- Guardrails (product policy, tunable on fixtures): max frame hole ratio above 8% or target visibility below 90%
  shows a warning. A reduced-motion clamp is only proposed (before/after overlay); the input is never changed silently.

## Mock vs. open weights

- `mock_v1` (default) runs on CPU: depth comes from the fixture ground truth when the image matches a fixture,
  otherwise a deterministic heuristic; the "cinematic" output is the geometry guide with holes inpainted and a
  mild grade. Results are labelled **Mock** and are not generative.
- `open_weights_v1` for Camera Sketch is **unavailable**: the two Comfy graphs (`camera_depth_v1`,
  `camera_cinematic_v1`) have not been exported from a real ComfyUI deployment, and the `control_strength`
  presets are uncalibrated (`CONTROL_PRESETS` are `None`). `GET /api/v1/capabilities` reports it unavailable
  with reason "control presets not calibrated" until that is done on a Comfy deployment with a GPU. See
  [`workflows/camera-sketch/README.md`](workflows/camera-sketch/README.md). No real depth to LTX inference has been run.

## Models and licenses

Weights are not included; see [`models/camera-sketch.manifest.json`](models/camera-sketch.manifest.json)
(sha256 values are filled in from the files actually deployed).

| Role | Model | License |
|---|---|---|
| Depth | Depth Anything V2 Small | Apache-2.0 |
| Cinematic + control LoRA | LTX-2.3 distilled + the union IC-LoRA of the same release | LTX community license (weights), distinct from this repository's MIT code license |

## S3 / MinIO: bucket CORS

With `OC_STORAGE_BACKEND=s3` the browser reads signed bucket URLs cross-origin. The Camera Sketch web origin
needs a CORS rule allowing **GET with the `Range` header** (and exposing `Content-Range`, `Accept-Ranges`,
`Content-Length`) on the bucket, for: preview depth/image fetches, the path overlay on the result page, and
share-page playback. Uploads additionally need PUT (as for Product Lock). With the default local backend
(files served through the API) no CORS setup is needed. Example (`mc` / S3 CORS JSON):

```json
[{"AllowedOrigins": ["http://localhost:3001"], "AllowedMethods": ["GET", "HEAD", "PUT"],
  "AllowedHeaders": ["Range", "Content-Type"], "ExposeHeaders": ["Content-Range", "Accept-Ranges", "Content-Length"],
  "MaxAgeSeconds": 3000}]
```

## Evaluation and verification

- Manual eval sheet (6 photos x 3 presets): [`docs/camera-sketch-eval.md`](docs/camera-sketch-eval.md) - not yet run.
- Spec section 13 results on the mock profile: [`docs/camera-sketch-verification.md`](docs/camera-sketch-verification.md).
- `scripts/export_app.sh camera-sketch <out>` exports a standalone tree (this app, shared packages, nodes, services, infra,
  its presets/workflow/fixtures/model manifest, and the specs).

---

# Pocket VFX

Pocket VFX adds one visual effect to a short video clip of yours. The original pixels are kept: the effect is a
separate layer composited over your clip, and every frame is checked so nothing changes outside the effect's region.
Spec: [`docs/03_POCKET_VFX_SPEC.md`](docs/03_POCKET_VFX_SPEC.md). Dev server: `pnpm dev:pocket-vfx` on
**http://localhost:3002** (same API on **:8000** and the same worker as the other apps).

## Effects

| Effect | What it does | Notes |
|---|---|---|
| **Impact Wave** | Shock ring and dust from a clicked point at the chosen peak time. | Can sit behind the subject (subject mask needed). |
| **Energy Aura** | Glow band around the subject's contour, or a local glow at the anchor. | Needs a subject mask for the contour. |
| **Blink Out** | The subject dissolves into noise and particles and is replaced by a clean plate you upload. | Only with a fixed camera and an empty frame (clean plate). The app checks alignment and refuses a mismatch; it does not restore or invent the background. |

One effect and one subject per project. Timing (start / peak / end) and scale / intensity are config, not new generations.

## Quick vs. Generated plate

- **Quick**: bundled plates (dust burst, energy wisps, particles) plus procedural layers. No model run; labelled "Quick".
- **Generated**: an LTX effect plate on a black background, matted to alpha (threshold + soft knee, black-level despill,
  temporal smoothing) and composited behind or over the subject. Labelled "AI generated". Raw plate and matte are viewable.
  Changing scale or intensity re-renders only; the track and the plate are not regenerated.

## P0 limits

- Trim 1-5 s from a source of at most 60 s; MP4 / MOV / WebM, at most 100 MB.
- Output 24 fps constant frame rate, 1280x720 or 720x1280 (variable frame rate and rotated sources are normalised).
- One effect, one subject, one tracked point (Lucas-Kanade on a proxy, with manual correction keyframes).
- Blink Out only with a fixed camera and a clean plate. Moving-camera clean plates are not supported.
- SAM masks are segmentation, not matting: hair and motion-blurred edges are hard cut. Edit masks per frame when needed.
- The app never claims background restoration.
- Not supported: camera shake, depth ordering, multi-effect stacks, auto action recognition, pose, physics, face
  regeneration, alpha video codecs, 1080p/4K.
- Thresholds (track validity, mask diagnostics, matte QA) were tuned on synthetic fixtures only.

## Mock vs. open weights

- `mock_v1` (default) runs on CPU: masks come from the fixture ground truth when the clip matches a fixture,
  otherwise deterministic GrabCut; the "generated" plate is a deterministic procedural stand-in. Results are labelled **Mock**.
- `open_weights_v1` for Pocket VFX is **pending**: the `vfx_masks_v1` (SAM 2.1 hiera small, video predictor) and
  `vfx_plate_v1` (LTX-2.3 distilled v1.1) graphs are not exported from a Comfy deployment, their binding manifests are
  not added and the model sha256 values in [`models/pocket-vfx.manifest.json`](models/pocket-vfx.manifest.json) are empty.
  `GET /api/v1/capabilities` reports it unavailable. See [`workflows/pocket-vfx/README.md`](workflows/pocket-vfx/README.md).
  No real mask or plate inference has been run.

## Models and licenses

Weights are not included.

| Role | Model | License |
|---|---|---|
| Subject masks | SAM 2.1 hiera small | Apache-2.0 |
| Effect plate | LTX-2.3 distilled v1.1 | LTX community license (weights), distinct from this repository's MIT code license |
| Plate text encoder | Gemma text encoder of the official LTX-2.3 ComfyUI example | Gemma Terms of Use |

The bundled plates (`presets/pocket-vfx/plates/`) and SFX (`presets/pocket-vfx/sfx/`) are procedurally generated by
`fixtures/pocket-vfx/make_fixtures.py` and are MIT like the code.

## Known limitations

- The layers ZIP holds two RGBA PNG sequences (effect, foreground), one PNG per frame (120 frames each), so it can be large.
- With `OC_STORAGE_BACKEND=s3` the browser reads signed URLs cross-origin; the bucket needs CORS for the Pocket VFX origin
  (see the CORS note under Camera Sketch, with `http://localhost:3002`).

## Evaluation and verification

- Manual eval sheet (3 inputs per effect): [`docs/pocket-vfx-eval.md`](docs/pocket-vfx-eval.md) - not yet run.
- Spec section 14 results on the mock profile: [`docs/pocket-vfx-verification.md`](docs/pocket-vfx-verification.md).
- `scripts/export_app.sh pocket-vfx <out>` exports a standalone tree.
