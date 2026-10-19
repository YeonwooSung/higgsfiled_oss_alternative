# Product Lock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Product Lock P0 vertical slice — upload a product image, confirm mask/core, pick a background preset, generate background image → background video → deterministic composite with a verified protected core → MP4/cover/recipe export.

**Architecture:** A pure Python imaging core (`open_creative`, no Comfy/torch dependency) implements geometry, masks, locked-product resampling, composite, shadow and integrity checks; it is reused by the CPU worker and by thin Comfy custom-node wrappers. FastAPI (`oc_api`) owns sessions/projects/revisions/assets/jobs on PostgreSQL (SQLite in unit tests); the worker (`oc_worker`) claims jobs with a DB lease and runs stages through a `WorkflowRunner` adapter (mock profile now, Comfy SDK profile once real exported graphs exist). Next.js app `apps/product-lock` is the editor.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, NumPy, Pillow, OpenCV-headless, FFmpeg; Next.js App Router + React + TS, Tailwind, Radix, Lucide, Zustand, TanStack Query, react-konva; vitest; pytest; uv workspace; pnpm workspace.

**Spec:** `docs/00_SHARED_IMPLEMENTATION_SPEC.md`, `docs/01_PRODUCT_LOCK_SPEC.md`

## Global Constraints

- Prefix `/api/v1`; errors are `{error:{code,message,field?,retryable,request_id}}`; never expose stack traces or provider keys.
- All IDs UUID, timestamps UTC. Owner/session check on every API route; other-session asset references rejected.
- Image input: JPG/PNG/WebP, ≤ 20MB, ≤ 24MP, EXIF orientation applied then metadata stripped, sRGB normalized; decoder-verified MIME; pixel bomb blocked.
- Output: 1280×720 (16:9) or 720×1280 (9:16), H.264 yuv420p MP4, 24 fps, 5 s = 120 frames. Preview 640×360 / 360×640 marked Draft.
- Upload URL TTL 10 min, read URL 5 min. 413 size, 415 format, 422 model/config mismatch, 429 quota, 503 profile disabled, 409 revision/idempotency conflict.
- Idempotency-Key: same session+endpoint+body hash → same job; different body → 409; retention 24 h.
- Quotas: 1 active GPU job/session, 1 active render/project, max 3 queued/session.
- Job states: queued → preparing → submitting → running → postprocessing → succeeded; plus failed, cancel_requested, cancelled, submission_unknown. Never show 100% before success; unknown progress = null.
- Lease 60 s, heartbeat 15 s, `SELECT … FOR UPDATE SKIP LOCKED`. Provider ID known → inspect, never resubmit. Timeout with unknown ID → `submission_unknown`.
- Costs: never hard-code prices; unknown actual usage stays null (not 0); units never mixed.
- Core preservation: alpha=1 inside core; no generation/color/particles/shadow on core; preencode check max channel error ≤ 1 (of 255); empty core = invalid (not pass).
- Product transform: width_ratio 0.15–0.70, feather 0–4 px (output), rotation_deg must be 0; shadow opacity 0–0.6, blur 0–40 px; background brightness −0.2–0.2; extra prompt ≤ 300 chars; brush 2–80 source px.
- Kinds allowlist: product.segment, product.background, product.background_video, product.render, product.inspect (+ internal asset.inspect, export).
- Mock profile always visibly labelled "Mock". No runtime graph upload; only bindings (image, prompt, seed, mask, resolution, frame count) are substituted.
- No user input builds file paths, shell commands or FFmpeg filter strings.
- UI tokens: bg #F5F4F0, panel #FFFFFF, stage #E9E8E3, text #171A18, muted #666D68, accent #18794E, border #DADDD7, panel radius 12, button radius 8; Inter + Noto Sans KR; body 14/1.5; touch target ≥ 44 px; autosave debounce 600 ms; undo history 50.
- Repo `.gitignore` ignores `lib/`: never name a source directory `lib`.

## Review Focus

1. Product placed partly outside the frame (center near edge, large width) — composite and core check must clip, not crash; core pixels outside the frame are excluded from the check.
2. Alpha PNG with fully transparent image or tiny product — core empty → `invalid core`, render blocked with "core too small" before Generate.
3. Background video whose size/aspect/frame count differs from output (e.g. 121 frames, 768×512) — cover-fit and trim/hold to exactly 120 frames.
4. Late segmentation finishing after a manual mask edit — must not overwrite the newer mask (revision pinning).
5. Worker crash after provider submit — restart must inspect existing provider job, not resubmit.

