# Camera Sketch verification (spec section 13) - mock profile

> Note: the three apps were merged into `apps/studio` on 2026-10-26; paths, ports and compose services below describe the runs as they happened.

Source for the PR body. Branch `feat/camera-sketch`. Everything below ran on the **mock_v1** profile (CPU, no GPU,
no Comfy). Nothing here claims a Cinematic quality or accuracy figure; see the last row and the eval sheet.

## Section 13 table

| Item | Pass condition (spec) | Evidence on mock profile | Result |
|---|---|---|---|
| Coordinates | Known-depth synthetic plane/box fixture: left / right / push directions match expectation | `nodes/open_creative/tests/test_cs_camera.py::test_camera_moved_right_moves_point_left`, `::test_push_in_enlarges_box_width`; `test_cs_render.py::test_box_in_front_of_plane_from_plus_x_camera`; `test_cs_path.py::test_slide_moves_plus_x_and_keeps_framing`; `test_cs_scene.py::test_dav2_convention_and_near_bright_gives_smaller_z`; TS `tests/sketch.test.ts` (target right -> negative lateral) | PASS |
| Static path | Fixed target: all Geometry frames identical, no spurious motion | `test_cs_render.py::test_static_path_frames_byte_identical_and_streamed`; `test_cs_path.py::test_static_path_is_identity_everywhere`; `services/worker/tests/test_camera_executors.py::test_guide_static_path_identical_frames_no_holes` (120 frames, 1 distinct rgb sha256, hole ratio max 0) | PASS |
| Front / server | 3 fixed keyframes: target projection difference <= 2 px at output size | `apps/camera-sketch/tests/parity.test.ts` (163 tests, `pnpm --filter @oc/camera-sketch exec vitest run tests/parity.test.ts`): "sampled poses and target projections match (<= 1e-5, <= 2 px)" and 9:16 + 16:9 cases. Measured max errors: target 2.8e-12 px, pose 4.0e-15, Three.js camera 4.5e-13 px. Python side: `test_cs_parity.py` (committed fixture equals a fresh build) | PASS |
| Path solver | Target framing RMS <= 1% of output short side on valid fixtures | `test_cs_path.py::test_slide_moves_plus_x_and_keeps_framing` and `::test_push_is_additive_on_slide_and_arc` assert `diagnostics["framing_rms_rel"] <= 0.01` (slide at the +0.05 bound; small_arc 5 deg + push 0.08) | PASS (these presets; arbitrary sketches are not claimed) |
| Occlusion | Triangles behind a foreground do not pop forward; holes shown correctly | `test_cs_render.py::test_box_in_front_of_plane_from_plus_x_camera`, `::test_target_visibility_occlusion_and_frame`, `::test_fill_small_holes_bbox_at_most_4px_both_sides`; `test_cs_scene.py::test_box_on_plane_cuts_faces_along_boundary` | PASS |
| Length | Output 120 frames / 24 fps, separate from model-internal frame rules | `test_camera_executors.py` (`OUTPUT_CONTRACT` 1280x720, 120 frames, 24 fps; manifest 120 frames; cinematic restart output contract); `test_cs_guides.py::test_crop_from_model_pulls_exactly_n_frames`; `services/api/tests/test_camera_jobs.py::test_cinematic_frame_count_mismatch` (422); `test_cs_render.py::test_timing_120_frames_1280x720_stride2_under_90s` (slow) | PASS |
| Version | Exported path and workflow point to the result's input revision | `test_camera_executors.py::test_cinematic_restart_output_contract_and_exports` (path.json `revision`, `source_job_id`, `guide_job_id`, `guide_revision`; recipe asset `meta.revision` and `conf["revision"]` match the job's input revision); `services/api/tests/test_camera_jobs.py::test_camera_export_job_kind`, `::test_camera_result_videos_are_shareable_with_mode_meta` | PASS |
| Real inference | Depth guide -> LTX result generated at least once on the final deployment | **PENDING** - needs a Comfy deployment with a GPU. The Comfy graphs `camera_depth_v1` / `camera_cinematic_v1` are not exported, binding manifests and model sha256 are empty, and `CONTROL_PRESETS` are uncalibrated, so `open_weights_v1` reports unavailable ("control presets not calibrated"). Not run. | PENDING |

