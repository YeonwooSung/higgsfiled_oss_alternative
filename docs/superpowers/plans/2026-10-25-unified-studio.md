# Unified Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the three Next.js apps (Product Lock, Camera Sketch, Pocket VFX) into one app `apps/studio` with a labelled left sidebar, a cross-tool project list, and server-side result hand-offs between tools.

**Architecture:** Backend gains `GET /projects`, `Storage.copy`, `POST /handoffs` (copy a finished output into a new project of another tool, then run the normal `asset.inspect`), and upstream-mock propagation. The frontend moves each app's `src` unchanged into `apps/studio/src/features/<tool>/`. A thin shell (routes, sidebar, top bar, theme scopes, shared locale) wraps the features. Old apps, per-app Docker services and the export script are removed at the end.

**Tech Stack:** FastAPI + SQLAlchemy (Python 3.12, uv), Next.js 15 App Router + React 19 + Tailwind 3 + Zustand + TanStack Query (pnpm, Node 20), vitest, pytest, Playwright MCP for the final walk-through.

**Spec:** `docs/superpowers/specs/2026-10-25-unified-studio-design.md` (Korean). Executors read both.

## Global Constraints

- Package `@oc/studio` lives at `apps/studio`. Its `dev`/`start` scripts use port **3000**.
- Routes:
  - `/` redirects to `/camera`.
  - Tools: `/camera`, `/product`, `/vfx`.
  - Editors: `/camera/p/[id]`, `/product/p/[id]`, `/vfx/p/[id]`.
  - Public share page: `/s/[token]`.
  - No redirects from the old per-port URLs.
- Pin these exact versions: `konva` 9.3.22, `react-konva` 19.2.7 (Next 15 bundles React 19.2 canary), `three` 0.186.1, `@types/three` 0.186.0, `fast-png` 7.0.1. Other dependency ranges come from the existing apps.
- `API_ORIGIN` is read at build time (Next rewrites). Keep the `/api/:path*` rewrite, the `canvas: false` webpack alias, the security headers and `transpilePackages: ["@oc/ui", "@oc/contracts", "@oc/web-core"]`.
- Every new UI string needs an `en` and a `ko` entry.
- Every `localStorage` read and write is wrapped in try/catch. The UI renders correctly when storage is unavailable.
- Hand-off allowlist, keyed by (source project `app_slug`, source asset `role`) → target app / upload role:
  - (`product-lock`, `render`) → `pocket-vfx` / `clip`
  - (`camera-sketch`, `geometry_video`) → `pocket-vfx` / `clip`
  - (`camera-sketch`, `cinematic_video`) → `pocket-vfx` / `clip`
  - (`product-lock`, `cover`) → `camera-sketch` / `scene`
  - Anything else is rejected with 422 `handoff_not_allowed`.
- Mock rule: a result is labelled Mock if its own profile is mock **or** any input of its project came from a mock result (`upstream_mock`). This applies to the result view, the recipe and the share meta.
- Feature specs (docs/01–03) limits are unchanged. Hand-off inputs pass the same size check and the same `asset.inspect`.
- Do not touch Comfy, GPU or `open_weights_v1` code.
- Never kill processes by name or pattern. The user may run their own API on :8000.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Do not push.

## Review Focus

1. **Source project deleted after the hand-off.** The target keeps working. The provenance badge shows text without a link (`source_exists: false`). `upstream_mock` still holds because it is stored on the copied asset at hand-off time, not recomputed from the source. Tested in Task 4.
2. **Double-click or retry on a hand-off button.** It must create exactly one project. The server replays on the same `Idempotency-Key` (Task 3). The client keeps one key per (button, asset) for the lifetime of the result view (Task 11).
3. **Hand-off of a file too large for the target role** (e.g. a large Product Lock render over Pocket VFX `clip` 100 MB). Expect 413 `too_large` before any copy or project creation. Tested in Task 3.
4. **Language toggle in the shell must re-render feature text immediately.** Each feature still has its own dictionary, but all share one locale. Tested in Task 5.
5. **Sidebar collapse state with storage blocked.** It falls back to expanded and must not throw. Tested in Task 10.

---

### Task 1: Session project list endpoint

**Files:**
- Modify: `services/api/oc_api/routes/projects.py`
- Test: `services/api/tests/test_projects.py`

**Interfaces:**
- Produces: `GET /api/v1/projects?limit=50` → `{"projects": [{"id", "app_slug", "title", "created_at", "updated_at"}]}`.
  - Scope: the current session's non-deleted projects.
  - Order: newest `updated_at` first.
  - `updated_at` = `created_at` of the project's `current_revision` row.
  - `limit` is 1..100, default 50.

- [ ] **Step 1: Write the failing tests**

