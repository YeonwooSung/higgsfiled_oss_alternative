# Camera Sketch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Camera Sketch P0 — upload/sample a still photo, analyze depth, click a target, sketch a framing path or pick a preset, see an instant Three.js 2.5D preview with a Top/Side camera gizmo, render a deterministic Geometry video + RGB/depth/validity guides on the server, optionally generate an LTX-conditioned Cinematic video, compare them in sync and export MP4/path JSON/guides/recipe.

**Architecture:** A pure NumPy/OpenCV camera core (`open_creative.camera_sketch`, no torch/Comfy) owns the camera convention, depth→scene, the sketch→path compiler and the z-buffer guide renderer; it is the reference for everything and is wrapped by thin `CS*` Comfy nodes. The FastAPI service gets an app-module registry so `camera-sketch` plugs in next to `product-lock` (config schema, job kinds, compile endpoint). Depth and Cinematic run through the existing `WorkflowRunner` (mock profile now; `open_weights_v1` once real graphs are exported); guide rendering runs in the CPU worker. `apps/camera-sketch` is a new Next.js app whose TS camera math is pinned to the Python core by a shared parity fixture.

**Tech Stack:** Python 3.12, NumPy, OpenCV-headless, Pillow, FFmpeg, FastAPI, Pydantic v2, SQLAlchemy 2; Next.js App Router + React 19 + TS, Tailwind, Radix, Lucide, Zustand, TanStack Query, Three.js (plain, no react-three-fiber), react-konva (sketch overlay); vitest; pytest; uv + pnpm workspaces.

**Spec:** `docs/02_CAMERA_SKETCH_SPEC.md` (app), `docs/00_SHARED_IMPLEMENTATION_SPEC.md` (shared). Existing reference: `docs/superpowers/plans/2026-10-19-product-lock.md`.

**Branch:** `feat/camera-sketch` from `main` (Product Lock is merged on `main`).

## Global Constraints

- Everything in the Product Lock plan's shared constraints still holds (error envelope, UUIDs, upload/read URL TTL, idempotency, quotas, job states, lease, cost units, Mock label, no user-built paths/commands/filters, `lib/` is git-ignored).
- Output: 1280×720 (16:9) or 720×1280 (9:16), H.264 yuv420p, 24 fps, 5 s = **120 output frames**. Model frame/stride padding (LTX 121 frames, multiples of 32) is applied only inside the cinematic stage and cropped/trimmed back.
- Coordinates: OpenCV camera (x right, y down, z forward). Poses stored as world-to-camera `T_cw` (4×4). World frame = source camera at t=0. `T_wc` only as an explicit inverse. The OpenCV→Three.js conversion lives in exactly one TS function.
- Depth model: **Depth Anything V2 Small** only (never swap to Large). Its output is relative **inverse** depth, not meters. `z = scale / max(d + offset, eps)`; target-patch median z normalized to **1 scene unit**.
- Camera bounds: lateral −0.05…0.05 scene units; push 0…0.10 of target depth; small_arc ≤ ±6° around the target; focal length 35…70 mm-equivalent (synthetic, sensor width 36 mm assumed), fixed per shot; roll fixed 0. Scroll wheel zooms the stage only.
- Sketch: ≤ 128 raw points → Ramer–Douglas–Peucker → 2…5 control points; drawing direction = time order. Sketch space is `target_screen_position`, interpreted **relative** to the target: `desired(t) = target_uv + (sketch(t) − sketch(0))`.
- Guardrails (product policy, tunable on fixtures): max frame hole ratio > **8%** or target visibility < **90%** → warning. Clamp is only proposed (before/after overlay + "Apply reduced motion"); input is never silently changed.
- Small holes ≤ **4 px** may be deterministically inpainted; larger holes stay invalid in the validity mask.
- Depth guide uses one fixed near/far range for the whole shot (no per-frame min-max).
- Kinds allowlist (camera-sketch): `camera.analyze`, `camera.guide`, `camera.cinematic`, `camera.export` (+ internal `asset.inspect`).
- Cinematic results are never labelled exact camera control. Badges: Geometry = "Path render / 경로 렌더", Cinematic = "AI generated / AI 생성". Generated motion QA that cannot track → `unknown`, never 0 or pass.
- UI tokens: app bg #101216, panel #191D24, stage #0A0C10, text #F0F3F8, muted #A3ACB9, border #303845, camera path #66DCF2, target #F0BA65, generated accent #B89BFF; panel radius 10 px, controls 6 px. Layout: left 240, inspector 320, Top/Side view 220×160, timeline 140 px. Inter + Noto Sans KR, en default + ko.
- Performance targets: browser preview 30 fps (low mesh resolution), path edit visible ≤ 100 ms, compile endpoint ≤ 2 s.

## Review Focus