Manual Cinematic/Geometry review (6 photos x 3 presets): sheet in [`camera-sketch-eval.md`](camera-sketch-eval.md), **not yet run**.
Performance targets (30 fps preview, <= 100 ms edit, compile <= 2 s) were not measured in a browser in this task.

## Commands run (final state of the branch)

| Command | Result | Time |
|---|---|---|
| `uv run pytest nodes services -q` | 431 passed, 1 skipped, 12 deselected | 120 s |
| `uv run pytest -m slow nodes services -q` | 12 passed, 432 deselected | 174 s |
| `pnpm -r test` | web-core 17, product-lock 11, camera-sketch 350 tests passed | 3 s |
| `pnpm -r typecheck` | all 5 projects clean | 3 s |
| `pnpm --filter @oc/camera-sketch build` / `@oc/product-lock build` | both build | 10 s / 8 s |
| `docker compose -f infra/docker-compose.yml config` | valid; services include `web` (:3000) and `web-camera` (:3001) | - |
| `docker build -f infra/Dockerfile.web --build-arg APP=camera-sketch --build-arg PORT=3001 .` and the default (product-lock) build | both images build; containers answered HTTP 200 on `/` | ~40 s (cache warm) |
| `scripts/export_app.sh camera-sketch <scratch>` then `pnpm install --frozen-lockfile` and `pnpm --filter @oc/camera-sketch build` there | install 2 s; build succeeds; depth-prep worker emitted as its own chunk | 2 s / 17 s |
| `scripts/export_app.sh product-lock <scratch>` | exports (now also includes `docs/01_PRODUCT_LOCK_SPEC.md`) | - |

Not run: the full `docker compose up` stack (postgres + migrate + api + worker + both webs) and a manual browser
walk-through of the UI; the PostgreSQL-backed test run (`OC_TEST_DATABASE_URL`); the Python half of the standalone export (`uv sync` in the exported tree).

## Full stack walk-through (2026-10-25)

`docker compose -f infra/docker-compose.yml up --build -d` (Docker 29, `.env` from `.env.example` with a random
signing secret, `OC_DEFAULT_PROFILE=mock_v1`), then a Playwright (Chromium) walk-through of each web app. Run twice:
with the default local storage backend, and with `--profile s3` (MinIO, `OC_STORAGE_BACKEND=s3`).

| Step | Result |
|---|---|
| Stack: postgres healthy, `migrate` exits 0, api answers `GET /api/v1/capabilities` (mock_v1 available, open_weights_v1 unavailable: no `COMFY_BASE_URL`), worker starts with 2 slots and claims jobs from Postgres (45 jobs across the three apps, all `succeeded`) | PASS |
| Upload `fixtures/camera-sketch/room.png` (local) / `product.png` (s3) -> Analyze scene (camera.analyze) -> Depth ready, 3D preview | PASS |
| Sketch framing drag -> server compile -> "The server approved this path"; hole warning + "Apply reduced motion" suggestion on the wide room sketch | PASS |
| Render Geometry (warning confirm -> "Render anyway") -> Results view, Geometry video with **Mock** + "Path render" badges, measured holes, path overlay | PASS (about 90 s CPU render in the container for room.png) |
| Downloads: MP4, Path JSON, Guides ZIP, Recipe ZIP | PASS (all four files downloaded) |
| Create share link -> open `/s/<token>`: plays, labelled "Path render" + **Mock** | PASS |
| S3: presigned PUT to MinIO, depth/scene image GETs, path overlay JSON fetch, result video Range GETs (206) cross-origin from `localhost:3001`; video seek works; no console errors, no failed requests | PASS (after the fixes below) |
| Console errors / failed network requests during all runs | none |

Fixed on branch `chore/compose-walkthrough`: `--profile s3` could not start (upstream `minio/minio` images are no longer
pullable; nothing created the bucket; presigned URLs were signed for the container host `minio:9000`, which the browser
cannot open). Now: pinned `pgsty/minio` image, a `minio-init` bucket step, `OC_S3_PUBLIC_ENDPOINT_URL` for presigning,
and `MINIO_API_CORS_ALLOW_ORIGIN` for `localhost:3000`-`3002` (MinIO rejects per-bucket CORS). Preflights from the three
origins get `Access-Control-Allow-Origin`; another origin gets none. Also fixed: the Product Lock share page did not show
the Mock badge (Camera Sketch's did).

Still not run: Generate Cinematic on a real deployment (needs Comfy/GPU), performance targets in a browser, the manual
eval sheet.
