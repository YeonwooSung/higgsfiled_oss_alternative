# Studio full-stack verification (2026-10-26)

The unified studio (`apps/studio`, one web service on :3000) with the API, worker, PostgreSQL and (second run) MinIO,
all in Docker, driven with Playwright (Chromium, 1440x900 unless noted). Profile `mock_v1` throughout, so every
result is a Mock result. Branch `feat/unified-studio` at `3a9cb7b`, then `237294a` (the fix below) from the Camera
Sketch walk-through onwards.

## Commands

```bash
cp .env.example .env     # OC_SIGNING_SECRET=$(openssl rand -hex 32), OC_DEFAULT_PROFILE=mock_v1 (not committed)
# Host port 8000 was taken by another process, so an override outside the repo remapped the API:
#   services: { api: { ports: !override [ "8100:8000" ] } }
docker compose -f infra/docker-compose.yml -f <scratchpad>/compose.override.yml --env-file .env up --build -d
docker compose ... up --build -d web          # after the fix commit (web image only)
# S3 run: in .env OC_STORAGE_BACKEND=s3, OC_S3_ENDPOINT_URL=http://minio:9000,
#         OC_S3_PUBLIC_ENDPOINT_URL=http://localhost:9000, AWS_ACCESS_KEY_ID=minio, AWS_SECRET_ACCESS_KEY=minio-secret
docker compose -f infra/docker-compose.yml -f <scratchpad>/compose.override.yml --env-file .env --profile s3 up --build -d
docker compose -p open-creative -f infra/docker-compose.yml -f <scratchpad>/compose.override.yml --env-file .env --profile s3 down
pnpm --filter @oc/studio test && pnpm --filter @oc/studio typecheck   # 35 files, 501 tests; tsc clean
```

Each run started from a browser with cleared cookies, so "My projects" shows only that run's projects.
Screenshots are kept in the session scratchpad (`.superpowers/sdd/2026-10-25-unified-studio/task-14-*.png`), not committed.

## Results

| Step | Check | Result |
|---|---|---|
| Stack | postgres healthy, `migrate` exits 0, worker starts with 2 slots, `GET /api/v1/capabilities?app_slug=<tool>` for all three tools: `mock_v1` available, `open_weights_v1` unavailable (`COMFY_BASE_URL is not configured` / `control presets not calibrated`) | PASS |
| 1 Product Lock (local) | Upload `can_lock_cola.png` -> Use transparency as mask -> Generate background (estimate dialog shows Mock) -> Render ad -> "Product protection verified", 120 frames, max error 0/1 -> MP4, Cover PNG, Recipe ZIP downloaded -> Share -> `/s/<token>` plays with **Mock** | PASS |
| 1 Camera Sketch (local) | Upload `room.png` -> Analyze -> Depth ready -> Sketch framing drag -> hole warning + "Apply reduced motion" + lateral suggestion, "The server approved this path" -> Render Geometry -> "Render anyway" -> guide done in ~27 s -> Results: Geometry, Path render, **Mock**, measured holes max 33.7% -> MP4, Path JSON, Guides ZIP, Recipe ZIP downloaded -> share page plays, "Path render" + **Mock** | PASS (after the fix below) |
| 1 Pocket VFX (local) | Upload `stomp_landscape.mp4` -> Prepare clip -> Impact Wave: Select subject (figure), Anchor at the feet, Track -> "Tracked on 120 of 120 frames" -> Render (Quick) -> "Render finished." -> Result: Quick + **Mock**, "footage unchanged (max error 0/255)", "subject's core is untouched", "No tracking or mask warnings" -> MP4, Cover PNG, Recipe ZIP, Layers ZIP downloaded -> share page plays, Quick + **Mock** | PASS |
| 2 Demo chain (local) | Product Lock render (`can_lock_cola`) -> "Move the camera in Camera Sketch" -> opens `/camera/p/<id>` with the cover already linked and "From Product Lock result" badge -> Analyze -> Render Geometry -> "Add an effect in Pocket VFX" -> opens `/vfx/p/<id>` with the geometry MP4 linked (Trim step) and "From Camera Sketch result" badge -> Prepare clip -> subject, anchor, Track -> Quick render -> share | PASS |
| 2 pass criteria (local) | Each target opens with its input linked: yes. Provenance badges: yes (both links go back to the source project). Final result and its `/s/` page show Mock: yes. "My projects" lists all three (`can_lock_cola`, `can_lock_cola → Camera Sketch`, `can_lock_cola → Camera Sketch → Pocket VFX`): yes. Recipe ZIPs: Camera Sketch and Pocket VFX `config.json` have a `handoff` block (`source_app`, `source_project_id`, `source_asset_id`, `source_job_id`, `source_sha256`, `source_title`, `mock: true`) and the README says the input came from a Mock result. `source_sha256` equals the sha256 of the downloaded Product Lock cover / Camera Sketch MP4 | PASS |
| 3 Sidebar navigation | Tool links go to `/camera`, `/product`, `/vfx`; the active tool has `aria-current="page"`; project links open the project | PASS |
| 3 Collapse persisted | 1440 px: Collapse -> rail 56 px; reload -> still 56 px with "Expand sidebar"; Expand -> 200 px | PASS |
| 3 Drawer at 390 px | Sidebar hidden; top-bar "Open menu" opens the drawer (dialog) with tools and My projects; choosing a tool navigates and closes it; no horizontal scroll (scrollWidth 390) | PASS |
| 3 Language toggle | On a project of each tool, the toggle switches the sidebar blurbs and the tool's own text together (`<html lang>` en -> ko and back) | PASS (two small leftovers, see notes) |
| 3 Console / network | Errors seen: `401 GET /api/v1/projects` on a fresh visit (bug 1), and 403 + 409 when going back to a Product Lock project (bug 2). Nothing else failed during either run | FAIL (bugs 1 and 2) |
| 4 Demo chain (S3 / MinIO) | Same chain with `bottle_moss.png`: badges, linked inputs, Mock on result and `/s/` page, three projects listed, `handoff` blocks with matching sha256. Browser traffic to MinIO: 1 presigned PUT (200), 21 GET (200), 8 Range GET (206), cross-origin from `localhost:3000`, all videos `readyState 4`; preflight from `http://localhost:3000` gets `Access-Control-Allow-Origin` | PASS |
| 4 Console / network (S3) | Only the fresh-visit 401 (bug 1) | FAIL (bug 1) |
| 5 Clean up | `docker compose -p open-creative ... --profile s3 down` (volumes kept); browser closed; the other process on :8000 untouched | done |