1. **Target on an edge, sky or depth discontinuity** — target depth taken from the median of the valid patch; if the target is not in front of the camera or leaves the frame the compiler returns `invalid` with a reason, never NaN poses or a GPU submission. (Task 4, Task 8)
2. **Portrait source → landscape output (or vice versa)** — the source-to-output cover-fit transform must be applied identically in Python and TS; identity pose renders the cover-fit crop of the source. (Task 2; parity fixture in Task 13 covers 9:16 and 16:9)
3. **Degenerate sketches** — a single click (→ static path), > 128 raw points (→ uniform downsample before RDP), a closed loop or a > 120° backtrack (→ `unsupported_sketch` + "Restore preset"). (Task 4, Task 15)
4. **Scene replaced after analyze/guide** — stale depth rejected on PATCH (lineage check) and a cinematic job whose revision's geometry differs from its guide job's → 422 `guide_stale`; guide frame count ≠ 120 → 422 `frame_count_mismatch`. (Task 7, Task 8)
5. **Depth adapter misreading the convention / flat depth** — a fixture where near is brighter in inverse depth must come out with smaller z; a constant depth map must not divide by zero and must yield a valid planar scene. (Task 3)

---

## File Structure

```
nodes/open_creative/open_creative/camera_sketch/
  camera.py       intrinsics, cover-fit transform, projection, look-at/framing rotation, pose interpolation
  scene.py        depth convention adapter, clip/smooth/normalize, mesh with discontinuity cuts
  path.py         sketch simplification, presets, framing least squares, compile_path, diagnostics
  render.py       z-buffer rasterizer, small-hole inpaint, frame sequence, hole ratio, target visibility
  guides.py       depth guide mapping, model-shape pad/crop, guide manifest
  qa.py           generated motion report (feature tracking)
  presets.v1.json mirror of presets/camera-sketch/presets.v1.json
nodes/open_creative/open_creative/comfy_nodes.py      + CS* wrappers
nodes/open_creative/open_creative/comfy/{bindings,profiles}.py  + camera workflows, per-app scope
services/api/oc_api/apps/                             app module registry (product_lock, camera_sketch)
services/api/oc_api/camera_sketch/{config,jobs,routes}.py
services/worker/oc_worker/executors/camera_{analyze,guide,cinematic}.py, export.py (per app)
services/worker/oc_worker/comfy/mock.py               + mock depth, mock cinematic
presets/camera-sketch/presets.v1.json
models/camera-sketch.manifest.json
workflows/camera-sketch/README.md                     (+ graphs/binding manifests when exported)
fixtures/camera-sketch/make_fixtures.py               synthetic scenes + GT depth + parity.v1.json
packages/web-core/                                    ApiClient, job watcher, autosave, history, i18n base
apps/camera-sketch/                                   Next.js editor
```

---

## Phase P0-A — deterministic core and parity

### Task 1: App-module registry (refactor, no behavior change)

**Files:** Create `services/api/oc_api/apps/__init__.py`, `apps/product_lock.py`; Modify `apps_registry.py`, `routes/jobs.py`, `routes/projects.py`, `routes/assets.py`, `routes/exports.py`, `open_creative/comfy/profiles.py`, `open_creative/comfy/bindings.py`; Test `services/api/tests/test_app_registry.py`

**Interfaces — Produces:**
- `class AppModule(Protocol)`: `slug: str`, `config_model: type[BaseModel]`, `default_config(profile) -> BaseModel`, `kinds: tuple[str,...]`, `plan_job(db, storage, project, cfg, kind, options, *, validate_assets=True) -> JobPlan`, `default_estimate_options(kind, cfg) -> dict`, `capabilities_extra() -> dict`, `asset_ref_fields: tuple[str,...]` (dotted paths into config), `upload_roles: dict[str, tuple[str,...]]`, `render_kinds: frozenset[str]` (per-project active-render quota), `export_formats: dict[str, tuple[str,...]]` (source kind → formats), `workflows: tuple[str,...]` (bindings needed by GPU profiles).
- `get_app(slug) -> AppModule`; `APPS: dict[str, AppModule]`. `JobPlan` moves to `oc_api/apps/__init__.py`.
- `profile_status(profile_id, workflows_dir, comfy_base_url, workflows: tuple[str,...])` — checks only the given app's workflows; `REQUIRED_BINDINGS` becomes keyed by workflow name across apps (unchanged values for product lock). Workflow files resolved under `workflows/{app}/`.
- `ProjectCreate.app_slug: Literal["product-lock","camera-sketch"]`.

- [ ] Test: all existing `services/api` and `services/worker` tests pass unchanged; new test asserts `get_app("product-lock").kinds` equals the old tuple, `GET /capabilities?app_slug=nope` → 422, and that `open_weights_v1` availability for product-lock is not affected by missing camera workflow files.
- [ ] Move product-lock specifics behind `AppModule`; routes dispatch via `get_app(project.app_slug)`. Run `uv run pytest services` → PASS. Commit `refactor(api): per-app module registry`.

