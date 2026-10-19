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
packages/ui            shared UI primitives        packages/contracts   API types
services/api           FastAPI + SQLAlchemy + Alembic (sessions, projects, assets, jobs, shares)
services/worker        DB-lease job worker, stage runner, Comfy adapter (mock + comfy-sdk)
nodes/open_creative    imaging core (NumPy/OpenCV/FFmpeg) + ComfyUI custom nodes (PL*)
workflows/product-lock binding-manifest contract for the exported Comfy graphs
presets/product-lock   background presets          models/              model manifest (no weights)
fixtures/product-lock  synthetic test products     infra/               Dockerfiles, compose
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
```

Or everything in Docker (PostgreSQL, migrations, API, worker, web):

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
- No prices are hard-coded. Estimates show reserved GPU-second caps and "price unknown".
- Code is MIT. Model weights keep their own licenses (LTX-2.3 uses a community license).