## Bugs

**Fixed here (`237294a`): another project's jobs and results shown after switching projects.** Opening a second
Camera Sketch project from the sidebar kept the first project's jobs in the editor store: the new `room` project
listed five jobs (it had one), showed "Depth ready" before its own analysis, and its Results view showed a Geometry
result with MP4 / Recipe / Share buttons that belonged to `can_lock_cola → Camera Sketch` (screenshot
`task-14-camera-jobs-leak.png`; `GET /projects/<room>` returned only `camera.analyze` and `asset.inspect`). Cause:
`load()` merged `jobs: { ...get().jobs, ...jobs }` even when the project id changed
(`apps/studio/src/features/camera-sketch/state/editorStore.ts:179`, same in
`apps/studio/src/features/product-lock/state/editorStore.ts:90`). Pocket VFX already reset them
(`pocket-vfx/state/editorStore.ts:161`). Fix: the same guard in both stores, with a regression test each
(`tests/camera-sketch/editorStore.test.ts`, new `tests/product-lock/editorStore.test.ts`; both fail without the fix).
Re-checked in the rebuilt web container: `room` then showed only its own job. Not fixed: Product Lock's
`selectedRenderJobId` is not reset in `load()` either (`product-lock/state/editorStore.ts:67,123`), so a render
selected in one project can stay selected after switching; not observed in the UI during this run.

**1. Fresh visitor: "Couldn't load projects" and a 401 in the console.** With no session cookie the sidebar's
`GET /api/v1/projects?limit=50` (`apps/studio/src/shell/Sidebar.tsx:83`) runs before any tool has called
`POST /sessions`, gets 401, and the sidebar shows the error with Retry (`task-14-fresh-session-401.png`). It recovers
once a tool creates the session (the first upload invalidates the query). Reproduced at the start of both runs.
Possible fix: treat 401 as "no projects yet" in the sidebar query (or start the session before listing).

**2. Going back to a Product Lock project: expired asset URLs (403) and a false "Changed elsewhere — reloaded" (409).**
Reproduced: open a Product Lock project, work on it, go to Camera Sketch via the hand-off, then click
"From Product Lock result". Requests on return:
`PATCH /projects/<id>` with `expected_revision: 1` -> `409 revision_conflict` (`current_revision: 4`), then
`GET /projects/<id>` and the editor reloads; the top bar shows "Changed elsewhere — reloaded" although nothing changed
elsewhere (`task-14-product-return-409.png`). Before that, five `403` on `/api/v1/storage/o/...` (product, mask,
background, render).
Cause: the editor's project query has `staleTime: Infinity` (`apps/studio/src/features/product-lock/components/Editor.tsx:40`),
so on client-side navigation back it feeds `load()` the cached response of the *first* `GET` of that project.
That snapshot is revision 1, from before upload inspection finished (product asset `uploaded`, `product_asset_id` null),
and its signed read URLs expired (`read_url_ttl_s = 300`, `services/api/oc_api/settings.py:29`) -> the 403s.
The `uploaded` asset starts the refresh poll; `refresh()` brings the asset list up to date but keeps the stale config,
so the "bind the uploaded product" render-time update (`apps/studio/src/features/product-lock/components/LeftPanel.tsx:29-35`)
fires and autosaves with revision 1 -> 409 -> `onConflict` reloads (`product-lock/state/editorStore.ts:81`).
The reload restores the right state, so no data is lost; the 409 is what prevents the stale config (mask cleared)
from being saved. Camera Sketch and Pocket VFX refetch on return and showed neither problem.
Possible fix: refetch on mount (drop `staleTime: Infinity`, or `refetchOnMount: "always"` and don't `load()` cached
data), or keep the query cache in step with the store.