Each is pinned by a test in Tasks 1, 3, 5, 13 and 12 respectively.

---

## File Structure

```
pyproject.toml                         uv workspace root (dev deps: pytest, httpx)
nodes/open_creative/                   pure imaging core + Comfy node wrappers
  open_creative/product_lock/geometry.py   output sizes, placement
  open_creative/product_lock/masks.py      alpha→mask, core computation, protect boxes
  open_creative/product_lock/locked.py     LockedProduct (premultiplied resample, feather)
  open_creative/product_lock/layers.py     background brightness, particles, contact shadow
  open_creative/product_lock/composite.py  frame/sequence composite
  open_creative/product_lock/integrity.py  preencode + postencode QA
  open_creative/product_lock/prompts.py    preset loading + background prompt
  open_creative/media/image.py             normalize_image
  open_creative/media/video.py             encode/decode/probe, fit_sequence
  open_creative/comfy_nodes.py             PL* node classes (NODE_CLASS_MAPPINGS)
services/api/oc_api/                   FastAPI app, models, routes, storage, config schema
services/worker/oc_worker/             lease loop, stage runner, executors, comfy adapter
workflows/product-lock/                binding manifests + README (graphs exported from ComfyUI)
presets/product-lock/presets.v1.json
models/product-lock.manifest.json
fixtures/product-lock/                 generated fixtures + generator script
apps/product-lock/                     Next.js editor
packages/contracts/                    generated TS types from OpenAPI
infra/                                 docker-compose, Dockerfiles, .env.example
```

---

### Task 1: Workspace + geometry

**Files:** Create `pyproject.toml`, `nodes/open_creative/pyproject.toml`, `nodes/open_creative/open_creative/product_lock/geometry.py`; Test `nodes/open_creative/tests/test_geometry.py`

**Interfaces — Produces:**
- `output_size(aspect: Literal["16:9","9:16"], draft: bool=False) -> tuple[int,int]` (w,h)
- `@dataclass(frozen) Placement(x0:int, y0:int, w:int, h:int)`
- `place_product(src_w, src_h, out_w, out_h, center_x, center_y, width_ratio, rotation_deg=0) -> Placement` — `w=round(width_ratio*out_w)`, `h=round(w*src_h/src_w)`, `x0=round(center_x*out_w - w/2)`, `y0=round(center_y*out_h - h/2)`; ValueError if width_ratio∉[0.15,0.70] or rotation≠0.
- `Placement.visible(out_w,out_h) -> tuple[slice,slice,slice,slice]` (frame ys, xs, product ys, xs) for clipping.

- [ ] Tests: `output_size("9:16")==(720,1280)`, draft `(360,640)`; placement of 1000×2000 src at width 0.36 in 720×1280 → `w=259,h=518`; rotation 5 raises; width 0.8 raises; **edge case**: center_x=0.98 → `visible` slices clip to frame width and product slice widths match.
- [ ] Run `uv run pytest nodes/open_creative/tests/test_geometry.py` → FAIL, implement, PASS, commit.

### Task 2: Masks and core

**Files:** `open_creative/product_lock/masks.py`; Test `tests/test_masks.py`

**Produces:**
- `mask_from_alpha(alpha_u8: ndarray HxW) -> ndarray float32 [0,1]`
- `rasterize_boxes(boxes: list[dict(x,y,w,h normalized)], width, height) -> (bool HxW, clipped: bool)`
- `compute_core(mask: float32, user_core: bool|None, erosion_px: int=3) -> bool` = (erode(mask≥0.999) ∪ user_core) ∩ (mask≥0.5)
- `core_fraction(core, mask) -> float`; `CORE_MIN_FRACTION = 0.02`

- [ ] Tests: opaque square mask core is eroded by 3 px; user box outside the product is intersected away; box partly outside image returns `clipped=True`; empty mask → empty core and fraction 0.
- [ ] Red → green → commit.

### Task 3: LockedProduct, layers, composite

**Files:** `locked.py`, `layers.py`, `composite.py`; Tests `test_locked.py`, `test_composite.py`

