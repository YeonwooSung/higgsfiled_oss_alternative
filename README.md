# Open Creative Studio

One web studio with three tools that share a single API, worker and project store. Each tool keeps the source
pixels you give it and checks the result against them.

- **Camera Sketch**: one photo plus a sketched on-screen path becomes a 5-second camera move.
- **Product Lock**: one product photo becomes a 5-second ad **without asking a generative model to redraw the
  product**. The product is cut out once from the original pixels and composited over a generated background;
  every frame is checked so the protected label area matches the original before encoding.
- **Pocket VFX**: one visual effect added to a short clip of yours, as a separate layer over the original pixels.

Specs: [`docs/00_SHARED_IMPLEMENTATION_SPEC.md`](docs/00_SHARED_IMPLEMENTATION_SPEC.md),
[`docs/01_PRODUCT_LOCK_SPEC.md`](docs/01_PRODUCT_LOCK_SPEC.md),
[`docs/02_CAMERA_SKETCH_SPEC.md`](docs/02_CAMERA_SKETCH_SPEC.md),
[`docs/03_POCKET_VFX_SPEC.md`](docs/03_POCKET_VFX_SPEC.md). Studio design:
[`docs/superpowers/specs/2026-10-25-unified-studio-design.md`](docs/superpowers/specs/2026-10-25-unified-studio-design.md),
plan: [`docs/superpowers/plans/2026-10-25-unified-studio.md`](docs/superpowers/plans/2026-10-25-unified-studio.md).
Earlier per-tool plans are in [`docs/superpowers/plans/`](docs/superpowers/plans/).

The studio (`apps/studio`, package `@oc/studio`, port 3000) has a labelled left sidebar listing the tools and your
session's projects ("My projects"), and one top bar for all tools. Routes: `/camera` (the default; `/` redirects there),
`/product`, `/vfx`, `/<tool>/p/[id]` for a project and `/s/[token]` for a share link.

## The three tools

### Camera Sketch

Take one photo, click a target and draw how the framing of that target should move. It renders a 5-second clip in two ways:

| Mode | Badge | What it is | What it is not |
|---|---|---|---|
| **Geometry** | Path render / 경로 렌더 | Deterministic 2.5D render of the photo's estimated depth along the compiled camera path (z-buffer, holes kept as invalid pixels). | Follows the input path, but large viewpoint changes are not photoreal: newly revealed areas stay holes. |
| **Cinematic** | AI generated / AI 생성 | The Geometry RGB and a fixed-range depth guide condition an LTX video model. | No exact camera pose guarantee. Depth/edge conditioning steers the motion; it is not a camera-matrix API. |

Cinematic results are never labelled as exact camera control. Motion QA that cannot track returns `unknown`.

### Product Lock

Upload, normalization (EXIF, sRGB, limits), alpha mask suggestion, mask editing (alpha PNG, click segmentation, brush,
label boxes), background image, background video, render, then MP4 / cover / recipe ZIP / share link. The composite
and protected-core check (at most 1/255 on every lossless frame) is tested on 5 fixtures x 2 ratios. Jobs support
idempotency, quotas, revision pinning, SSE with polling fallback, cancel and `submission_unknown`, tested on SQLite
and PostgreSQL 16.

### Pocket VFX

| Effect | What it does | Notes |
|---|---|---|
| **Impact Wave** | Shock ring and dust from a clicked point at the chosen peak time. | Can sit behind the subject (subject mask needed). |
| **Energy Aura** | Glow band around the subject's contour, or a local glow at the anchor. | Needs a subject mask for the contour. |
| **Blink Out** | The subject dissolves into noise and particles and is replaced by a clean plate you upload. | Only with a fixed camera and an empty frame (clean plate). The app checks alignment and refuses a mismatch; it does not restore or invent the background. |

One effect and one subject per project. Timing (start / peak / end) and scale / intensity are config, not new generations.

- **Quick**: bundled plates (dust burst, energy wisps, particles) plus procedural layers. No model run; labelled "Quick".
- **Generated**: an LTX effect plate on a black background, matted to alpha (threshold + soft knee, black-level despill,
  temporal smoothing) and composited behind or over the subject. Labelled "AI generated". Raw plate and matte are viewable.
  Changing scale or intensity re-renders only; the track and the plate are not regenerated.

## Hand-offs between tools

A finished result can be sent to the next tool as that tool's input, through `POST /api/v1/handoffs`
(`{ "source_asset_id", "target_app" }`, supports `Idempotency-Key`). The server copies the asset into a new project of
the target tool and records where it came from (`source_app`, `source_project_id`, `source_job_id`, `source_sha256`, `mock`).

- Product Lock render to Pocket VFX.
- Camera Sketch geometry or cinematic result to Pocket VFX.
- Product Lock cover to Camera Sketch.