### Task 2: Camera convention core

**Files:** Create `open_creative/camera_sketch/__init__.py`, `camera.py`; Test `nodes/open_creative/tests/test_cs_camera.py`

**Produces:**
- `intrinsics(focal_mm: float, width: int, height: int, sensor_w_mm=36.0) -> ndarray 3x3` (`fx=fy=focal_mm/sensor_w_mm*width`, `cx=width/2`, `cy=height/2`).
- `@dataclass(frozen) CoverFit(scale, offset_x, offset_y, out_w, out_h)`; `cover_fit(src_w, src_h, out_w, out_h) -> CoverFit`; `output_intrinsics(K_src, fit) -> K_out` (scale then shift by crop).
- `project(K, T_cw, X: Nx3) -> (uv Nx2 pixels, z N)`; `to_uv01(px, w, h)`, `from_uv01(...)`.
- `framing_rotation(c_world, target_world, desired_px, K) -> R 3x3` — rotation (roll 0) whose camera ray through `desired_px` points at the target; closed form: yaw/pitch of the target direction minus yaw/pitch of `K⁻¹[u,v,1]`.
- `pose(R, c_world) -> T_cw`; `camera_center(T_cw) -> c_world`.
- `ease(s, kind: Literal["linear","ease_in_out"]) -> float` (ease_in_out = cubic smoothstep `3s²−2s³`); `slerp(qa, qb, s)`; `interpolate(keys: list[(t, T_cw)], times: ndarray, easing) -> list[T_cw]` — position eased per segment, orientation slerp; endpoints exact.

- [ ] Tests: identity pose projects `X = z·K⁻¹[u,v,1]` back to `(u,v)` (≤1e-9); camera moved +x makes a point at z=1 move left on screen; push (+z) enlarges a box's projected width; `framing_rotation` puts the target exactly at `desired_px` and has zero roll (camera x-axis ⟂ world y); `interpolate` at t=0/1 equals the endpoint poses; cover-fit of 720×1280 source into 1280×720 maps source center to output center.
- [ ] Red → green. Commit `feat(camera-sketch): camera convention core`.

### Task 3: Depth → scene

**Files:** Create `open_creative/camera_sketch/scene.py`, `fixtures/camera-sketch/make_fixtures.py`; Test `tests/test_cs_scene.py`

**Produces:**
- `@dataclass DepthConvention(kind: Literal["inverse_relative","depth_relative"], scale=1.0, offset=0.0, eps=1e-6)`; DA-V2 adapter constant `DAV2_SMALL = DepthConvention("inverse_relative")`.
- `depth_to_z(d: float32 HxW, conv) -> z` — inverse: `scale/max(d+offset,eps)`.
- `prepare_z(z, target_uv01, clip_pct=(1,99), smooth=True) -> (z_norm, target_z_raw)` — percentile clip, edge-aware smoothing (`cv2.bilateralFilter` on log z), divide by median z of the target patch (radius 2% of short side, finite values only) so target z = 1.0.
- `@dataclass Mesh(vertices Nx3 float32, uv Nx2 float32, faces Mx3 int32, grid_shape)`; `build_mesh(z_norm, K, stride: int, edge_rel_threshold=0.08) -> Mesh` — grid vertices `z·K⁻¹[u,v,1]`, two triangles per cell, drop a triangle if `max(z)/min(z) − 1 > threshold` over its vertices.
- `encode_depth_png16(d) -> (bytes, meta{min,max})`, `decode_depth_png16(bytes, meta) -> d`.
- `make_fixtures.py` writes 3 synthetic scenes (indoor room + chair box, product on table, building facade) as `*.png` + GT inverse-depth `*.depth.png` (16-bit) + `scenes.json`, and `parity.v1.json` (Task 13); the three PNGs are also copied to `apps/camera-sketch/public/samples/`.

- [ ] Tests: inverse-depth fixture where the box is brighter (larger d) gives smaller z on the box; constant d → finite z everywhere and a planar mesh (all faces kept); box-on-plane fixture cuts faces along the box boundary (no face spans both); target patch on NaN/sky region falls back to the nearest finite patch and never returns NaN; png16 round-trip error ≤ 1/65535 of range.
- [ ] Red → green. Commit.

### Task 4: Sketch → camera path compiler

**Files:** Create `open_creative/camera_sketch/path.py`, `presets/camera-sketch/presets.v1.json` (+ package mirror); Test `tests/test_cs_path.py`