**Produces:**
- `@dataclass LockedProduct(rgb: uint8 hxwx3, alpha: float32 hxw, core: bool hxw, placement: Placement)`
- `build_locked_product(rgb, mask, core, placement, feather_px: float) -> LockedProduct` — resample premultiplied `rgb*mask` and `mask` with `cv2.INTER_AREA` (down) / `INTER_LINEAR` (up); unpremultiply and round to uint8; core resampled with `INTER_NEAREST` then ∩ (alpha≥0.999 before feather); feather = gaussian on alpha applied only where `not core`; alpha forced to 1.0 on core.
- `expected_reference(locked) -> (rgb uint8, core bool)` (the reference the integrity check compares to).
- `adjust_brightness(frame, amount: float) -> uint8`; `contact_shadow(locked, out_w, out_h, opacity, blur_px) -> float32 HxW`; `apply_shadow(frame, shadow) -> uint8`; `add_particles(frame, t: int, seed: int, kind: Literal["none","bokeh","neon"]) -> uint8`.
- `composite_frame(background: uint8 HxWx3, locked, shadow: float32|None) -> uint8` — `C = round(A*P + (1-A)*B)` in visible region.
- `composite_sequence(backgrounds: Iterable[ndarray], locked, layer_cfg: LayerConfig) -> Iterator[ndarray]` with `LayerConfig(brightness, shadow_opacity, shadow_blur, shadow_enabled, particles, seed)`; shadow computed once.

- [ ] Tests: composite of a red product over random background equals reference on core exactly; brightness +0.2, shadow 0.6 and particles all leave core unchanged; edge pixels blend (0<A<1 exists when feather>0); product partially off-frame composites without error.
- [ ] Red → green → commit.

### Task 4: Integrity check

**Files:** `integrity.py`; Test `test_integrity.py`

**Produces:**
- `@dataclass FrameCheck(index, max_error:int, core_pixels:int)`
- `class CoreChecker(locked)`: `.check(frame, index) -> FrameCheck`; `.report() -> dict` with keys `core_valid`, `core_pixel_count`, `frames_checked`, `preencode_max_error`, `passed` (`core_valid and max ≤ 1`), `threshold=1`.
- `postencode_metrics(decoded_frames: Iterable, locked) -> dict(mean_abs_error, max_error, ssim_mean, frames)` — diagnostic only.

- [ ] Tests: perfect composite → passed, max 0; frame with one core pixel shifted by 2 → failed, max 2; empty core → `core_valid False, passed False`; core entirely off-frame → `core_valid False`.

### Task 5: Media normalization and video IO

**Files:** `open_creative/media/image.py`, `open_creative/media/video.py`; Tests `test_media_image.py`, `test_media_video.py`

**Produces:**
- `class MediaError(code: Literal["too_large","unsupported_format","too_many_pixels","decode_failed"])`
- `normalize_image(data: bytes, max_bytes=20MB, max_pixels=24_000_000) -> NormalizedImage(rgb uint8, alpha uint8|None, width, height, mime)`; `to_png_bytes(rgb, alpha|None) -> bytes`
- `encode_mp4(frames: Iterable[ndarray], path, w, h, fps=24, crf=18) -> None` (ffmpeg stdin rawvideo → libx264 yuv420p, `-movflags +faststart`)
- `probe(path) -> dict(width,height,fps,frame_count,codec,pix_fmt,duration_ms)`
- `decode_frames(path, w=None, h=None) -> Iterator[ndarray]`
- `fit_sequence(frames: Iterable, out_w, out_h, n=120) -> Iterator` — cover-fit (scale+center-crop) each frame; trim beyond n; hold last frame if short; raise if empty.

- [ ] Tests: EXIF orientation 6 JPEG returns rotated dims; PNG with alpha keeps alpha; text bytes → `unsupported_format`; 21MB → `too_large`; 5000×5000 header → `too_many_pixels` without full decode; encode 12 frames then probe → h264/yuv420p/24fps/12 frames; **fit_sequence** of 121 frames 768×512 → 120 frames 720×1280; 3 frames → 120 frames.

### Task 6: Fixtures and golden preservation test

**Files:** `fixtures/product-lock/make_fixtures.py`, generated `fixtures/product-lock/*.png` (5 opaque products with small label text, alpha PNG), `nodes/open_creative/tests/test_golden.py`

- [ ] Test: for each of 5 fixtures × both aspects, full 120-frame composite with shadow+particles+brightness over a moving synthetic background passes `CoreChecker` (`passed` True, max ≤ 1); also encode MP4 and record postencode metrics (assert `ssim_mean > 0.9` as diagnostic floor).

### Task 7: Presets, prompt builder, config schema