The target shows a provenance badge for the input, and the target's recipe includes a `handoff` block so a chain can be
reproduced. **Mock is carried through the chain**: a result is shown as Mock if its own profile is mock or the
hand-off's `mock` flag is true, on the result page, in the recipe and on the share page. `GET /api/v1/projects` lists the
session's projects for the sidebar.

## Mock vs. open weights

The `mock_v1` profile runs everything on CPU with deterministic stand-ins. Its results are labelled **Mock** in the UI
and in the recipe. It exists so the whole product can be exercised without a GPU; it is not the finished demo.

- Product Lock: synthetic studio backdrops, GrabCut masks, a slow light drift instead of LTX.
- Camera Sketch: depth comes from the fixture ground truth when the image matches a fixture, otherwise a deterministic
  heuristic; the "cinematic" output is the geometry guide with holes inpainted and a mild grade. Not generative.
- Pocket VFX: masks come from the fixture ground truth when the clip matches a fixture, otherwise deterministic GrabCut;
  the "generated" plate is a deterministic procedural stand-in.

**`open_weights_v1` is not integrated yet for any tool.** `GET /api/v1/capabilities` reports it unavailable and
generation requests get HTTP 503. No real inference has been run.

- Product Lock (SAM 2.1 small, FLUX.1-schnell, LTX-2.3): becomes available only after the three API graphs are exported
  from ComfyUI and their binding manifests validate ([`workflows/product-lock/README.md`](workflows/product-lock/README.md)).
  The runner is written against `comfy-sdk==0.5.0`'s actual submit/idempotency semantics but has not been run against a
  real deployment.
- Camera Sketch: the two Comfy graphs (`camera_depth_v1`, `camera_cinematic_v1`) are not exported, and the
  `control_strength` presets are uncalibrated (`CONTROL_PRESETS` are `None`); reason reported: "control presets not
  calibrated" ([`workflows/camera-sketch/README.md`](workflows/camera-sketch/README.md)). No real depth to LTX inference has been run.
- Pocket VFX: the `vfx_masks_v1` (SAM 2.1 hiera small, video predictor) and `vfx_plate_v1` (LTX-2.3 distilled v1.1) graphs
  are not exported, their binding manifests are not added and the model sha256 values in
  [`models/pocket-vfx.manifest.json`](models/pocket-vfx.manifest.json) are empty
  ([`workflows/pocket-vfx/README.md`](workflows/pocket-vfx/README.md)).

## Layout

```
apps/studio            Next.js studio (App Router, Tailwind, Radix, Zustand, TanStack Query, react-konva, Three.js)
                       src/shell (sidebar, top bar, hand-offs), src/features/{camera-sketch,product-lock,pocket-vfx}
packages/ui            shared UI primitives        packages/contracts   API types
packages/web-core      API client, job watcher, autosave, history, i18n base
services/api           FastAPI + SQLAlchemy + Alembic (sessions, projects, assets, jobs, shares, hand-offs)
services/worker        DB-lease job worker, stage runner, Comfy adapter (mock + comfy-sdk)
nodes/open_creative    imaging core (NumPy/OpenCV/FFmpeg) + ComfyUI custom nodes (PL*)
workflows/<tool>       binding-manifest contract for the exported Comfy graphs
presets/<tool>         background / camera / effect presets      models/   model manifests (no weights)
fixtures/<tool>        synthetic test inputs       infra/               Dockerfiles, compose
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
pnpm dev:studio                # http://localhost:3000 (API on :8000)
```