**Produces:**
- Presets JSON: `push_in` (push 0…0.10, lateral 0), `slide` (lateral −0.05…0.05), `small_arc` (angle −6…6°), each with `bounds`, `default`, `label_en`, `label_ko`; `SOLVER_VERSION = "cs_framing_ls_v1"`.
- `simplify_sketch(points: list[(t,u,v)]) -> (control: list[(t,u,v)], warning: str|None)` — uniform downsample to ≤128, RDP with ε doubling from 0.005 until ≤5 points, keep ≥2 (single point → `[p, p]` static); times renormalized to [0,1] by arc length; `warning="unsupported_sketch"` if the path closes (end within 0.02 of start with length > 0.1) or turns back > 120°.
- `solve_framing(desired: Nx2 uv01, lam_vel=0.01, lam_acc=0.1, lo=0.05, hi=0.95) -> Nx2` — minimizes `Σ‖p−d‖² + lam_vel Σ‖Δp‖² + lam_acc Σ‖Δ²p‖²` with `p₀` fixed to `d₀`, box-bounded by an active-set loop (solve normal equations, clamp violators, fix them, re-solve; ≤5 iterations). Same algorithm is ported to TS in Task 15.
- `preset_center(preset, params, s, target_world) -> c_world` — slide: `(lateral·s, 0, 0)`; push_in: `(0,0,push·s)`; small_arc: rotate the source camera about the vertical axis through the target by `angle·s`; all start at the origin.
- `compile_path(cfg: dict, scene: SceneInfo, out_w, out_h, n_frames=120) -> CompiledPath` where `SceneInfo(K_src, src_w, src_h, target_world, target_uv01)`; `CompiledPath` serializes to the §10 artifact: `schema_version, solver_version, coordinate_convention="opencv_T_cw", K, source_to_output, depth_mapping, keyframes[{t, T_cw, desired_uv}], samples[{t_ms, T_cw}], diagnostics, status: "ok"|"warning"|"invalid", reasons[]`, plus `suggested_params` when a reduced motion would pass (never applied).
- Diagnostics: `framing_rms_px`, `framing_rms_rel` (÷ output short side), `target_in_front_min_z`, `target_in_frame_fraction`, out-of-bound params → `invalid`.

- [ ] Tests: all keyframes equal → every sample equals identity pose (static is valid); slide with lateral +0.05 moves camera center +x and target framing RMS ≤ 1% of short side on the indoor fixture; push 0.10 ends at z=0.10; small_arc 6° keeps distance to target within 1e-6 and 7° → `invalid`; target near image edge with framing path pushing it out → `solve_framing` clamps to [0.05,0.95]; 200-point zig-zag → ≤5 control points; closed loop → `unsupported_sketch`; target behind camera → `invalid`, no NaN in output.
- [ ] Red → green. Commit.

### Task 5: Guide renderer

**Files:** Create `open_creative/camera_sketch/render.py`, `guides.py`; Test `tests/test_cs_render.py`, `tests/test_cs_guides.py`

**Produces:**
- `rasterize(mesh, colors: Nx3 uint8, K_out, T_cw, w, h, near=0.05, far=100.0) -> Frame(rgb uint8 HxWx3, z float32 HxW (inf=invalid), valid bool HxW)` — vectorized barycentric rasterization of triangle batches over per-triangle bbox windows, nearest-z wins (z-buffer), perspective-correct color interpolation; triangles crossing `near` dropped.
- `fill_small_holes(frame, max_px=4) -> (frame, filled_mask)` — holes whose connected component fits a 4-px-radius disk are inpainted (`cv2.inpaint` TELEA on RGB, nearest on z); larger stay invalid.
- `render_sequence(mesh, colors, compiled: CompiledPath, w, h) -> Iterator[Frame]` (streams; never holds 120 frames).
- `hole_ratio(frame) -> float`; `target_visibility(frame, target_world, K_out, T_cw, patch_points) -> float` (fraction of target patch points in frame, in front and passing the z-test with 2% tolerance).
- `DepthGuideRange(near, far)` from frame 0 valid z (1st/99th pct); `depth_guide(frame, rng) -> uint8 HxW` (inverse-depth mapped, near = white, invalid filled by nearest valid value; fill strategy name returned for the trace).
- `pad_to_model(frames, model_w, model_h, model_n) -> Iterator` (edge-pad, repeat last frame) and `crop_from_model(frames, out_w, out_h, n=120)`.

- [ ] Tests: identity pose on planar fixture reproduces the cover-fit source (mean abs error ≤ 2/255, hole ratio 0); static path → all 120 frames byte-identical; box-in-front-of-plane rendered from a +x camera: no plane pixel overwrites a box pixel where both project (back triangles never pop forward) and the newly exposed strip is `valid=False`; hole of 3 px gets filled, 10 px stays invalid; depth guide value at the same 3D point is equal in frames 0 and 119 (fixed range); `pad_to_model(120→121, 720→736)` then `crop_from_model` round-trips exactly. Timing check (marked `slow`): 120 frames at 1280×720 from the indoor fixture with mesh stride 2 under 90 s on CPU — record the number in the test output.
- [ ] Red → green. Commit.