```python
def test_list_projects_is_session_scoped_and_newest_first(app, session_client):
    a = session_client.post("/api/v1/projects", json={"app_slug": "camera-sketch", "title": "A"}).json()
    b = session_client.post("/api/v1/projects", json={"app_slug": "pocket-vfx", "title": "B"}).json()
    other = new_client(app); other.post("/api/v1/sessions")
    other.post("/api/v1/projects", json={"app_slug": "product-lock", "title": "X"})
    r = session_client.get("/api/v1/projects")
    assert r.status_code == 200
    rows = r.json()["projects"]
    assert [p["id"] for p in rows] == [b["id"], a["id"]]
    assert rows[0]["app_slug"] == "pocket-vfx" and set(rows[0]) == {"id", "app_slug", "title", "created_at", "updated_at"}

def test_list_projects_orders_by_last_revision_and_hides_deleted(app, session_client):
    a = session_client.post("/api/v1/projects", json={"app_slug": "camera-sketch", "title": "A"}).json()
    b = session_client.post("/api/v1/projects", json={"app_slug": "camera-sketch", "title": "B"}).json()
    session_client.patch(f"/api/v1/projects/{a['id']}", json={"expected_revision": 1, "config": a["config"]})
    assert [p["id"] for p in session_client.get("/api/v1/projects").json()["projects"]] == [a["id"], b["id"]]
    session_client.delete(f"/api/v1/projects/{b['id']}")
    assert [p["id"] for p in session_client.get("/api/v1/projects").json()["projects"]] == [a["id"]]
```

- [ ] **Step 2:** Run `uv run pytest services/api/tests/test_projects.py -k list_projects -q`. Expected: FAIL (405 or 404).
- [ ] **Step 3: Implement `list_projects(request, limit: int = Query(50, ge=1, le=100), db, session)` in `routes/projects.py`**
  - One query joining `ProjectRevision` on `(project.id, project.current_revision)`.
  - Order by revision `created_at` desc, then `Project.created_at` desc.
- [ ] **Step 4:** Run the same command. Expected: PASS. Then run `uv run pytest services/api/tests/test_projects.py -q`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(api): list the session's projects for the studio sidebar`.

### Task 2: `Storage.copy`

**Files:**
- Modify: `services/api/oc_api/storage.py` (Protocol, `LocalStorage`, `S3Storage`)
- Test: `services/api/tests/test_storage.py`

**Interfaces:**
- Produces: `Storage.copy(src_key: str, dst_key: str, mime: str | None = None) -> None`.
  - Byte-identical copy.
  - Raises `FileNotFoundError` when `src_key` is missing.
  - Local: the same temp-file + rename path as `put_file` (`begin_write`/`commit_write`).
  - S3: `copy_object` with `MetadataDirective="REPLACE"` and `ContentType=mime` when given.

- [ ] **Step 1: Write the failing tests**
  - `test_local_copy_is_byte_identical_and_independent`: put `b"abc"`, copy, assert `get_bytes(dst) == b"abc"`, then `delete_prefix(src)` and assert dst still exists.
  - `test_local_copy_missing_source_raises`.
  - `test_s3_copy_calls_copy_object`: use the file's existing S3 stub/mocking pattern; assert Bucket, Key and CopySource.
- [ ] **Step 2:** Run `uv run pytest services/api/tests/test_storage.py -k copy -q`. Expected: FAIL.
- [ ] **Step 3: Implement `copy` on both backends and add it to the Protocol.**
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit `feat(api): server-side object copy for local and S3 storage`.

### Task 3: `POST /handoffs`

**Files:**
- Create: `services/api/oc_api/handoffs.py` (allowlist, mock helpers, provenance)
- Create: `services/api/oc_api/routes/handoffs.py`
- Modify: `services/api/oc_api/app.py` (register router)
- Test: `services/api/tests/test_handoffs.py`

**Interfaces:**
- Consumes:
  - `Storage.copy` (Task 2).
  - `owned_asset` (`routes/assets.py`), `object_key`, `upload_limit`, `default_app_config`.
  - `enqueue_internal_job(db, project, "asset.inspect", {"asset_id": ...})`.
  - `IdempotencyRecord`.
- Produces in `oc_api/handoffs.py`:
  - `HANDOFF_TARGETS: dict[tuple[str, str], tuple[str, str]]` — the allowlist from Global Constraints.
  - `asset_is_mock(db, asset: Asset) -> bool`. True if any of:
    - `asset.meta_json["mock"]` is set,
    - the asset's job (`asset.job_id`) used a profile with `PROFILES[...].is_mock`,
    - `project_upstream_mock(db, source project)` is true.
  - `project_upstream_mock(db, project) -> bool`. True if any asset of the project has `meta_json["handoff"]["mock"]` true. It reads the stored flag only.
  - `project_handoff(db, project) -> dict | None`. Returns the newest hand-off input's provenance `{"source_app", "source_project_id", "source_title", "source_exists": bool, "mock": bool}`. `source_exists` is true when the source project row exists and `deleted_at` is null.