Or everything in Docker (PostgreSQL, migrations, API, worker, the studio on :3000):

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml --env-file .env up --build
```

## Tests

```bash
uv run pytest                    # Python: imaging core, API, worker (SQLite)
uv run pytest -m slow            # full 120-frame renders for all fixtures (~1 min)
OC_TEST_DATABASE_URL=postgresql+psycopg://oc:oc@127.0.0.1:5432/oc_test uv run pytest services   # PostgreSQL
pnpm -r test && pnpm -r typecheck   # web logic (vitest) and types
pnpm --filter @oc/studio build
```

The Python-to-TypeScript parity tests are `apps/studio/tests/camera-sketch/parity.test.ts` and
`apps/studio/tests/pocket-vfx/parity.test.ts`.

Evaluation and verification (mock profile):

- Camera Sketch: manual eval sheet (6 photos x 3 presets, not yet run) [`docs/camera-sketch-eval.md`](docs/camera-sketch-eval.md);
  spec section 13 results [`docs/camera-sketch-verification.md`](docs/camera-sketch-verification.md).
- Pocket VFX: manual eval sheet (3 inputs per effect, not yet run) [`docs/pocket-vfx-eval.md`](docs/pocket-vfx-eval.md);
  spec section 14 results [`docs/pocket-vfx-verification.md`](docs/pocket-vfx-verification.md).
  These two verification records describe past runs from when the tools were separate apps.

## Using the Comfy nodes

Copy or symlink `nodes/open_creative` into `ComfyUI/custom_nodes/open_creative` and install
`numpy pillow opencv-python-headless`. The nodes (`PLProductMask`, `PLLockedProduct`, `PLBackgroundPrompt`,
`PLCompositeSequence`, `PLCoreIntegrityCheck`) use the same code as the worker, so a graph built from them
gives the same composite and the same integrity report.

## Models and licences

Weights are not included; see [`models/`](models/) (`product-lock`, `camera-sketch`, `pocket-vfx` manifests; sha256
values are filled in from the files actually deployed).

| Tool | Role | Model | Licence |
|---|---|---|---|
| Camera Sketch | Depth | Depth Anything V2 Small | Apache-2.0 |
| Camera Sketch | Cinematic + control LoRA | LTX-2.3 distilled + the union IC-LoRA of the same release | LTX community licence (weights) |
| Pocket VFX | Subject masks | SAM 2.1 hiera small | Apache-2.0 |
| Pocket VFX | Effect plate | LTX-2.3 distilled v1.1 | LTX community licence (weights) |
| Pocket VFX | Plate text encoder | Gemma text encoder of the official LTX-2.3 ComfyUI example | Gemma Terms of Use |

Product Lock's planned `open_weights_v1` models are SAM 2.1 small, FLUX.1-schnell and LTX-2.3 (see its manifest).
Code is MIT; model weights keep their own licences, and the LTX community licence is distinct from this repository's
MIT code licence. The bundled Pocket VFX plates (`presets/pocket-vfx/plates/`) and SFX (`presets/pocket-vfx/sfx/`) are
procedurally generated by `fixtures/pocket-vfx/make_fixtures.py` and are MIT like the code.

## Known limits

General
- No prices are hard-coded. Estimates show reserved GPU-second caps and "price unknown".
- Deleting a project removes its storage and asset rows once its jobs have stopped. Provider-side artifacts
  (open-weights profile) are recorded as `cleanup_tasks(target=provider)` but not deleted automatically yet.
- Generated backgrounds and plates are not bit-exact across GPUs or library versions; the recipe reproduces the
  same graph, models, settings and inputs.

Camera Sketch
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

Product Lock
- Opaque products only. Glass, liquids and thin handles need manual mask work; results are not guaranteed.
- The product is fixed in place for the whole clip; no relighting, rotation or new viewpoints.
- The preservation guarantee is about the composite before encoding. H.264 changes pixels slightly;
  the UI reports that separately as a diagnostic ("Video compression check").

Pocket VFX
- Trim 1-5 s from a source of at most 60 s; MP4 / MOV / WebM, at most 100 MB.
- Output 24 fps constant frame rate, 1280x720 or 720x1280 (variable frame rate and rotated sources are normalised).
- One effect, one subject, one tracked point (Lucas-Kanade on a proxy, with manual correction keyframes).
- Blink Out only with a fixed camera and a clean plate. Moving-camera clean plates are not supported.
- SAM masks are segmentation, not matting: hair and motion-blurred edges are hard cut. Edit masks per frame when needed.
- The app never claims background restoration.
- Not supported: camera shake, depth ordering, multi-effect stacks, auto action recognition, pose, physics, face
  regeneration, alpha video codecs, 1080p/4K.
- Thresholds (track validity, mask diagnostics, matte QA) were tuned on synthetic fixtures only.
- The layers ZIP holds two RGBA PNG sequences (effect, foreground), one PNG per frame (120 frames each), so it can be large.

### S3 / MinIO: bucket CORS

With `OC_STORAGE_BACKEND=s3` the browser reads signed bucket URLs cross-origin. The studio web origin
needs a CORS rule allowing **GET with the `Range` header** (and exposing `Content-Range`, `Accept-Ranges`,
`Content-Length`) on the bucket, for: preview depth/image fetches, the path overlay on the result page, and
share-page playback. Uploads additionally need PUT . With the default local backend
(files served through the API) no CORS setup is needed.

The compose MinIO (`--profile s3`) is set up for this: `minio-init` creates the bucket, and since MinIO does not
implement per-bucket CORS (`PutBucketCors` is rejected) the server-wide `MINIO_API_CORS_ALLOW_ORIGIN` allows the
studio origin (`http://localhost:3000`; set it in the compose file if the studio is served elsewhere). Set `OC_S3_ENDPOINT_URL=http://minio:9000` for the API/worker and
`OC_S3_PUBLIC_ENDPOINT_URL=http://localhost:9000` so presigned URLs are signed for the host the browser opens.
For AWS S3 or another store with bucket CORS, an example rule (S3 CORS JSON):

```json
[{"AllowedOrigins": ["http://localhost:3000"], "AllowedMethods": ["GET", "HEAD", "PUT"],
  "AllowedHeaders": ["Range", "Content-Type"], "ExposeHeaders": ["Content-Range", "Accept-Ranges", "Content-Length"],
  "MaxAgeSeconds": 3000}]
```