### Task 6: Generated motion QA

**Files:** Create `open_creative/camera_sketch/qa.py`; Test `tests/test_cs_qa.py`

**Produces:** `motion_report(generated: Iterable[frame], geometry: Iterable[frame], target_px_out, compiled) -> dict(status: "ok"|"check"|"unknown", tracked_points:int, target_drift_px: float|None, similarity_mean: float|None, reasons[])` — `cv2.goodFeaturesToTrack` in a window around the target on frame 0, Lucas–Kanade through the sequence, compare target-window trajectory with the geometry render's; fewer than 8 trackable points → `unknown` with reason `low_texture`; large drift/low similarity → `check` ("subject change detected / review needed").

- [ ] Tests: geometry vs itself → `ok`; flat gray sequence → `unknown`, never 0 or ok; geometry vs a version where the target window is replaced by noise from frame 30 → `check`.
- [ ] Red → green. Commit.

## Phase P0-B/C — API, worker, Comfy contract

### Task 7: Camera Sketch config, presets and compile endpoint

**Files:** Create `services/api/oc_api/camera_sketch/{__init__,config,routes}.py`, `oc_api/apps/camera_sketch.py`; Modify `oc_api/app.py`, `routes/projects.py`; Test `services/api/tests/test_camera_config.py`, `test_camera_compile.py`

**Interfaces — Consumes:** `compile_path`, presets (Task 4); `AppModule` (Task 1).
**Produces:**
- `CameraSketchConfig` = §10 schema (`schema_version:1, scene_asset_id, depth_asset_id, target{u,v,depth_scale 0.5–2.0}, camera{focal_length_mm_equiv 35–70, sensor_width_mm_assumed: Literal[36], roll_deg: Literal[0]}, path{preset, sketch_space: Literal["target_screen_position"], sketch_points ≤5 (t,u,v ∈[0,1]), lateral_ratio −0.05–0.05, push_ratio 0–0.10, arc_deg −6–6, easing}, mode: "geometry"|"cinematic", generation{seed, control_preset low|medium|high, extra_prompt ≤300}, output{aspect, duration_ms: 5000, fps: 24}`) plus editor fields `scene{depth_contrast 0.5–1.5, edge_protection 0–1, show_holes}` and `model_profile`. `extra="forbid"`.
- PATCH lineage rule: `depth_asset_id` must be a `depth` asset whose `meta_json.source_asset_id == scene_asset_id`, else 422 `depth_stale`.
- `GET /api/v1/camera-sketch/presets` → presets with bounds.
- `POST /api/v1/projects/{id}/camera-path/compile` body `{revision, sketch: [{t,u,v}] (≤128 raw), preset, parameters}` → `{path: CompiledPath, control_points, warnings[], status}`; loads scene/depth of that revision, runs simplify+compile with a 2 s budget; if the hole-ratio estimate (160×90, 6 sampled frames) would exceed the budget it returns `hole_ratio: null, deferred: true` (the guide job computes it).
- Upload role `scene` (jpeg/png/webp); inspector treats `scene` like `product`.

- [ ] Tests: default config validates; lateral 0.06 → 422 field `config.path.lateral_ratio`; scene swap with old depth → 422 `depth_stale`; compile with 1-point sketch → static `ok`; compile with closed loop → `warnings` contains `unsupported_sketch`; compile on another session's project → 404.
- [ ] Red → green. Commit.

### Task 8: Camera job kinds and pre-submit validation

**Files:** Create `oc_api/camera_sketch/jobs.py`; Test `services/api/tests/test_camera_jobs.py`

**Produces:** option models and `plan_job` for:
- `camera.analyze` `{}` → stages `["depth"]`, GPU, requires ready `scene_asset_id`.
- `camera.guide` `{}` → stages `["compile","render","encode"]`, CPU; requires ready scene + fresh depth; runs `compile_path` on the pinned revision and returns 422 `path_invalid` (with reasons) when `status=="invalid"`.
- `camera.cinematic` `{guide_job_id}` → stages `["prepare","generate","postprocess","qa"]`, GPU; guide job must be same project, `camera.guide`, `succeeded`; `geometry_hash(cfg)` (sha256 of canonical scene_asset_id, depth_asset_id, target, camera, path, output) of this revision must equal the guide revision's → else 422 `guide_stale`; guide `result_json.frame_count` must equal `duration_ms*fps/1000` (120) → else 422 `frame_count_mismatch`.
- `camera.export` handled via `POST /projects/{id}/exports` with `export_formats = {"camera.guide": ("mp4","path_json","guides_zip","recipe_zip"), "camera.cinematic": ("mp4","path_json","recipe_zip")}`.
- `GPU_SECONDS_CAP` += `camera.analyze: 60`, `camera.cinematic: 600`, guide/export 0. `render_kinds = {"camera.guide","camera.cinematic"}`.