- Produces the endpoint `POST /api/v1/handoffs`:
  - Body: `{"source_asset_id": uuid, "target_app": "pocket-vfx" | "camera-sketch"}`; optional header `Idempotency-Key`.
  - Response: 201 `{"project_id", "asset_id", "app_slug", "inspect_job_id"}`.
  - Errors:
    - 404: foreign or missing asset.
    - 409 `asset_not_ready`: source not `ready`.
    - 422 `handoff_not_allowed`: pair not in the allowlist, or `target_app` mismatch.
    - 413 `too_large`: `asset.bytes > upload_limit(target app, target role)`.
    - 409 `idempotency_conflict`: same key, different body.
- New asset fields:
  - `role` = target role, `state="uploaded"`, `mime`/`bytes`/`sha256` copied from the source.
  - `filename` = `f"{source app}-{source role}{ext}"`.
  - `source_asset_id` = source id.
  - `object_key=object_key(session.id, new_project.id, new_asset.id, f"{role}-original", mime)`.
  - `meta_json={"handoff": {"source_app", "source_project_id", "source_asset_id", "source_job_id", "source_sha256", "source_title", "mock": asset_is_mock(...)}}`.
- New project: `title = (f"{source title} → {target label}")[:120]`, with the default config of the target app.
- **Deviation from spec §5.2 step 7** (intended): the server does not patch the config. Camera Sketch (`sceneToLink`) and Pocket VFX (`clipToLink`) already link the newest ready input of their role on the client, exactly as after a normal upload. Do not add a server link.
- Idempotency: `endpoint="POST /handoffs"`, `request_hash` = sha256 of the canonical JSON body, `job_id` = the inspect job id. A replay loads that job and returns its project and its asset (`options_json["asset_id"]`).

- [ ] **Step 1: Write the failing tests** (`test_handoffs.py`). Build the source with `helpers.put_asset` plus a succeeded `Job` the way `test_shares_exports.finished_render` does; set `asset.job_id` to it.
  - `test_product_render_to_vfx_creates_project_and_inspect_job`:
    - 201, the new project has `app_slug == "pocket-vfx"`, and the asset is role `clip` with state `uploaded`.
    - `storage.get_bytes(new.object_key) == storage.get_bytes(src.object_key)`.
    - An `asset.inspect` job is queued with `options_json == {"asset_id": new_id}`.
    - `meta_json["handoff"]["source_app"] == "product-lock"`.
  - `test_product_cover_to_camera_scene`: role `scene`.
  - `test_camera_geometry_and_cinematic_to_vfx`: parametrized over both roles.
  - `test_disallowed_pairs_are_422`:
    - vfx `cover` → camera,
    - product-lock `render` → camera-sketch,
    - product-lock `product` (original) → vfx,
    - product-lock `cover` → product-lock.
  - `test_foreign_session_asset_is_404`.
  - `test_source_not_ready_is_409`.
  - `test_too_large_for_target_is_413_and_creates_nothing`: set the source `bytes` above Pocket VFX `CLIP_MAX_BYTES`; assert the project count is unchanged.
  - `test_idempotent_replay_returns_same_project_and_conflict_on_other_body`.
  - `test_handoff_marks_mock_from_source_profile`: source job `model_profile="mock_v1"`, so `meta.handoff.mock is True`.
- [ ] **Step 2:** Run `uv run pytest services/api/tests/test_handoffs.py -q`. Expected: FAIL (404 route).
- [ ] **Step 3: Implement `oc_api/handoffs.py` and `routes/handoffs.py`, and register the router in `create_app`.**
  - Check order: allowlist and size before creating anything.
  - Then create the project and revision, copy the object, add the asset, enqueue the inspect job, add the idempotency record, and commit once.
  - On `IntegrityError` from a concurrent same-key request: roll back, delete the copied object prefix, and replay.
- [ ] **Step 4:** Run the same command. Expected: PASS. Then run `uv run pytest services/api/tests -q`. Expected: PASS.
- [ ] **Step 5: Worker check.** Add `services/worker/tests/test_handoff_inspect.py::test_handed_off_clip_and_scene_inspect_ready`, using the existing worker test harness for `asset.inspect`:
  - Hand off a real fixture MP4 (`fixtures/pocket-vfx/stomp_landscape.mp4` bytes as a product-lock `render`).
  - Hand off a PNG `cover`.
  - Run inspect; both become `ready`. The clip gets its `clip_preview`/`clip_filmstrip`, and the scene is normalized like an upload.
  - Run `uv run pytest services/worker/tests/test_handoff_inspect.py -q`. Expected: PASS.