**Files:** `presets/product-lock/presets.v1.json`, `open_creative/product_lock/prompts.py`, `services/api/oc_api/product_lock/config.py`; Tests

**Produces:**
- Presets `soft_studio_v1`, `neon_studio_v1`, `botanical_v1` with `version`, `name`, `prompt`, `negative`, `default_accent`, `allowed_motion`, `particles`, `prompt_fields`.
- `load_presets(path=None) -> dict[str, Preset]`; `build_background_prompt(preset, accent_hex, composition: Literal["center"], extra_prompt) -> (str, str)`; extra prompt stripped of control chars, >300 → ValueError.
- `ProductLockConfig` pydantic: spec §8 fields, plus `transform.feather_px` (0–4, default 1.0), `lighting.background_brightness` (−0.2–0.2), `protect_boxes: list[Box]`; all asset ids optional; `rotation_deg` Literal[0]; `output.duration_ms` Literal[5000]; `output.fps` Literal[24]; `accent_hex` regex `^#[0-9A-Fa-f]{6}$`.

### Task 8: Comfy adapter + mock profile

**Files:** `services/worker/oc_worker/comfy/{runner.py,bindings.py,mock.py,sdk.py,profiles.py}`, `workflows/product-lock/*.bindings.json`, `workflows/product-lock/README.md`

**Produces:**
- `WorkflowRunner` Protocol (spec §4.1): `capabilities()`, `submit(graph_path, bindings, submission_key) -> str`, `inspect(id) -> dict(state, progress)`, `events(id, cursor=None)`, `collect(id, target_dir) -> list[Path]`, `cancel(id) -> dict`.
- `ProviderTimeout(Exception)` (raised when submission outcome unknown), `ProviderRejected(Exception)`.
- `validate_bindings(graph: dict, manifest: dict, values: dict) -> None` raises on unknown node/field, missing binding, out-of-range value.
- `MockRunner(seed-deterministic)` workflows: `product_segment_v1` (OpenCV GrabCut from box/points → PNG mask), `product_background_image_v1` (preset-colored gradient studio PNG), `product_background_video_v1` (121-frame MP4 with slow light drift). Duplicate submission_key → `ProviderRejected`.
- `ComfySdkRunner` lazily imports `comfy_sdk`; `get_runner(profile) -> WorkflowRunner`; profile `open_weights_v1` enabled only if every graph file listed in its bindings exists; otherwise disabled (→ 503).

### Task 9: API foundation — models, sessions, projects

**Files:** `services/api/oc_api/{settings.py,db.py,models.py,errors.py,app.py,deps.py,routes/sessions.py,routes/projects.py}`, `services/api/alembic/…`; Tests `services/api/tests/test_projects.py`

- Models exactly per spec §5 tables (+ `idempotency_records(session_id, endpoint, key, request_hash, job_id, created_at)`).
- `POST /sessions` sets cookie `oc_session` (HttpOnly, SameSite=Lax, Secure from settings). Missing/expired session → 401 `session_required`.
- `PATCH /projects/{id}` with stale `expected_revision` → 409 `revision_conflict`; invalid config → 422.
- Tests: create session/project; GET other session's project → 404; revision conflict 409; revisions immutable (old revision config unchanged).

### Task 10: Storage + asset API

**Files:** `oc_api/storage.py` (`LocalStorage` with HMAC signed URLs, `S3Storage` boto3 presign), `routes/assets.py`, `routes/storage.py`

- `init` validates role ∈ {product, mask, core_mask, background, background_video}, mime allowlist, bytes ≤ 20MB image / 100MB video → 413/415; returns `{asset_id, upload_url, headers, expires_at}`.
- `complete` verifies stored bytes, sha256 match (409 `hash_mismatch`), sets state `uploaded` and enqueues `asset.inspect` job; GET returns state + metadata + 5-min read URL when `ready`.
- Tests: expired/forged token 403; sha mismatch; mask asset referenced by other session rejected.

### Task 11: Jobs API

**Files:** `oc_api/routes/jobs.py`, `oc_api/jobs_service.py`, `oc_api/product_lock/routes.py`