- [ ] Tests: each 422 above; cinematic with a valid guide → 202 with `input_revision` = request revision and options carrying `guide_job_id`; second concurrent guide on the same project → 429 `render_busy`; estimate for `camera.cinematic` returns `price_status: "unknown"`.
- [ ] Red → green. Commit.

### Task 9: Workflow contract, mock profile, manifests, CS nodes

**Files:** Modify `open_creative/comfy/bindings.py`, `comfy/profiles.py`, `services/worker/oc_worker/comfy/mock.py`, `open_creative/comfy_nodes.py`; Create `models/camera-sketch.manifest.json`, `workflows/camera-sketch/README.md`; Test `nodes/open_creative/tests/test_cs_nodes.py`, `services/worker/tests/test_mock_camera.py`

**Produces:**
- `REQUIRED_BINDINGS["camera_depth_v1"] = {"image":"image"}`, outputs `("depth",)` (16-bit inverse depth produced by `CSDepthToScene` inside the graph); `REQUIRED_BINDINGS["camera_cinematic_v1"] = {"guide_rgb":"image","guide_depth":"image","reference":"image","prompt":"string","seed":"int","width":"int","height":"int","frames":"int","control_strength":"float"}`, outputs `("video",)`. `camera_guide_v1` and `camera_compare_v1` are CPU-side (worker) in P0; their API graphs exist only for the published workflow ZIP.
- `CONTROL_PRESETS = {"low": None, "medium": None, "high": None}` in profiles — filled with actual strengths from the first real smoke; while `None`, `open_weights_v1` reports `available=False, reason="control presets not calibrated"` for camera-sketch.
- Mock depth: if the input sha256 matches a fixture in `fixtures/camera-sketch/scenes.json` return its GT inverse depth, else a deterministic heuristic (vertical gradient + blurred luminance); mock cinematic: geometry frames with holes `cv2.inpaint`-filled and a mild grade. Both labelled Mock in outputs' metadata.
- Comfy nodes `CSDepthToScene`, `CSCompileCameraPath`, `CSRenderGuides`, `CSPathDiagnostics`, `CSGeneratedMotionReport` in `NODE_CLASS_MAPPINGS`, each a thin call into Tasks 2–6; RGB `IMAGE` outputs and depth tensors kept in separate outputs/ranges.
- Model manifest: Depth Anything V2 Small (Apache-2.0), LTX-2.3 distilled + matching union IC-LoRA (same release; no LTX-2.0 camera LoRA), encoders; all `sha256: null, verified: false`.

- [ ] Tests: manifests for camera workflows validated by `validate_manifest` against a minimal test graph; mock depth on a fixture returns its GT exactly; node mappings import without torch present; `profile_status(open_weights_v1, …, camera workflows)` is unavailable with the calibration reason.
- [ ] Red → green. Commit.

### Task 10: Worker executors

**Files:** Create `services/worker/oc_worker/executors/camera_analyze.py`, `camera_guide.py`, `camera_cinematic.py`; Modify `executors/__init__.py`, `executors/export.py` (dispatch by app), `executors/inspect_asset.py`; Test `services/worker/tests/test_camera_executors.py`