- [ ] **Step 6:** Commit `feat(api): hand off a finished result into a new project of another tool`.

### Task 4: Upstream mock and provenance in project, share meta and recipes

**Files:**
- Modify: `services/api/oc_api/routes/projects.py` (`project_out`)
- Modify: `services/api/oc_api/routes/shares.py` (`share_meta`)
- Modify: `services/worker/oc_worker/executors/export.py`, `camera_export.py`, `vfx_export.py`
- Modify: `packages/contracts/src/index.ts`
- Test: `services/api/tests/test_handoffs.py`, `services/worker/tests/test_handoff_inspect.py` (or the existing export test files)

**Interfaces:**
- Consumes: `project_upstream_mock`, `project_handoff` (Task 3).
- Produces:
  - `project_out` adds `"upstream_mock": bool` and `"handoff": project_handoff(...) | None`.
  - `share_meta` adds `"app_slug": project.app_slug`, and `mock` becomes `existing or project_upstream_mock(db, project)`.
  - Each recipe ZIP:
    - gets a `handoff` key (the input asset's `meta_json["handoff"]` or `null`) in the JSON document it already writes;
    - in its README, gets the mock line when `upstream_mock`, plus one line naming the source app and project id.
  - Contracts: `interface HandoffInfo { source_app: AppSlug; source_project_id: string; source_title: string; source_exists: boolean; mock: boolean }`. `ProjectOf` gains `upstream_mock: boolean; handoff: HandoffInfo | null`. Add `type AppSlug = "product-lock" | "camera-sketch" | "pocket-vfx"` if it does not exist. Add `interface ProjectSummary { id: string; app_slug: AppSlug; title: string; created_at: string; updated_at: string }`.

- [ ] **Step 1: Write the failing tests**
  - `test_share_meta_has_app_slug_and_upstream_mock`:
    - Hand off a mock product render to vfx.
    - Make the target `render_video` with a job whose `model_profile="open_weights_v1"` and no mock meta.
    - Share it; meta has `app_slug == "pocket-vfx"` and `mock is True`.
  - `test_project_out_handoff_survives_source_delete`:
    - After the hand-off, delete the source project.
    - `GET` the target: `handoff.source_exists is False`, `upstream_mock is True`.
  - Worker test: build a recipe for a vfx render whose project has a hand-off input. The recipe JSON has `handoff.source_app == "product-lock"` and the README contains "Mock".
- [ ] **Step 2:** Run `uv run pytest services/api/tests/test_handoffs.py services/worker/tests -k "upstream or handoff or recipe" -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - The worker reads the project's assets through the same session/db access that the export executors already use.
  - The mock flag in each recipe's stored asset meta also ORs in upstream mock.
- [ ] **Step 4:** Run `uv run pytest services -q`. Expected: PASS. Then run `pnpm --filter @oc/contracts typecheck` (or `pnpm -r typecheck`). Expected: clean.
- [ ] **Step 5:** Commit `feat: carry hand-off provenance and upstream Mock into projects, shares and recipes`.

### Task 5: Studio scaffold, shared locale, theme scopes

**Files:**
- Create in `apps/studio`:
  - `package.json`, `next.config.ts`, `tailwind.config.ts`, `postcss.config.*`, `tsconfig.json`, `vitest.config.ts`
  - `src/app/layout.tsx`, `src/app/providers.tsx`, `src/app/globals.css`, `src/app/page.tsx` (redirect to `/camera`), `src/app/icon.svg`
  - `src/shell/i18n/{en,ko,index}.ts(x)`
- Modify: `packages/web-core/src/i18n.tsx`, `packages/web-core/src/index.ts`
- Test: `packages/web-core/tests/i18n.test.tsx` (or `.ts`, matching the folder), `apps/studio/tests/shell/theme.test.ts`

**Interfaces:**
- Produces in `@oc/web-core`:
  - `LocaleProvider({children})`: owns the locale state, the `oc.locale` storage key and `document.documentElement.lang`. This is the logic that today lives inside `createI18n`'s provider.
  - `useLocale(): { locale: Locale; setLocale(l: Locale): void }`.
  - `createI18n(dicts)` keeps its return shape `{ I18nProvider, useI18n }`. Its `I18nProvider` now reads the locale from `LocaleProvider` when one is mounted, and falls back to its own state otherwise, so the old apps keep working until removed.
- Produces in the studio:
  - Theme classes `theme-camera`, `theme-product`, `theme-vfx` in `globals.css`. Each sets RGB-triplet CSS variables `--oc-app`, `--oc-panel`, `--oc-stage`, `--oc-ink`, `--oc-muted`, `--oc-border`, `--oc-accent`, `--oc-accent-strong`, `--oc-danger`, `--oc-on-accent`.
  - `tailwind.config.ts` maps each semantic colour to `rgb(var(--oc-x) / <alpha-value>)`. Alpha modifiers such as `bg-panel/90` and `ring-accent/30` are in use.
  - Values: Camera Sketch and Pocket VFX use their current hex values exactly (see their old `tailwind.config.ts`).
  - Product Lock dark values (the spec's "my call"):
    - surfaces: app `#121513`, panel `#1A1E1B`, border `#313833`;
    - text: ink `#EEF2EE`, muted `#A4ADA6`;
    - accent `#4CC38A`, accent-strong `#6FD3A2`, danger `#FF8A80`;
    - stage stays light `#E9E8E3`. The Product Lock `.checker` class keeps its light pattern.
  - Tool-only colours keep fixed values: `path`, `target`, `generated` from Camera Sketch; `effect`, `tracking`, `warning` from Pocket VFX. The `emerald`/`amber`/`red` overrides are identical in the two dark apps except amber 700–900; use CSS variables for those too.
  - The shell's own chrome (sidebar, top bar) uses `theme-camera` neutrals with the active tool's accent.
  - `gridTemplateColumns` keys clash (`shell` is 240/320 in Camera Sketch and 220/300 in Pocket VFX). Rename them to `camera-shell` and `vfx-shell` and update the usages when the features move.
  - `height: {topbar: "56px", timeline: "160px"}`, `borderRadius` (take the max of each key: panel 12px, card 10px, button 8px), `fontSize`, `fontFamily` and `transitionDuration` come from the old configs.

- [ ] **Step 1: Write the failing tests**
  - In `web-core`: render two `createI18n` providers (dicts A and B) under one `LocaleProvider`. Calling `setLocale("ko")` from a component in A changes the text rendered by B. This is Review Focus 4. Use the test setup web-core already has; if there is no DOM environment, test through a small pure store export instead and say so in the commit.
  - In the studio: `theme.test.ts` parses `globals.css` and asserts each of the three theme classes defines every `--oc-*` variable listed above.
- [ ] **Step 2:** Run `pnpm --filter @oc/web-core test` and `pnpm --filter @oc/studio test`. Expected: FAIL.
- [ ] **Step 3: Implement the scaffold.**
  - `package.json` takes the union of the three apps' dependencies and devDependencies, with the pins from Global Constraints. Scripts: `dev`/`start` on `-p 3000`, `build`, `test`, `typecheck`.
  - The layout keeps the Inter + Noto Sans KR fonts. Metadata title is "Open Creative Studio", the name from the shell i18n key `appName`.
  - Run `pnpm install` to update `pnpm-lock.yaml`.
- [ ] **Step 4:** Run `pnpm --filter @oc/web-core test`, `pnpm --filter @oc/studio test`, `pnpm -r typecheck` and `pnpm --filter @oc/studio build`. Expected: all pass.
- [ ] **Step 5:** Commit `feat(studio): scaffold the unified app with shared locale and per-tool theme scopes`.

### Task 6: Move Camera Sketch into the studio

**Files:**
- Move: `apps/camera-sketch/src/{api,components,flow,hooks,i18n,model,state,workers}` → `apps/studio/src/features/camera-sketch/`
- Move: `apps/camera-sketch/tests/*` → `apps/studio/tests/camera-sketch/`
- Move: `apps/camera-sketch/public/samples/*` → `apps/studio/public/samples/camera/`
- Create:
  - `apps/studio/src/app/camera/layout.tsx`: wraps children in `<div className="theme-camera ...">` and the feature's `I18nProvider`.
  - `apps/studio/src/app/camera/page.tsx`: start screen.
  - `apps/studio/src/app/camera/p/[id]/page.tsx`.
- Modify:
  - Internal links: `router.push(\`/p/${id}\`)` → `/camera/p/${id}`; `href="/"` → `/camera`.
  - Sample URLs → `/samples/camera/...`.
  - `grid-cols-shell` → `grid-cols-camera-shell`.

**Interfaces:**
- Consumes: Task 5 scaffold.
- Produces: `features/camera-sketch` keeps its internal relative imports. Exported pages: `StartScreen`, `ProjectView` (or whatever `app/page.tsx` / `p/[id]/page.tsx` render today). `ShareView` stays in the feature for Task 9.

- [ ] **Step 1:** `git mv` the files listed above (git history is preserved). Fix test import paths (`../src/...` → `../../src/features/camera-sketch/...`) and widen the vitest `include` to `tests/**/*.test.ts`.
- [ ] **Step 2:** Run `pnpm --filter @oc/studio test`. Expected: all Camera Sketch tests pass (347 today, including `parity.test.ts`). Same count as before the move.
- [ ] **Step 3:** Run `pnpm --filter @oc/studio typecheck` and `pnpm --filter @oc/studio build`. Expected: both clean. The depth-prep Web Worker is still emitted as its own chunk; check `.next` output for a separate worker chunk.
- [ ] **Step 4:** Delete `apps/camera-sketch` and remove it from the CI build list in `.github/workflows/ci.yml`. Run `pnpm install` and `pnpm -r test`. Expected: PASS.
- [ ] **Step 5:** Commit `refactor(studio): move Camera Sketch into the studio under /camera`.

### Task 7: Move Product Lock into the studio

Same shape as Task 6, with these differences:
- Target: `features/product-lock`, routes `/product` and `/product/p/[id]`, layout class `theme-product`, samples under `/samples/product/`.
- Product Lock's light `globals.css` rules move into `features/product-lock/product.css`, imported by its layout and scoped under `.theme-product`. Its `.checker` and scrollbar colours become dark-surface equivalents. The checker stays light because it sits on the stage.
- Check every Product Lock component for hard-coded light colours (grep `#` hex and `bg-white`, `text-black`) and map them to semantic tokens.
- Tests: 11 Product Lock tests pass after the move.
- Commit `refactor(studio): move Product Lock into the studio under /product with the dark shell theme`.

### Task 8: Move Pocket VFX into the studio

Same shape as Task 6, with these differences:
- Target: `features/pocket-vfx`, routes `/vfx` and `/vfx/p/[id]`, layout class `theme-vfx`, samples under `/samples/vfx/`.
- `grid-cols-shell` → `grid-cols-vfx-shell`. `TrimScreen`'s "upload another" link goes to `/vfx`.
- Tests: 108 Pocket VFX tests pass. After Tasks 6–8, `pnpm -r test` reports the same totals as before the move: web-core 32 + 347 + 11 + 108, plus the new tests.
- Commit `refactor(studio): move Pocket VFX into the studio under /vfx`.

### Task 9: Single public share page

**Files:**
- Create: `apps/studio/src/app/s/[token]/page.tsx`, `apps/studio/src/shell/SharePage.tsx`
- Modify: the three features' `ShareView.tsx` export only the inner view, taking `meta` as a prop if they currently fetch it themselves.
- Test: `apps/studio/tests/shell/sharePage.test.ts`

**Interfaces:**
- Consumes: share meta `app_slug` (Task 4).
- Produces: `pickShareView(meta: { app_slug?: AppSlug; role: string }): "camera" | "product" | "vfx"`.
  - Uses `app_slug` when present.
  - Fallback by role: `geometry_video`/`cinematic_video` → camera, `render_video` → vfx, else product.
  - `SharePage` fetches meta once and renders the chosen feature's view inside its theme class and `I18nProvider`. No sidebar.

- [ ] **Step 1:** Write `pickShareView` tests for every role and for `app_slug` winning over role (a vfx `cover` must pick vfx).
- [ ] **Step 2:** Run `pnpm --filter @oc/studio test -- sharePage`. Expected: FAIL.
- [ ] **Step 3:** Implement the page and the helper. The Mock badge stays in each view and reads `meta.mock`, which now includes upstream mock.
- [ ] **Step 4:** Run tests and build. Expected: PASS.
- [ ] **Step 5:** Commit `feat(studio): one public share page that renders the owning tool's view`.

### Task 10: Sidebar and "My projects"

**Files:**
- Create:
  - `apps/studio/src/shell/Sidebar.tsx`, `apps/studio/src/shell/tools.ts`, `apps/studio/src/shell/sidebarState.ts`
  - `apps/studio/src/app/(tools)/layout.tsx` (or put the shell into each tool layout; builder's choice). The shell wraps `/camera`, `/product` and `/vfx`, never `/s`.
- Modify: `packages/web-core/src/client.ts` (`listProjects`)
- Test: `apps/studio/tests/shell/sidebar.test.ts`

**Interfaces:**
- Produces:
  - `TOOLS: { id: "camera" | "product" | "vfx"; slug: AppSlug; href: string; labelKey; blurbKey; icon }[]` in the order Camera Sketch, Product Lock, Pocket VFX.
  - `toolForSlug(slug: AppSlug)`.
  - `projectHref(p: ProjectSummary): string` → `/{tool}/p/{id}`.
  - `ApiClient.listProjects(limit?: number): Promise<{ projects: ProjectSummary[] }>`.
  - `readCollapsed(storage: Pick<Storage, "getItem"> | null): boolean`: key `oc.sidebar.collapsed`; returns false on a missing value or a thrown error.
  - `writeCollapsed(storage, v: boolean): void`: swallows errors.
- Layout:
  - Expanded width 200px: icon + name + one-line blurb per tool, the active tool highlighted with its accent. Below that, the "My projects" list: tool icon + title, newest first, React Query key `["projects"]`, invalidated after create, hand-off and delete.
  - Collapsed: 56px, icons only, with tooltips.
  - Below the `md` breakpoint the sidebar is hidden. A top-bar menu button (Task 11) opens it as a drawer; Radix Dialog is already a dependency.
- Copy (en/ko): "Camera Sketch / 카메라 스케치" is not translated; product names stay English in both locales. Blurbs:
  - en: "Sketch a camera move on a still" / "Ad clips that keep your product exact" / "Tracked effects on a phone clip"
  - ko: "사진 위에 카메라 움직임 그리기" / "제품을 그대로 지키는 광고 클립" / "휴대폰 영상에 추적 효과 넣기"
  - Section label: "My projects / 내 프로젝트". Empty state: "No projects yet / 아직 프로젝트가 없습니다".

- [ ] **Step 1: Write the failing tests**
  - `readCollapsed` returns false for null storage, for a throwing `getItem`, and for a missing key; true for `"1"`. This is Review Focus 5.
  - `projectHref` for each slug.
  - `TOOLS` order and hrefs.
- [ ] **Step 2:** Run `pnpm --filter @oc/studio test -- sidebar`. Expected: FAIL.
- [ ] **Step 3:** Implement. Start screens that create projects (all three `StartScreen`s) invalidate `["projects"]` after `createProject`.
- [ ] **Step 4:** Run tests, typecheck and build. Expected: PASS.
- [ ] **Step 5:** Commit `feat(studio): labelled tool sidebar with the session's projects`.

### Task 11: Unified top bar

**Files:**
- Create: `apps/studio/src/shell/TopBar.tsx`, `apps/studio/src/shell/LanguageToggle.tsx`
- Modify: the three features' editor views use the shell `TopBar`. Delete the features' `TopBar.tsx` and `LanguageToggle.tsx`.
- Test: `apps/studio/tests/shell/topbar.test.ts` (pure helpers only)

**Interfaces:**
- Produces: `TopBar({ title?: string; saveState?: "saved" | "saving" | "error" | "conflict"; mock?: boolean; actions?: ReactNode; onMenu?: () => void })`.
  - Layout: the brand "Open Creative Studio" (links to `/camera`), then the title, then the save status, then the Mock badge, then `actions` (each feature passes undo/redo), then the language toggle. On mobile there is also the menu button.
  - Save-status strings live in the shell i18n (`saved`, `saving`, `saveFailed`, `conflict`, en/ko copied from the Camera Sketch dictionary).
  - `mock` is `profile.is_mock || project.upstream_mock`.
- [ ] **Step 1:** Write a test for `topBarMock(profileIsMock: boolean | undefined, upstreamMock: boolean | undefined): boolean`.
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3:** Implement and wire all three editors. Remove feature i18n keys that are now unused only if each feature's `i18n.test.ts` still passes (those tests check en/ko key parity).
- [ ] **Step 4:** Run `pnpm --filter @oc/studio test`, typecheck and build. Expected: PASS.
- [ ] **Step 5:** Commit `feat(studio): one top bar for all tools`.

### Task 12: Hand-off UI, provenance badge, Mock propagation, demo-chain hint

**Files:**
- Create: `apps/studio/src/shell/handoff.ts`, `apps/studio/src/shell/NextSteps.tsx`, `apps/studio/src/shell/ProvenanceBadge.tsx`
- Modify:
  - `packages/web-core/src/client.ts`: `handoff`.
  - Product Lock `ResultView`: NextSteps for the selected render.
  - Camera Sketch `ResultView`: NextSteps for the geometry or cinematic video.
  - Camera Sketch and Pocket VFX editors: ProvenanceBadge next to the scene / clip.
  - Each feature's result Mock badge ORs `project.upstream_mock`.
  - Camera Sketch `StartScreen`: demo-chain hint.
- Test: `apps/studio/tests/shell/handoff.test.ts`

**Interfaces:**
- Consumes: `POST /handoffs` (Task 3), `project.handoff` / `project.upstream_mock` (Task 4), `TOOLS`/`projectHref` (Task 10).
- Produces:
  - `ApiClient.handoff(sourceAssetId: string, targetApp: AppSlug, idempotencyKey: string): Promise<{ project_id: string; asset_id: string; app_slug: AppSlug; inspect_job_id: string }>`.
  - `handoffTargets(app: AppSlug, role: string): AppSlug[]`. Mirrors the server allowlist; returns `[]` for anything else.
  - `NextSteps({ app, assets: {id, role}[] })`: one button per allowed (asset, target).
    - Each button holds a stable `crypto.randomUUID()` key in a `useRef` per (asset id, target). This is Review Focus 2.
    - While the request runs the button is disabled and shows progress.
    - On success: invalidate `["projects"]` and `router.push(projectHref(...))`.
    - On error: `errorText(e, t)`.
  - `ProvenanceBadge({ handoff })`: shows "From {Tool} result" and links to the source project when `source_exists`, plain text otherwise.
- Copy:
  - Buttons: "Add an effect in Pocket VFX / Pocket VFX에서 효과 넣기"; "Move the camera in Camera Sketch / Camera Sketch에서 카메라 움직이기".
  - Section: "Next step / 다음 단계".
  - Badge: "From {tool} result / {tool} 결과에서 가져옴".
  - Hint: "Try all three: start in Product Lock, move its cover in Camera Sketch, then add an effect in Pocket VFX. / 세 기능 이어 쓰기: Product Lock에서 시작해 커버 이미지를 Camera Sketch로 움직이고 Pocket VFX로 효과를 넣어 보세요." with a link to `/product`.
- [ ] **Step 1:** Write tests for `handoffTargets`:
  - every allowed pair;
  - product `product`, vfx `render_video`, vfx `cover` and camera `scene` all give `[]`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement and wire.
- [ ] **Step 4:** Run `pnpm -r test`, `pnpm -r typecheck` and `pnpm --filter @oc/studio build`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(studio): hand results to the next tool with provenance and Mock carried over`.

### Task 13: Infra, CI, docs cleanup

**Files:**
- Modify:
  - `infra/docker-compose.yml`: replace `web`, `web-camera`, `web-vfx` with one `web` built with `APP=studio`, `PORT=3000`, on port 3000.
  - `infra/Dockerfile.web`: `ARG APP=studio`, `ARG PORT=3000`, and update the comments.
  - `.env.example`, compose `MINIO_API_CORS_ALLOW_ORIGIN`: `http://localhost:3000`; the variable stays configurable.
  - `.github/workflows/ci.yml`: the web job builds `@oc/studio` only.
  - `README.md`: rewritten for the studio. Sections: what it is, the three tools, hand-offs, Mock vs open weights, run locally, tests, models and licences, known limits. Known limits are merged from the three current sections.
- Delete: `scripts/export_app.sh`, any leftover `apps/{product-lock,camera-sketch,pocket-vfx}`.
- [ ] **Step 1:** Run `docker compose -f infra/docker-compose.yml config`. Expected: valid, with one web service.
- [ ] **Step 2:** Run `docker build -f infra/Dockerfile.web .` (default args). Expected: the image builds.
- [ ] **Step 3:** Run `rg -n "3001|3002|web-camera|web-vfx|export_app|apps/(product-lock|camera-sketch|pocket-vfx)" --glob '!docs/superpowers/**' --glob '!docs/*-verification.md' --glob '!pnpm-lock.yaml'`. Expected: no hits that still need changing.
- [ ] **Step 4:** Run `uv run pytest -q`, `pnpm -r typecheck` and `pnpm -r test`. Expected: PASS.
- [ ] **Step 5:** Commit `chore: one studio web service; drop per-app images and export script; README for the studio`.

### Task 14: Full-stack verification

**Files:**
- Create: `docs/studio-verification.md`

- [ ] **Step 1: Default (local storage) walk-through.**
  - Start the stack: `docker compose -f infra/docker-compose.yml up --build -d`. Use `.env` from `.env.example` with a random `OC_SIGNING_SECRET`; do not commit it.
  - Walk each tool on Mock with Playwright, using the steps of the "Full stack walk-through (2026-10-25)" sections in `docs/camera-sketch-verification.md` and `docs/pocket-vfx-verification.md`. Product Lock: upload → mask → background → Render ad → MP4/Cover/Recipe → share with the Mock badge.
- [ ] **Step 2: Demo chain.**
  - Product Lock render → "Move the camera in Camera Sketch" (cover) → Analyze → Render Geometry → "Add an effect in Pocket VFX" → Prepare clip → subject, anchor, Track → Quick render → share.
  - Pass if:
    - each target opens with its input already linked;
    - provenance badges are shown;
    - the final result and its `/s/` page show Mock;
    - "My projects" lists all three projects;
    - the recipe ZIP has the `handoff` block.
- [ ] **Step 3: Shell checks.**
  - Sidebar navigation between the tools.
  - Collapse, then reload: still collapsed.
  - A 390px-wide viewport opens the drawer from the menu button.
  - The language toggle switches the shell and feature text together.
  - No console errors and no failed network requests.
- [ ] **Step 4: S3 run.** Repeat Step 2 with `--profile s3` (MinIO).
- [ ] **Step 5: Clean up and record.**
  - Stop only the containers you started: `docker compose ... down`.
  - Write `docs/studio-verification.md`: pass/fail per step, commands run, screenshots kept in the session scratchpad (not committed).
  - Commit `docs: studio full-stack verification`.