- `POST /projects/{id}/jobs` validates kind allowlist, options per kind (pydantic), revision exists, referenced assets belong to project and are `ready` (422 `asset_not_ready`), mask dims equal product dims (422), core non-empty for render (422 `core_too_small`), quotas (429), profile availability (503). Creates job + `job_costs` reservation. Idempotency per constraints.
- `GET /jobs/{id}`, `GET /jobs/{id}/events` (SSE, `id:` = sequence, honors `Last-Event-ID`, terminates on terminal state), `POST /jobs/{id}/cancel` (queued → cancelled immediately; running → cancel_requested).
- `POST /projects/{id}/estimates`, `GET /capabilities`, `GET /product-lock/presets`, `GET /jobs/{id}/integrity`.
- Tests: idempotent replay returns same job; different body → 409; 2nd GPU job → 429; render without core → 422; cancel queued → cancelled.

### Task 12: Worker lease + stage runner

**Files:** `oc_worker/lease.py`, `oc_worker/stages.py`, `oc_worker/main.py`

- `claim_next(db, worker_id, now) -> Job|None` (`with_for_update(skip_locked=True)`, eligible: queued or lease-expired non-terminal, sets lease 60 s).
- `run_provider_stage(ctx, stage_name, graph, bindings) -> list[Path]`: persist `submission_key=f"{job.id}:{stage}:{attempt}"` before submit; if `provider_job_id` set → inspect/collect; `ProviderTimeout` → job `submission_unknown`; checks `cancellation_requested_at` at boundaries.
- Tests: crash after submit (provider id stored, lease expired) → second worker collects without a second submit (runner submit count 1); timeout → `submission_unknown`; cancel_requested between stages → `cancelled`.

### Task 13: Executors

**Files:** `oc_worker/executors/{asset_inspect.py,segment.py,background.py,render.py,inspect.py,export.py}`

- `asset.inspect`: normalize image (stores normalized PNG + metadata, state `ready` or `rejected` with code); alpha PNG product auto-creates a suggested `mask` asset.
- `product.segment`: runs runner `product_segment_v1`; result mask asset recorded on job outputs only — applied to project config only if project's current `mask_asset_id` equals the one at the pinned revision (late result never overwrites a newer manual mask).
- `product.background` / `product.background_video`: runner stages; outputs saved as assets.
- `product.render`: background video (reuse id or job option) → `fit_sequence` → composite → `CoreChecker` on every lossless frame → encode MP4 → cover PNG (frame 0) → postencode metrics → integrity JSON asset. No background provider stage executed when reusing.
- Tests: render job with mock end-to-end produces mp4 120 frames + integrity passed; reuse render submits zero provider jobs; late segmentation does not change config.

### Task 14: Exports, shares, delete

- `POST /projects/{id}/exports {job_id, format: mp4|cover_png|recipe_zip}`; recipe ZIP = config revision JSON, preset, binding manifests, model manifest, integrity JSON, README.
- Shares: token (32 bytes urlsafe) stored as sha256; `GET /s/{token}` public read redirect; revoke; delete project → 404 immediately + storage cleanup scheduled.

### Task 15: Comfy nodes, manifests

- `open_creative/comfy_nodes.py`: `PLProductMask`, `PLLockedProduct`, `PLBackgroundPrompt`, `PLCompositeSequence`, `PLCoreIntegrityCheck` with `INPUT_TYPES/RETURN_TYPES/FUNCTION/CATEGORY`, tensor conversion via duck-typing (`.cpu().numpy()` when present). Tests call node functions with numpy arrays.
- `models/product-lock.manifest.json` (SAM 2.1 small, FLUX.1-schnell, LTX-2.3 distilled v1.1; license, source, sha256 `null` + `verified:false`).

### Task 16–19: Web app

- 16: scaffold `apps/product-lock` (Next 15, Tailwind tokens, rewrites `/api/*` → API), `packages/contracts` generated from OpenAPI, typed API client, session bootstrap.
- 17: Zustand editor store — config history (50), undo/redo, 600 ms autosave debounce with `expected_revision`, conflict reload. vitest tests.
- 18: Start screen (Upload product / Try sample / preset gallery) and Editor (react-konva stage with product placement, mask brush add/remove 2–80 px, protect boxes with clipping notice, inspector groups, Generate background / Render ad with estimate dialog, job panel via SSE fallback polling after 3 failures, Mock badge).
- 19: Result screen (original/result toggle, 200% label zoom, two separate badges "Product protection verified" / "Video compression check", Export MP4/Cover/Recipe, Share).

### Task 20: Infra and docs

- `infra/docker-compose.yml` (postgres, minio, api, worker, web), Dockerfiles, `.env.example`, `LICENSE` (MIT), `README.md` (setup, mock vs Comfy profile, known limits), E2E smoke script running mock profile: sample → render → download.