## Notes (not failures)

- Camera Sketch has no output-aspect control and defaults to 16:9 (`services/api/oc_api/camera_sketch/config.py:52`),
  so the 9:16 Product Lock cover becomes a 1280x720 center crop of the bottle in the chain. Pre-existing limitation,
  more visible now that a portrait cover is handed over.
- Mock tracking on chained clips is weak: on the `can_lock_cola` geometry clip the anchor tracked on 1 of 120 frames
  ("Tracking lost on frames 35–43, 45–83: the effect is hidden there"); on the S3 `bottle_moss` clip the track held
  (120/120) but the mock subject mask was empty except on the keyframe ("Mask is empty on frames 1–43, 45–120").
  Render, checks, share and recipe still worked. The fixture clip `stomp_landscape.mp4` has no warnings.
- Language: Product Lock preset descriptions come from `presets/product-lock/presets.v1.json` in English only, and the
  stage's "Protected" toggle is "Protected" in `product-lock/i18n/ko.ts:75` too.
- Mock results took: Product Lock background + render about 25 s, Camera Sketch geometry about 27 s (room.png),
  Pocket VFX prepare + track + quick render under a minute, Layers ZIP about 30 s.

Still not run: real inference (Comfy/GPU) for any tool, Generate Cinematic / Generate effect plate on a real
deployment, Blink Out with a clean plate.

## Fixes after verification (2026-10-27)

Both open failures are fixed on `feat/unified-studio` (commits `723f433`..`86b6f6a`), together with the final review's
other findings.

- **Bug 1 (fresh visitor 401).** `GET /projects` without a session now answers `200 {"projects": []}` and starts no
  session (`optional_session` in `services/api/oc_api/deps.py`); the sidebar query also treats a 401 as an empty list
  (`listProjectsOrEmpty` in `apps/studio/src/shell/projectQuery.ts`). An editor that has to start a session refreshes
  the sidebar's list (`withSession`).
- **Bug 2 (Product Lock return: 403 + false 409).** Each editor's project query (Product Lock, Camera Sketch,
  Pocket VFX) now uses `gcTime: 0` with `staleTime: Infinity` (`editorProjectQuery`), so a remount loads the server's
  current revision instead of the first cached GET. The three copies of `useAssetUrl` became one shared hook
  (`apps/studio/src/shell/assetUrl.ts`) that never returns a URL older than 4.5 minutes (server URLs live 300 s) and
  times its refresh from when the URL was fetched.
- Also: Product Lock resets the Result view, render selection and tool when another project opens; Product Lock,
  Camera Sketch and Pocket VFX drop late `refresh()`/conflict responses for a project that is no longer open; a
  replayed hand-off whose target project was deleted is 404; a failed storage cleanup in `POST /handoffs` is logged
  and no longer hides the original error.

How it was checked: new unit tests (`tests/shell/projectQuery.test.ts`, `tests/shell/assetUrl.test.ts`, store cases in
`tests/product-lock/editorStore.test.ts` and `tests/camera-sketch/editorStore.test.ts`, API cases in
`test_projects.py` and `test_handoffs.py`; the store and API cases fail on the code before the fix), then a local
re-run (SQLite + local storage, mock profile, production studio build) in Playwright:

| Check | Result |
|---|---|
| Fresh browser context on `/camera` | "No projects yet", no console errors, no 4xx, no session cookie set |
| Product Lock sample -> background -> render -> hand cover to Camera Sketch -> provenance badge back -> edit | `PATCH` 200, no 403/409, no "Changed elsewhere" |
| Back to the Camera Sketch project from the sidebar (within 5 min) after editing it -> edit again | Earlier edit shown (current revision), `PATCH` 200, no 409 |
| On Product Lock project A's Result, open project B from the sidebar | B opens on Output with its stage; Result disabled; no errors |
| Return to Product Lock A 294 s after leaving it (inside the old 403 window) | All images/video load with freshly signed URLs; no 403 |
| API log over the whole run | 0 responses with 4xx/5xx |

Screenshots: `.superpowers/sdd/2026-10-25-unified-studio/final-fix-*.png` (worktree only).