**Produces:**
- `camera_analyze(run)`: runner `camera_depth_v1` → assets `depth` (png16, `meta_json{source_asset_id, convention, min, max, model:"depth-anything-v2-small"}`), `depth_preview` (8-bit PNG) ; result `{depth_asset_id, intrinsics_suggestion: K for 50 mm}`.
- `camera_guide(run)`: compile (store `path_artifact` JSON asset with both user config and compiled artifact), stream-render 120 frames → assets `geometry_video` (MP4), `guide_depth_video` (MP4, fixed range), `guide_validity` (ZIP of 1-bit PNGs), `guide_rgb` frames reused from geometry; result `{frame_count:120, hole_ratio_max, hole_ratio_mean, target_visibility_min, framing_rms_rel, warnings[], fill_strategy}`.
- `camera_cinematic(run)`: download guide assets, `pad_to_model` to `background_video_shape(aspect)` (1280×736/736×1280, 121), submit `camera_cinematic_v1` with `control_strength` from `CONTROL_PRESETS`, collect, `crop_from_model` → 120 frames, encode `cinematic_video`, run `motion_report` → result `{qa, guide_job_id, mode:"cinematic"}`; never overwrites geometry assets.
- Export (camera): `mp4` (selected job's video), `path_json`, `guides_zip` (depth MP4 + validity ZIP + manifest), `recipe_zip` (config, compiled path, workflow graphs/bindings, model manifest, input hashes, README stating Geometry = path render, Cinematic = AI generated without exact camera control, and Mock if applicable).

- [ ] Tests (mock profile, SQLite, temp storage): analyze → guide → cinematic → export happy path; guide on static path produces identical frames and `hole_ratio_max == 0`; cinematic output probes as 1280×720, 120 frames, 24 fps, h264/yuv420p; worker restart after cinematic submit inspects instead of resubmitting (reuse existing stage helpers); export recipe references the job's input revision.
- [ ] Red → green. Commit.

## Phase P0-B/E — web app

### Task 11: Extract shared web core

**Files:** Create `packages/web-core/{package.json,tsconfig.json,src/index.ts,src/client.ts,src/jobWatcher.ts,src/waitForJob.ts,src/autosave.ts,src/history.ts,src/i18n.tsx}`; Move tests from `apps/product-lock/tests/{client,jobWatcher,waitForJob,autosave,history}.test.ts`; Modify product-lock imports; Modify `packages/contracts/src/index.ts`

**Produces:** `createApiClient(appSlug: string, fetcher?)` with the generic methods of today's `ApiClient` (`capabilities()` uses `appSlug`); product-lock keeps a thin `api/client.ts` that adds its app-specific calls. Contracts gain `AppSlug`, `CameraSketchConfig`, `CompiledPath`, `CameraJobKind`, `CameraPreset`, `CompileResponse`, generic `Job.result` typing for camera results.

- [ ] `pnpm -r test && pnpm -r typecheck` green; `pnpm --filter @oc/product-lock build` succeeds. Commit `refactor(web): shared web-core package`.

### Task 12: App scaffold, start screen, project flow

**Files:** Create `apps/camera-sketch/` (Next config, Tailwind tokens from Global Constraints, `layout.tsx`, `page.tsx` start screen, `p/[id]/page.tsx`, `s/[token]/page.tsx`, `i18n/{en,ko}.ts`, `src/api/client.ts`, `public/samples/*`), port 3001; Test `apps/camera-sketch/tests/i18n.test.ts`

**Produces:** Start screen with Upload scene / Try sample (3 fixtures from Task 3) using the same upload path; after upload, "Analyze scene" submits only `camera.analyze` (no generation cost shown as spent); Mock badge when profile is mock; en/ko key parity test.

- [ ] Test en/ko keys equal; manual check `pnpm --filter @oc/camera-sketch dev`, upload sample → analyze → depth preview appears. Commit.

### Task 13: TS camera math with parity fixture

**Files:** Create `apps/camera-sketch/src/model/{camera.ts,path.ts,three.ts}`; Test `apps/camera-sketch/tests/{camera,path,parity}.test.ts`; Generate `fixtures/camera-sketch/parity.v1.json` (Task 3 script, using Tasks 2/4)

**Produces:** TS ports of `intrinsics`, `coverFit`, `outputIntrinsics`, `project`, `framingRotation`, `ease`, `slerp`, `interpolate`, `simplifySketch`, `solveFraming`, `presetCenter`, `compilePathProvisional` (same algorithms/constants as Tasks 2 & 4); `toThreeCamera(T_cw, K_out, w, h) -> {position, quaternion, fov, aspect}` — the single OpenCV→Three conversion (flip y and z axes).
Parity fixture: for 16:9 and 9:16 outputs (incl. portrait source → landscape output), slide/push_in/small_arc with 3 keyframes: K, keyframe `T_cw`, 10 sampled poses, target projections.

- [ ] Tests: every target projection within **2 px** of the fixture (output pixels); sampled poses within 1e-5; `simplifySketch` and `solveFraming` outputs equal Python's within 1e-6; projecting through a Three.js `PerspectiveCamera` built by `toThreeCamera` matches `project` within 0.5 px.
- [ ] Red → green. Commit.

### Task 14: Three.js preview stage and Top/Side gizmo

**Files:** Create `apps/camera-sketch/src/components/{PreviewStage,CameraGizmo,DepthMesh}.tsx`, `src/model/mesh.ts`; Test `tests/mesh.test.ts`

**Interfaces — Consumes:** `toThreeCamera`, `interpolate` (Task 13); depth PNG16 + meta from analyze result.
**Produces:** `buildPreviewMesh(depth: Float32Array, w, h, K, stride, edgeRelThreshold=0.08) -> {positions, uvs, index}` with the same discontinuity rule as `scene.build_mesh`; `PreviewStage` renders the textured mesh from the current `T_cw` at the timeline time, with hole visualization (red hatch over pixels no triangle covers) and a "Provisional" badge until the server compile response arrives; `CameraGizmo` draws top (x–z) and side (z–y) views 220×160 with source camera, keyframe cameras (numbered), path (cyan) and target (amber); labels "Camera path" vs on-image "Sketch".

- [ ] Tests: mesh face count equals Python's for the box fixture (parity fixture includes counts); dropped faces identical set on a 16×9 grid.
- [ ] Manual: preview holds ~30 fps on the indoor sample (stride chosen to keep ≤ 60k triangles); scrubbing updates within 100 ms. Commit.

### Task 15: Editor — tools, inspector, timeline, compile loop

**Files:** Create `src/state/editorStore.ts`, `src/components/{Editor,LeftPanel,Inspector,Timeline,SketchOverlay,TopBar}.tsx`, `src/model/sketch.ts`; Test `tests/{sketch,editorStore}.test.ts`

**Produces:**
- Tools Select target / Sketch framing / Move keyframe (Escape cancels). Target click sets `target.u/v` and suggests depth from the depth map median; amber reticle.
- `captureSketch(points) -> raw ≤128` (uniform downsample), then `simplifySketch`; on `unsupported_sketch` show the notice and "Restore preset" button. On sketch completion for `slide`, the UI proposes `lateral_ratio` sign opposite to the horizontal sketch direction and shows the change in the inspector (user confirms; not silent).
- Inspector sections per spec §4 (Target, Camera, Path, Scene, Render) with bounds from `/camera-sketch/presets`; focal note "synthetic setting, not EXIF".
- Timeline 0–5 s, numbered keyframe markers, easing linear/ease-in-out, Space plays.
- Compile loop: edits update the provisional TS path immediately; debounced 300 ms `POST camera-path/compile` after autosave (600 ms) produces the authoritative path; server result replaces provisional. Invalid → disabled Render buttons with reasons; warning with `suggested_params` → before/after overlay + "Apply reduced motion".
- Undo/redo 50 via `web-core` history; autosave; numeric inputs for every canvas action.
- Mobile (<768 px): preset + strength slider default, keyframe numbers in a bottom sheet, tabs Upload/Edit/Result.

- [ ] Tests: 1 point sketch → static; 300 raw points capped to 128 before simplify; store applies server compile result only if its revision is the latest (late response ignored); "Apply reduced motion" writes `suggested_params` as a new revision (undoable).
- [ ] Red → green; manual desktop + 390 px mobile check. Commit.

### Task 16: Render actions, results and export

**Files:** Create `src/components/{RenderBar,ResultView,SyncedPlayers,ExportMenu,ShareView}.tsx`; Test `tests/syncedPlayers.test.ts`

**Produces:** "Render Geometry" (`camera.guide`) and "Generate Cinematic" (`camera.cinematic` with the latest succeeded guide; estimate dialog first; explicit click only). Result tabs Source / Geometry / Depth guide / Cinematic; `SyncedPlayers` keeps all videos at the same timestamp (leader + drift correction > 1 frame); path overlay toggle (preview only, off in exports); header shows Geometry hole ratio and Cinematic QA status (`ok` / "Subject change detected — review" / `unknown` with reason); badges "Path render" (cyan) vs "AI generated" (#B89BFF); Previous-version marker when config changed; downloads MP4 / path JSON / guides ZIP / recipe ZIP; share link via existing shares API.

- [ ] Tests: sync helper seeks followers when drift > 1/24 s and not otherwise; QA `unknown` never renders as pass.
- [ ] Manual: sample → target → sketch → Geometry → Cinematic (mock) → compare → download all four. Commit.

### Task 17: Infra, README, verification and handoff

**Files:** Modify `infra/docker-compose.yml` (service `web-camera` on 3001 or `APP_SLUG` build arg), `infra/Dockerfile.web`, `scripts/export_app.sh` (works for `camera-sketch`), `README.md` (Camera Sketch section: modes, limits, mock vs open weights, model licenses), `.env.example`; Create `docs/camera-sketch-eval.md` (manual eval sheet: 6 photos × 3 presets × direction/subject/artifact, failures logged)

- [ ] `uv run pytest` and `pnpm -r test && pnpm -r typecheck` all green; `docker compose up` serves both apps; `scripts/export_app.sh camera-sketch /tmp/x` builds standalone.
- [ ] Run the spec §13 table on mock profile and record results in the PR body: coordinate fixture, static path, front/server ≤ 2 px, solver RMS ≤ 1%, occlusion, 120 frames/24 fps, exported revision link. Mark "real inference (depth guide → LTX ≥ 1 result)" as **pending Comfy deployment** — not done.
- [ ] Commit; finish with superpowers:finishing-a-development-branch.

---

## Out of scope / pending (do not build)

- Real `open_weights_v1` graphs for `camera_depth_v1` / `camera_cinematic_v1` and control preset calibration: need a Comfy deployment + GPU; export graphs from ComfyUI, add binding manifests, fill sha256s and `CONTROL_PRESETS`, then run the smoke (spec §13 "실추론"). If the 48 h LTX smoke fails, narrow Cinematic to the official small slide example (spec §14) — never widen scope.
- 360° orbit, multi-photo SfM, NeRF/3DGS, moving-person pose, lens change within a shot, long videos, free 6DoF.
