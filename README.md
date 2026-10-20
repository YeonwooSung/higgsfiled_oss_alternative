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
```

Or everything in Docker (PostgreSQL, migrations, API, worker, both web apps — :3000 and :3001):

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
