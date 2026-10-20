# Pocket VFX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pocket VFX P0 — upload/sample a 3–5 s phone clip, trim and normalize it to CFR 24 fps, pick an effect (Impact Wave, Energy Aura, Blink Out only with an empty-background frame), choose the peak moment and click the anchor/subject, track the point (LK + manual corrections) and propagate a subject mask (SAM 2.1 video), preview the effect locally, render a tracked composite over the untouched source (Quick = procedural + bundled plate, Generate = LTX effect plate + matte), and export MP4 with original audio/SFX, recipe and alpha layers.

**Architecture:** A pure NumPy/OpenCV core `open_creative.pocket_vfx` (no torch/Comfy) owns timing, tracking, mask utilities, procedural effects, plate matte, linear-light composite, ROI integrity, clean-plate check and audio planning; it is wrapped by thin `PV*` Comfy nodes. Video ingest (probe with rotation/VFR/audio, PTS-mapped CFR normalization, filmstrip, audio extract) is new **shared** code in `open_creative.media` because no app ingests video yet. The API gets a `pocket-vfx` app module (config, kinds, presets, validate); the worker gets `vfx.*` executors — analyze/track-LK/render/export on CPU, SAM masks (`vfx_masks_v1`) and LTX plate (`vfx_plate_v1`) through the existing `WorkflowRunner` (mock profile now). `apps/pocket-vfx` is a new Next.js app whose TS effect curves/frame mapping are pinned to Python by a parity fixture.

**Tech Stack:** Python 3.12, NumPy, OpenCV-headless 5, Pillow, FFmpeg 8 (libx264, aac), FastAPI, Pydantic v2, SQLAlchemy 2; Next.js App Router + React 19 + TS, Tailwind, Radix, Lucide, Zustand, TanStack Query, react-konva (overlay), Canvas 2D (proxy preview); vitest; pytest; uv + pnpm workspaces.

**Spec:** `docs/03_POCKET_VFX_SPEC.md` (app), `docs/00_SHARED_IMPLEMENTATION_SPEC.md` (shared). Reference plans: `docs/superpowers/plans/2026-10-19-{product-lock,camera-sketch}.md`.

**Branch:** `feat/pocket-vfx` from `main` (Product Lock and Camera Sketch are on `main`).

## Global Constraints

- Everything in the Product Lock / Camera Sketch shared constraints still holds (error envelope, UUIDs, URL TTLs, idempotency, quotas, job states, lease, cost units never mixed/never 0 when unknown, persistent "Mock" label, no user input builds paths/commands/FFmpeg filters, never name a dir `lib`).
- Video input: MP4/MOV/WebM (`video/mp4`, `video/quicktime`, `video/webm`), **≤ 100 MB**, exactly one video stream, ≤ 1 audio stream, source duration ≤ 60 s (product limit), decoded long side ≤ 4096. Codec support = what the server ffmpeg decodes (probe failure → 415 `unsupported_codec`).
- Trim: `duration_ms` 1000…5000, `source_start_ms + duration_ms ≤ source duration`. Timing origin = the normalized trimmed clip; `source_start_ms` is never applied twice.
- Frame mapping in exactly one function per language: `frame_index(t_ms) = floor(t_ms * 24 / 1000 + 0.5)`; `N = frame_index(duration_ms)`; last valid frame `N−1`. Timing: `0 ≤ start_ms ≤ peak_ms ≤ end_ms ≤ duration_ms`.
- Normalized work clip = rotation applied, CFR 24 fps (PTS-based, VFR resolved), center cover-fit to the output canvas 1280×720 (16:9) or 720×1280 (9:16); `aspect` defaults to the rotated source orientation. Changing trim or aspect invalidates analysis, track, masks. Proxy = same clip at 640 px long side (360×640 / 640×360) for masks, tracking and the browser.
- All editor coordinates are `[0,1]` of the work clip; proxy↔output via one explicit scale. Masks resized nearest (binary) or area/bilinear (soft) — never via the RGB path.
- Output: H.264 yuv420p MP4, 24 fps, work-clip size, N frames; AAC stream only when there is audio (original kept and/or SFX). A/V offset ≤ 1 output frame.
- Effect controls: `scale` 0.25–2.0, `intensity` 0–1, `palette` ∈ {violet, amber, cyan}, `rotation_deg` −180–180, `squash` 0.2–1.0 (Impact Wave ellipse), `layer_order` ∈ {behind_subject (default), in_front, split_glow}; SFX gain −30…0 dB (UI 0–100 % maps to it), `invalid_policy` hide (default) | freeze from preset.
- Tracking (proposed thresholds, tunable on fixtures): LK on proxy, failure if valid features < 4, forward/backward error > 3 proxy px, or point outside frame. Invalid frames are `valid=false` — never filled by copying the last coordinate. Confidence is a heuristic in [0,1], never shown as a probability/percent.
- Plate retime factor 0.5–2.0 only; outside → 422 `plate_length`. Texture loop only when loop-seam QA passes.
- Original preservation: outside the explicit effect ROI the pre-encode composite equals the source frame (max channel error ≤ 1/255). Blend in linear RGB with premultiplied alpha, convert to sRGB at the end.
- Kinds allowlist (pocket-vfx): `vfx.analyze`, `vfx.track`, `vfx.generate_plate`, `vfx.render`, `vfx.export` (+ internal `asset.inspect`). `render_kinds = {vfx.render}`. GPU: `vfx.track` (masks stage), `vfx.generate_plate`. `GPU_SECONDS_CAP`: track 120, generate_plate 600, others 0.
- Badges: render profile "Quick" (procedural + bundled plate) vs "Generated plate / 생성 plate"; never label a Quick render AI-generated. Tracking/mask warnings are shown on results.
- UI tokens: app #111113, panel #1C1C20, stage #080809, text #F6F5F2, muted #AAA8AE, accent CTA #FF9A52, effect overlay #DD8BFF, tracking #69D6A5, warning #F2C86B; radius panel 12 px, preset card 10 px. Layout: top 56, left 220, inspector 300, timeline 160 px. Inter + Noto Sans KR, en default + ko. Port 3002.
- Performance targets: proxy preview ≥ 15 fps once a plate exists; timing/scale edits reflected ≤ 100 ms; scale/intensity/timing edits never re-run track or plate stages.

## Review Focus

1. **Rotated portrait phone clip (display-matrix rotation 90/270) and VFR clip** — work clip is portrait, PTS map monotonic, frame count = N, and an anchor clicked on the proxy lands on the same scene point in the output render. (Task 1 fixture `rotated_portrait.mp4`, `vfr.mp4`; Task 9 end-to-end coordinate test)
2. **Tracking lost mid-clip (occluder crosses the anchor)** — lost frames are `valid=false`, the effect is hidden there (not frozen at the last position), the timeline shows a red interval, and one correction keyframe after the occlusion recovers the rest. (Task 7, Task 9)
3. **Stale results after edits** — render with a track job whose key (source sha + trim + aspect + anchor + prompts + corrections + mask edits + model revision) differs from the revision → 422 `track_stale`; changing only scale/intensity/timing reuses track and plate (no new stage rows). (Task 8, Task 12)
4. **Plate whose length/shape differs from the effect window** (e.g. 121 frames 1280×736 for a 0.4 s window, or retime > 2×) — retime within 0.5–2, else 422 `plate_length` before any GPU spend; plate shape cropped/cover-fit, never stretched. (Task 11, Task 12)
5. **Clip without audio, or with audio starting late (`start_time` > 0)** — no AAC stream unless SFX is enabled; with audio, A/V offset ≤ 1 frame after trim. (Task 1, Task 13)

---

## File Structure

```
nodes/open_creative/open_creative/media/video_ingest.py   probe_source, normalize_clip (PTS map), proxy, filmstrip, extract_audio
nodes/open_creative/open_creative/pocket_vfx/
  timeline.py       frame_index, timing validation, effect window
  presets.py        load presets (repo → packaged mirror)
  presets.v1.json   mirror of presets/pocket-vfx/presets.v1.json
  color.py          srgb<->linear LUTs, premultiplied over
  procedural.py     Impact Wave ring+dust, Energy Aura glow/particles, Blink particles (seeded)
  track.py          LK fwd/bwd point track, confidence, corrections, zero-phase smoothing, track key
  motion.py         background homography → camera stability diagnosis
  masks.py          mask seq manifest, resize, contour band, diagnostics, mask edits
  matte.py          plate → premultiplied RGBA (threshold+knee, temporal, despill), QA, retime, loop seam
  composite.py      layer assembly per frame, ROI, streaming
  integrity.py      outside-ROI check
  blink.py          clean-plate alignment check, dissolve
  audio.py          audio plan + fixed-argument ffmpeg mix/mux
nodes/open_creative/open_creative/comfy_nodes.py          + PV* wrappers
nodes/open_creative/open_creative/comfy/{bindings,profiles}.py  + vfx workflows
services/api/oc_api/apps/pocket_vfx.py; services/api/oc_api/pocket_vfx/{config,jobs,routes}.py
services/worker/oc_worker/executors/vfx_{common,analyze,track,plate,render,export}.py
services/worker/oc_worker/comfy/mock.py                   + mock masks, mock plate
presets/pocket-vfx/presets.v1.json, presets/pocket-vfx/{plates,sfx}/  bundled MIT plates + SFX
models/pocket-vfx.manifest.json; workflows/pocket-vfx/README.md
fixtures/pocket-vfx/make_fixtures.py                      synthetic clips + GT tracks/masks + parity.v1.json
apps/pocket-vfx/                                          Next.js editor (port 3002)
```

---

## Phase 0 — shared video ingest

### Task 1: Video ingest core and fixtures

**Files:** Create `open_creative/media/video_ingest.py`, `fixtures/pocket-vfx/make_fixtures.py`; Test `nodes/open_creative/tests/test_video_ingest.py`

**Produces:**
- `@dataclass SourceInfo(width, height, rotation: int (0/90/180/270), display_w, display_h, codec, duration_ms, start_ms, is_vfr: bool, frame_count, avg_fps: float, has_audio: bool, audio_codec: str|None, audio_start_ms: int|None)`; `probe_source(path) -> SourceInfo` — ffprobe JSON (`-show_streams -show_format`, video+audio), rotation from display-matrix side data or `rotate` tag, VFR = per-frame PTS deltas vary > 1 ms (`-show_entries frame=pts_time` on the video stream). Raises `MediaError` codes `unsupported_codec`, `multiple_video_streams`, `too_long` (> 60 s), `too_large_dims` (> 4096).
- `@dataclass PtsMap(source_pts_ms: list[float])` (index = work frame, value = source PTS chosen); `normalize_clip(src, out_path, info, source_start_ms, duration_ms, out_w, out_h) -> PtsMap` — decodes with ffmpeg autorotate, picks for each work frame `i` the source frame with the greatest PTS ≤ `start + i*1000/24` (nearest-previous; computed in Python from probed PTS, frames streamed via rgb24 pipe), cover-fits to `out_w×out_h`, writes via `Mp4Writer` (crf 16). Exactly `frame_index(duration_ms)` frames.
- `cover_transform(src_w, src_h, out_w, out_h) -> dict{scale, offset_x, offset_y}` (stored in `transform_json`); `proxy_size(out_w,out_h) -> (640,360)|(360,640)`; `make_proxy(work_path, proxy_path)` (scale filter with fixed numeric args).
- `filmstrip(work_path, out_png, n=12, thumb_h=90) -> list[int]` (frame indices) — one horizontal PNG; `first_frame_png(work_path, out_png)`.
- `extract_audio(src, out_m4a, info, source_start_ms, duration_ms) -> bool` — False (no file) without audio; AAC 48 kHz stereo; start offset accounts for `audio_start_ms − start_ms` (pad with silence via `adelay`/`apad` numeric args, then trim to exact duration).
- `make_fixtures.py` (seeded, MIT, procedural): `stomp_landscape.mp4` (1280×720, 5 s, textured ground + a body-shaped moving blob with a distinct "hand" disk; GT `tracks.json` hand trajectory per output frame; GT subject masks PNG zip), `occluded_hand.mp4` (a bar crosses the hand frames 50–62), `rotated_portrait.mp4` (1280×720 stream + 90° display matrix → portrait), `vfr.mp4` (mixed 30/15 fps timestamps), `with_audio.mp4` (440 Hz tone, audio starts at +200 ms), `clean_plate.png` (empty background of `stomp_landscape`), `manifest.json` (sha256 + GT); copies `stomp_landscape.mp4` and a portrait sample to `apps/pocket-vfx/public/samples/`.

- [ ] Tests: rotated fixture → `rotation==90`, work clip probes 720×1280; VFR fixture → `is_vfr`, PTS map monotonic non-decreasing, frame count 120 for 5000 ms; 3200 ms trim → 77 frames (`frame_index(3200)=77`); a frame-numbered fixture (frame index burned as a gray level) shows work frame `i` = source frame at `start + i/24` within one source frame; `extract_audio` on `with_audio.mp4` → duration within 1/24 s of trim and tone onset at 200 ms − start; silent clip → False; 2-video-stream file → `multiple_video_streams`.
- [ ] Red → green. Commit `feat(media): video ingest with rotation, VFR PTS map, proxy, filmstrip, audio`.

### Task 2: Video upload path (API + inspect)

**Files:** Modify `services/api/oc_api/routes/assets.py`, `oc_api/storage.py` (`MIME_EXT` + `video/quicktime`→`.mov`, `video/webm`→`.webm`, `audio/mp4`→`.m4a`), `services/worker/oc_worker/executors/inspect_asset.py`, `oc_api/apps/__init__.py` (optional `upload_limits: dict[role, int]`, default 20 MiB); Test `services/api/tests/test_video_upload.py`, `services/worker/tests/test_inspect_video.py`

**Produces:** per-role byte limit looked up via `getattr(app, "upload_limits", {})` in init/complete (413 message names the role's limit); 415 message lists the role's formats. Local PUT streams to disk instead of buffering in memory (`complete` hashes in chunks). Inspect branch for video roles (`clip`): `probe_source` → asset `meta_json{source: SourceInfo dict, original_sha256}`, `width/height` = display size, `duration_ms`, `frame_count`; plus a `clip_preview` asset (proxy-size MP4 of the whole source ≤ 60 s, for the trim screen) and `clip_filmstrip` PNG; `_MESSAGES` gains video copy ("Videos must be 100 MB or smaller.", "Use an MP4, MOV or WebM video.", "Clips must be 60 seconds or shorter.", "This video codec is not supported.").

- [ ] Tests: 100 MB+1 → 413 for `clip`, 20 MB+1 still 413 for image roles; `video/webm` accepted for `clip`, `image/png` → 415; inspect of `rotated_portrait.mp4` → ready, width 720 height 1280, preview + filmstrip assets with `source_asset_id`; corrupt bytes → rejected `unsupported_codec`. Existing suites pass unchanged.
- [ ] Red → green. Commit.

## Phase P0-A — fixed anchor + procedural ring + composite + timeline

### Task 3: Pocket VFX core: timeline, presets, color

**Files:** Create `open_creative/pocket_vfx/{__init__,timeline,presets,color}.py`, `presets/pocket-vfx/presets.v1.json` (+ packaged mirror, hatch artifact entry); Test `tests/test_pv_timeline.py`, `test_pv_color.py`; Modify `tests/test_packaging.py`

**Produces:**
- `FPS = 24`; `frame_index(t_ms: float) -> int`; `frame_count(duration_ms) -> int`; `@dataclass EffectWindow(start, peak, end)` (frame indices); `effect_window(effect: dict, duration_ms) -> EffectWindow`; `timing_errors(effect, duration_ms) -> list[dict{code, field}]` (codes `timing_order`, `timing_range`).
- Presets JSON: `impact_wave_v1`, `energy_aura_v1`, `blink_out_v1`, each `{id, version, label_en, label_ko, input_requirements{subject_mask: bool, clean_plate: bool, fixed_camera: bool}, plate_modes ["bundled","generated"], bundled_plates[{id, file, frames, loopable}], plate_prompt, plate_negative, plate_frames: 49, defaults{scale, intensity, palette, rotation_deg, squash, layer_order, durations_ms{pre_peak, post_peak}}, controls{name: [min,max]}, invalid_policy:"hide", sfx{file, peak_offset_ms}}`; `palettes{violet, amber, cyan: {core, mid, edge}}` in linear RGB. Prompt per spec §7 example for Energy Aura. `load_presets() -> dict[id, dict]` (repo → packaged fallback, same pattern as `camera_sketch/path.py`).
- `srgb_to_linear(u8) -> float32` / `linear_to_srgb(f) -> uint8` (LUT, exact round-trip for all 256 values); `over_premult(dst_lin, src_rgb_premult, src_a)`.

- [ ] Tests: `frame_index(1800)=43`, `frame_index(5000)=120`, `frame_index(20.8333)=1` (half-up), start>peak → `timing_order`; end > duration → `timing_range`; LUT round-trip identity for 0..255; packaged mirror byte-equal to repo presets.
- [ ] Red → green. Commit.

### Task 4: Procedural effects

**Files:** Create `open_creative/pocket_vfx/procedural.py`; Test `tests/test_pv_procedural.py`

**Produces:** `@dataclass EffectLayer(rgb_premult float32 HxWx3 linear, alpha float32 HxW, bbox (x0,y0,x1,y1)|None)`; `render_effect(preset_id, params: dict, window: EffectWindow, frame: int, anchor_px: (x,y)|None, out_w, out_h, seed: int, mask: ndarray|None) -> EffectLayer|None` (None = nothing visible this frame). Deterministic in `(seed, frame)` (`np.random.default_rng(seed*100003+frame)` only for per-frame jitter; particle births from `default_rng(seed)`).
- Impact Wave: before peak, a charge glow at the anchor ramping from `start` (`smoothstep`); at/after peak, ring radius `R = scale·0.35·short_side·(1−(1−s)³)`, `s=(f−peak)/max(end−peak,1)`, alpha `intensity·(1−s)^1.5`, ellipse with `squash`, `rotation_deg`, ring width 0.04·short_side·scale with soft falloff; dust particles (≤ 300) emitted on the ring front. First visible frame = `start`, maximum brightness at `peak`.
- Energy Aura: needs `mask` (contour band, Task 10) or `anchor_px` (local glow, radius 0.12·short_side·scale); flicker noise from seed; particles drift outward.
- Blink Out particles: emitted from mask boundary between peak and end.
- `effect_bbox` covers every nonzero alpha pixel (used for ROI).

- [ ] Tests: Impact frame < start → None; frame `peak` has the largest alpha sum over the window (±1 frame); radius monotone non-decreasing after peak; same seed → bit-identical, different seed → different dust; anchor at the frame corner renders without error (clipped bbox); Aura without mask and without anchor → `ValueError`.
- [ ] Red → green. Commit.

### Task 5: Composite and outside-ROI integrity

**Files:** Create `open_creative/pocket_vfx/{composite,integrity}.py`; Test `tests/test_pv_composite.py`

**Produces:**
- `@dataclass FrameInputs(source u8 HxWx3, effect: EffectLayer|None, plate: EffectLayer|None, subject_mask: float32 HxW|None, front_glow: EffectLayer|None, roi_limit: bool HxW|None)`.
- `composite_frame(inp, layer_order) -> (out u8, roi bool HxW)` — order Source → behind effect (+plate) masked by `(1−M)` when `behind_subject` → subject restored → optional front glow (`split_glow`: glow layer only inside ROI; `in_front`: no masking) ; linear blend, premultiplied; `roi = dilate(alpha_total > 1/1024, 2 px)`; pixels outside `roi` are copied from `source` bytes (never round-tripped).
- `composite_sequence(frames_iter, per_frame_fn, ...) -> Iterator[(out, roi)]` (bounded memory).
- `outside_roi_report(source, out, roi) -> dict{max_err, changed_outside_px}`; `IntegrityAccumulator` (`add`, `result() -> {status: pass|fail, max_err, worst_frame, frames}`); threshold max_err ≤ 1.
- `subject_core_cover(out, source, mask_core) -> float` — fraction of eroded-mask pixels changed by > 2/255 (Occlusion QA).

- [ ] Tests: no effect → output byte-equal source and empty ROI; behind_subject with a full-coverage ring and a subject mask → `subject_core_cover == 0` and pixels under the eroded mask unchanged; in_front → core is covered; random effect: `outside_roi_report.max_err == 0`; accumulator fails if a single pixel outside ROI changes by 2.
- [ ] Red → green. Commit.

### Task 6: App module, config, presets/validate endpoints, job kinds

**Files:** Create `services/api/oc_api/apps/pocket_vfx.py`, `oc_api/pocket_vfx/{__init__,config,jobs,routes}.py`; Modify `apps/__init__.py` (`_register`), `routes/projects.py` (Literal += "pocket-vfx"), `oc_api/app.py` (router tuple), `open_creative/comfy/profiles.py` (`GPU_SECONDS_CAP`, `VFX_WORKFLOWS`); Test `services/api/tests/test_vfx_config.py`, `test_vfx_jobs.py`

**Produces:**
- `PocketVfxConfig` (`extra="forbid"`) = spec §10 with: `video_asset_id`, `work_asset_id: UUID|None` (set by client from analyze result), `trim{source_start_ms ≥0, duration_ms 1000–5000}`, `effect{preset_id, start_ms, peak_ms, end_ms, scale, intensity, palette, rotation_deg, squash, layer_order}`, `anchor{frame ≥0, x, y ∈[0,1], follow, track_window_frames: int|None}`, `corrections[{frame,x,y}] ≤ 32`, `subject_prompts[{frame, points[{x,y,label 0|1}] ≤ 16}] ≤ 8`, `mask_edits[{frame, asset_id}] ≤ 16`, `plate{mode: bundled|generated, bundled_id: str|None, asset_id: UUID|None, seed, matte{threshold 0.01–0.3, knee 0.0–0.2}}`, `clean_plate_asset_id`, `audio{keep_original, sfx_enabled, sfx_gain_db −30…0}`, `output{aspect: "16:9"|"9:16", fps: Literal[24]}`, `render_profile: quick|generated`, `model_profile`.
- `check_config_assets`: `video_asset_id` role `clip`; `work_asset_id` role `work_clip` with `meta_json{source_asset_id, trim, aspect}` equal to config → else 422 `analysis_stale`; `mask_edits` assets role `mask_edit`; `clean_plate_asset_id` role `clean_plate`.
- Upload roles `clip` (3 video mimes, limit 100 MiB), `clean_plate` (jpeg/png/webp), `mask_edit` (png).
- `GET /api/v1/pocket-vfx/presets` → presets with controls/requirements, availability of `blink_out_v1` = `requires clean_plate` (shown, not hidden, with condition text).
- `validate(db, project, cfg) -> {valid, blocking_errors[{code,field,message}], warnings[]}` and `POST /api/v1/projects/{id}/vfx/validate {revision}`; checks: timing (Task 3), anchor frame < N, analysis fresh, track valid at peak (if a fresh track exists; `track_invalid_at_peak` warning otherwise blocking for `follow`), plate length (stub returning no error until Task 12 wires `retime_factor`), blink clean plate required.
- Kinds & `plan_job`: `vfx.analyze {}` stages `["normalize","proxy","filmstrip","audio","motion"]` CPU; `vfx.track {}` stages `["track","masks"]` GPU (masks stage only when `layer_order != in_front` or preset requires a mask); `vfx.generate_plate {seed?}` `["plate","matte"]` GPU; `vfx.render {track_job_id|null, render_profile}` `["layers","composite","encode"]` CPU; `vfx.export` export-only. `track_key(cfg, source_sha, model_rev) -> str` (sha256 of canonical JSON of the Review-Focus-3 fields). Render: `track_job_id` must be same project `vfx.track` succeeded with `result.track_key == track_key(cfg)` else 422 `track_stale`; required when `follow` or mask needed; any `validate` blocking error → 422 `validation_failed` with the list. `export_formats = {"vfx.render": ("mp4","cover_png","recipe_zip","layers_zip")}`.

- [ ] Tests: default config validates; peak < start → validate blocking `timing_order` and `vfx.render` → 422; trim change with old `work_asset_id` → PATCH 422 `analysis_stale`; render with track job from older anchor → 422 `track_stale`; render with only intensity changed since the track → 202; blink without clean plate → blocking `clean_plate_required`; second concurrent render → 429 `render_busy`; estimate of `vfx.generate_plate` → `price_status: "unknown"`; product-lock and camera-sketch capability tests unchanged.
- [ ] Red → green. Commit.

## Phase P0-B — point track and manual correction

### Task 7: Point tracker

**Files:** Create `open_creative/pocket_vfx/{track,motion}.py`; Test `tests/test_pv_track.py`, `test_pv_motion.py`

**Produces:**
- `@dataclass TrackPoint(frame, x, y (proxy px), valid, confidence, fb_error, n_features, source: "tracked"|"keyframe"|"static")`; `track_point(frames: Sequence[gray u8], anchor_frame, anchor_xy, *, patch_r=None (4% short side), max_corners=40, fb_max=3.0, min_features=4, frame_range=None) -> list[TrackPoint]` — forward and backward from the anchor; per step `goodFeaturesToTrack` in the patch around the current point (mask disk), `calcOpticalFlowPyrLK` (winSize 21, maxLevel 3) forward then back, keep features with fb error ≤ fb_max, translation = median of kept displacements; after the first invalid step the run stops (no further frames in that direction are valid until a keyframe).
- `track_with_corrections(frames, anchor, corrections, window) -> list[TrackPoint]` — keyframes = anchor ∪ corrections (sorted, unique frame; correction wins); segment between adjacent keyframes = forward track from left and backward from right, blended linearly by time where both valid, else the valid one; outside first/last keyframe single-direction; keyframe frames exact (`source="keyframe"`). `segment_keys(...)` (hash per segment so Task 8 can reuse unchanged segments).
- `smooth_track(points, sigma_frames=1.0) -> list[TrackPoint]` — zero-phase Gaussian applied per contiguous valid run, keyframes pinned.
- `static_track(anchor, n)` for `follow=false` (all valid, confidence 1, source static).
- `track_to_json(points, proxy_wh, out_wh) -> dict{schema_version:1, proxy_size, output_size, points[{frame,x,y (uv01),valid,confidence}], invalid_intervals[[a,b]], algorithm:"lk_fb_median_v1", params}`.
- `camera_motion(frames_gray, subject_masks|None) -> dict{status: "fixed"|"handheld"|"moving"|"unknown", median_shift_px, max_shift_px}` — ORB + RANSAC homography frame-0→frame-k on background (outside dilated mask); <12 inliers → `unknown`.

- [ ] Tests (fixtures from Task 1, scaled to proxy): `stomp_landscape` hand GT median error ≤ 5 **output** px over visible frames; `occluded_hand` frames 50–62 invalid (not copied), and adding one correction at frame 63 makes frames 63–119 valid with median error ≤ 5 px; correction frames exactly equal; smoothing never moves a keyframe and never bridges an invalid gap; static fixture → camera `fixed`, synthetic panned clip → `moving`.
- [ ] Red → green. Commit.

### Task 8: vfx.analyze, vfx.track (LK stage) and vfx.render (Quick) executors

**Files:** Create `services/worker/oc_worker/executors/vfx_{common,analyze,track,render}.py`; Modify `executors/__init__.py`; Test `services/worker/tests/test_vfx_executors.py`

**Interfaces — Consumes:** Tasks 1, 3–7. Reuse `camera_common` helpers (`api_errors`, `ready_asset`, `json_asset`, `revision_config`, `source_job`, `frame_checkpoint`, `with_heartbeat`) — move generic ones to `executors/common.py` if needed without behavior change.
**Produces:**
- `vfx_analyze(run)`: stages normalize → `work_clip` (MP4, `meta_json{source_asset_id, trim, aspect, pts_map, mock:false}`, `transform_json` cover transform), `work_proxy` (MP4), `filmstrip` + `first_frame` PNGs, `source_audio` (m4a, only if audio), motion report JSON. Result `{work_asset_id, proxy_asset_id, filmstrip_asset_id, frame_count, has_audio, camera_motion, filmstrip_frames}`.
- `vfx_track(run)` stage `track`: decode proxy gray frames once (bounded: ≤ 120 × 640×360 gray ≈ 28 MB OK), compute/reuse segments (load previous succeeded track job of same project whose `segment_keys` match), `smooth_track`, store `track_json` asset; result `{track_key, track_asset_id, invalid_intervals, valid_fraction, peak_valid}`. (Masks stage added in Task 10.)
- `vfx_render(run)`: stage `layers` resolves anchor per frame (track → output px; invalid → hidden per `invalid_policy`), plate (Task 12) and masks (Task 10) if present; stage `composite` streams work-clip frames → `composite_frame` → `Mp4Writer` + `IntegrityAccumulator`; stage `encode` probes, writes `cover` PNG (peak frame), `roi_qa` JSON; result `{render_profile, roi_qa, occlusion_qa|null, tracking_warnings, plate_source: "procedural"|"bundled"|"generated", frame_count, mock}`.

- [ ] Tests (mock profile, SQLite, temp storage): upload `stomp_landscape` → analyze → render Quick with `follow=false` → MP4 1280×720, 120 frames, h264/yuv420p, ROI QA pass, peak brightness frame within ±1 of `frame_index(peak_ms)`; rotated portrait → 720×1280 render; track with occluded fixture reports invalid interval and the render hides the effect there (frame 55 equals source outside nothing; ROI empty); re-tracking after one correction reuses unchanged segments (assert segment cache hit count); worker restart mid-render resumes from stage outputs.
- [ ] Red → green. Commit.

### Task 9: End-to-end coordinate test (rotation + proxy + output)

**Files:** Test `services/worker/tests/test_vfx_coordinates.py`

- [ ] Test: for `rotated_portrait.mp4` and `vfr.mp4`, a GT scene point (fixture manifest gives its display-space position per source PTS) clicked at proxy uv on anchor frame 43 tracks to within 5 output px of GT at frames 0, 60, 119 in the render's track, and the rendered effect centroid at peak sits within 5 px of the anchor. Commit `test(pocket-vfx): end-to-end coordinate mapping`.

## Phase P0-C — subject masks and occlusion

### Task 10: Masks — utilities, mock SAM, workflow contract, PV nodes, track masks stage, occlusion in render

**Files:** Create `open_creative/pocket_vfx/masks.py`, `models/pocket-vfx.manifest.json`, `workflows/pocket-vfx/README.md`; Modify `open_creative/comfy/{bindings,profiles}.py`, `services/worker/oc_worker/comfy/mock.py`, `open_creative/comfy_nodes.py`, `executors/vfx_track.py`, `executors/vfx_render.py`; Test `tests/test_pv_masks.py`, `test_pv_nodes.py`, `services/worker/tests/test_mock_vfx.py`

**Produces:**
- `MaskSequence` manifest `{schema_version, size (proxy), frames[{frame, file, area, holes, empty}], prompts_hash, model}` stored as ZIP of 8-bit PNGs (`masks_zip`) + `mask_preview` MP4 (proxy gray, for the browser overlay).
- `upscale_mask(m_proxy, out_w, out_h, soft=True) -> float32` (INTER_LINEAR on float then clip; binary path INTER_NEAREST); `contour_band(mask, inner_px, outer_px) -> float32` (dilate − erode, Gaussian feather, temporally smoothed by `smooth_masks(seq, radius=1)`); `mask_diagnostics(seq) -> list[{frame, code: "area_jump"|"mask_empty"|"holes"}]` (area change > 35 % frame-to-frame, empty when prompt expects subject, hole count > 3); `apply_mask_edits(seq, edits)` — an edited frame replaces that frame and is added as a prompt frame for re-propagation (`edits_as_prompts`).
- Workflow `vfx_masks_v1`: `REQUIRED_BINDINGS = {"frames":"video","prompts":"json","width":"int","height":"int"}`, outputs `("masks",)` (zip); `WORKFLOW_APP["vfx_masks_v1"]="pocket-vfx"`; same for `vfx_plate_v1` (Task 12). `kind_workflows = {"vfx.track": ("vfx_masks_v1",), "vfx.generate_plate": ("vfx_plate_v1",)}`. Open-weights profile stays unavailable until graphs + manifests exist (existing `profile_status` behavior).
- Mock masks: if the source sha256 matches `fixtures/pocket-vfx/manifest.json`, return GT masks (resampled to the work timeline via PTS map); else GrabCut on the anchor frame seeded by points (positive → FG, negative → BG, rect = bbox of positives padded 30 %), then each frame GrabCut initialized from the previous mask dilated 8 px. Output sidecar `{"mock": true}`.
- Comfy nodes `PVTrackAnchor`, `PVSubjectMasks` (real SAM 2.1 small **video** predictor: `build_sam2_video_predictor`, `init_state` per job — never shared, `add_new_points_or_box` at prompt frames, `propagate_in_video` forward then `reverse=True`; lazy import), `PVPlateMatte`, `PVProceduralEffect`, `PVTrackedComposite`, `PVOutsideROIIntegrity` — thin wrappers over Tasks 4–7/10/11; import without torch.
- `vfx_track` stage `masks` (GPU via `run.provider_stage(st, "vfx_masks_v1", bindings)`): masks key excludes corrections (correction-only changes reuse the previous masks stage output); stores `masks_zip`, `mask_preview`, diagnostics in result.
- Render: `behind_subject` and `split_glow` use `upscale_mask`; Energy Aura uses `contour_band` ∪ anchor glow ROI; result `occlusion_qa = subject_core_cover` max.
- Model manifest: SAM 2.1 hiera small (Apache-2.0), LTX-2.3 distilled v1.1 + Gemma text encoder per official example (community license) — `sha256: null, verified: false`.

- [ ] Tests: GT-mask fixture through mock → mask IoU ≥ 0.95 vs GT; portrait fixture mask aligns with GT (IoU ≥ 0.9) at output size (no drift/transpose); `area_jump` flagged on a synthetic sequence with a frame dropped to half area; correction-only re-track does not create a new masks provider submission; render behind_subject on `stomp_landscape` → `occlusion_qa == 0`, ROI QA pass; nodes import without torch; `profile_status(open_weights_v1, …, vfx workflows)` unavailable with missing-graph reason.
- [ ] Red → green. Commit.

## Phase P0-D — effect plate, matte, reuse

### Task 11: Plate matte, retime, loop QA

**Files:** Create `open_creative/pocket_vfx/matte.py`; Test `tests/test_pv_matte.py`

**Produces:** `matte_frame(rgb_u8, threshold=0.06, knee=0.04) -> (rgb_premult_lin, alpha)` — linear luminance Y, `alpha = smoothstep(threshold−knee, threshold+knee, Y)`, black-level subtraction (5th pct of low-alpha pixels) then despill (`rgb_premult = max(lin − black, 0)·alpha`-consistent); `matte_sequence(frames, cfg) -> Iterator` with zero-phase 3-tap temporal alpha smoothing; `matte_qa(stats) -> {status: ok|check, bg_nonuniformity, flicker, coverage, reasons[]}` (bg std of alpha<0.05 region > 0.03 → `halo_risk`; mean |Δα| > 0.08 → `flicker`; coverage < 0.2 % → `empty_plate`); `retime_factor(plate_frames, window_frames) -> float`, `retime_indices(plate_frames, window_frames) -> list[int]` (nearest, factor 0.5–2.0 else `PlateLengthError`); `loop_seam_ok(first, last) -> bool` (mean abs diff ≤ 6/255) and `loop_indices` only if ok.

- [ ] Tests: pure black plate → alpha 0 and `empty_plate`; bright wisp on noisy dark bg (σ 4/255) → no alpha in bg > 0.05 and `ok`; plate with brightening bg → `halo_risk`; 49-frame plate into a 40-frame window → factor 1.225, indices monotone covering 0…48; 49 into 120 → `PlateLengthError`.
- [ ] Red → green. Commit.

### Task 12: vfx.generate_plate, bundled plates, render with plate, reuse

**Files:** Create `services/worker/oc_worker/executors/vfx_plate.py`, `presets/pocket-vfx/plates/{energy_wisps_v1,dust_burst_v1,particles_v1}.mp4` (generated by `fixtures/pocket-vfx/make_fixtures.py --plates`, procedural, MIT, 768×432, 49 frames); Modify `mock.py` (`vfx_plate_v1`), `bindings.py` (`vfx_plate_v1 = {"prompt":"string","negative":"string","seed":"int","width":"int","height":"int","frames":"int"}`, outputs `("video",)`), `vfx_render.py`; Test `services/worker/tests/test_vfx_plate.py`

**Produces:** `vfx_generate_plate(run)`: stage `plate` submits with preset prompt + palette words, `background_video_shape` rules for frames (LTX legal length ≥ effect window, e.g. 49/97/121) → `raw_plate` MP4 (`meta{preset_id, palette, seed, frames, mock}`); stage `matte` → `plate_matte_preview` MP4 (alpha as gray) + `matte_qa` JSON. Result `{plate_asset_id, matte_qa, frames}`. Never auto-swaps to a bundled plate on QA failure (UI offers it). Render resolves plate: `quick` → bundled per preset (`plate.bundled_id` or preset default); `generated` → `plate.asset_id` (must be `raw_plate` of same project; preset match else 422 `plate_mismatch`) ; retime to window, cover-fit around the anchor at `scale`, matte with `plate.matte`.
Reuse: render stages never call providers; changing scale/intensity/timing creates a new render job but no new `vfx.track`/`vfx.generate_plate` rows.

- [ ] Tests: generate_plate (mock) → raw plate + matte QA; render generated with plate → ROI pass, `plate_source=="generated"`, badge data in result; intensity-only revision → render 202 using the same track and plate (assert JobStage counts unchanged for track/plate jobs); plate of 121 frames for a 2 s window works, for 0.4 s window → 422 `plate_length` at plan time; OOM-like provider failure on plate keeps the track job's outputs and the next plate job succeeds.
- [ ] Red → green. Commit.

## Phase P0-E — audio, export

### Task 13: Audio plan and mux

**Files:** Create `open_creative/pocket_vfx/audio.py`, `presets/pocket-vfx/sfx/{impact_v1,aura_v1,blink_v1}.wav` (procedurally synthesized by `make_fixtures.py --sfx`, MIT); Modify `vfx_render.py` (encode stage); Test `tests/test_pv_audio.py`

**Produces:** `@dataclass AudioPlan(original: Path|None, sfx: Path|None, sfx_delay_ms: int, sfx_gain_db: float, duration_ms: int)`; `plan_audio(cfg, preset, window, source_audio) -> AudioPlan|None` (None → no audio stream); `mux(video_mp4, plan, out_mp4)` — fixed-argument ffmpeg: `adelay` with integer ms, `afade` in 10 ms/out 120 ms, `volume` in dB, `amix` with `normalize=0`, `alimiter limit=0.89`, `-c:v copy -c:a aac -b:a 160k -shortest` padded/trimmed to exact duration. SFX delay = `peak_ms − sfx.peak_offset_ms` (clamped ≥ 0, offset trimmed).

- [ ] Tests: no source audio + SFX off → output has no audio stream; with_audio fixture → AAC stream, duration within 1/24 s of video, tone onset offset ≤ 1 frame; SFX on → onset energy peak within ±1 frame of `peak_ms`; peak sample ≤ 0.95 full scale (no clipping).
- [ ] Red → green. Commit.

### Task 14: Export executor

**Files:** Create `services/worker/oc_worker/executors/vfx_export.py`; Modify `executors/export.py` (app branch `pocket-vfx`), `infra/Dockerfile.python` (COPY `fixtures/pocket-vfx/manifest.json` + clips used by mock); Test `services/worker/tests/test_vfx_export.py`

**Produces:** formats for a `vfx.render` source: `mp4` (render output), `cover_png`, `recipe_zip` (config of the pinned revision, track JSON, mask manifest, plate meta + matte config, preset JSON, workflow READMEs/graphs present, model manifest, input hashes, ROI/occlusion/tracking QA, README stating Quick vs Generated, Mock if applicable, "SAM masks are segmentation, not alpha matting"), `layers_zip` (`effect/%04d.png` RGBA straight-alpha sRGB, `foreground/%04d.png` subject RGBA from mask, `manifest.json` — README states MP4 has no alpha).

- [ ] Tests: each format on a mock render; recipe references the render's `input_revision` and `track_key`; layers_zip frame count = N and alpha nonzero only inside ROI; unknown format → 422 at API.
- [ ] Red → green. Commit.

## Phase web

### Task 15: Contracts, app scaffold, upload + trim screen

**Files:** Modify `packages/contracts/src/index.ts` (`AppSlug` += "pocket-vfx", `VfxJobKind`, `PocketVfxConfig`, `VfxPreset`, `TrackJson`, `VfxValidateResponse`, result maps, `Asset` gains `fps_num`/`fps_den`), root `package.json` (`dev:pocket-vfx`); Create `apps/pocket-vfx/` (copy camera-sketch scaffold: next config, tailwind tokens from Global Constraints, layout, providers, `page.tsx`, `p/[id]/page.tsx`, `s/[token]/page.tsx`, `i18n/{en,ko}.ts`, `src/api/client.ts` with `presets()`, `validate()`), `src/model/timeline.ts`, `src/components/{StartScreen,TrimScreen,TopBar}.tsx`, `src/flow/upload.ts`; Test `apps/pocket-vfx/tests/{i18n,timeline,parity}.test.ts`; Modify `fixtures/pocket-vfx/make_fixtures.py` (writes `parity.v1.json`: frame_index cases, Impact radius/alpha curve samples, retime indices)

**Produces:** `frameIndex(tMs)`, `frameCount`, `effectWindow`, `timingErrors` (TS ports, single conversion point). Start screen Upload clip / Try sample (same upload path; sample is silent); after `asset.inspect`, Trim screen: `clip_preview` player + filmstrip, range handles ≤ 5 s, aspect toggle (default from orientation), Keep original sound toggle (on; disabled for silent clips); "Prepare clip" submits `vfx.analyze` and on success PATCHes `work_asset_id` (no generation spent). Mock badge as in camera-sketch.

- [ ] Tests: en/ko key parity; TS `frameIndex`/curves/retime equal `parity.v1.json` exactly; `timingErrors` codes match Python. Manual: dev server → sample → trim → prepare → filmstrip shows.
- [ ] Red → green. Commit.

### Task 16: Editor stage, overlays, timeline, client proxy preview

**Files:** Create `src/state/editorStore.ts`, `src/components/{Editor,EffectCards,Stage,StageOverlay,ViewToggle,Timeline,Loupe}.tsx`, `src/model/{procedural.ts,preview.ts,frames.ts}`, `src/hooks/{useFrames,shortcuts}.ts`; Test `tests/{procedural,editorStore,frames}.test.ts`

**Produces:**
- Frame-accurate playback: `work_proxy` `<video>` drawn to canvas via `requestVideoFrameCallback` (fallback rAF), current frame = `frameIndex(mediaTime*1000)`; seek to frame = `(i + 0.5)/24`.
- Tools Anchor / Select subject (+/− points, Alt = negative) / Refine mask (brush on current frame → PNG upload as `mask_edit`, `mask_edits` entry) / Clean plate (Blink only); Escape cancels; Konva overlay draws anchor (#DD8BFF), trajectory (#69D6A5, invalid segments red dashed), prompt points.
- View toggle Source / Mask / Effect matte / Composite + "Effects off".
- Client proxy preview: TS port of Impact Wave/Aura curves (`procedural.ts`, parity on radius/alpha only) drawn with Canvas 2D (`lighter` blend over a mask-cut layer) using the plate preview video when present; "Draft preview" label; ≥ 15 fps target; scale/timing/intensity edits update ≤ 100 ms with no server call.
- Timeline 160 px: filmstrip, playhead, start/peak/end handles (constrained order, snapping to frames), confidence strip (green → warning, invalid red), red invalid intervals clickable → seek to first invalid frame.
- Undo/redo 50 + autosave via web-core; shortcuts per shared spec (disabled while typing); Space play.
- Mobile < 768 px: stage + timeline, tabs Effect/Track/Adjust in bottom sheet, touch loupe (2× circle above finger) and fine-adjust arrows (1 proxy px), pinch = stage zoom only.

- [ ] Tests: store enforces `start ≤ peak ≤ end` on handle drags; an anchor edit marks track stale while an intensity edit does not; `frames.ts` seek/frame mapping round-trips 0…N−1; preview curve values equal parity fixture.
- [ ] Red → green; manual desktop + 390 px check. Commit.

### Task 17: Inspector, Track/validate loop, render actions

**Files:** Create `src/components/{Inspector,TrackPanel,RenderBar}.tsx`, `src/hooks/{validate,jobs}.ts`; Test `tests/{validate,renderGate}.test.ts`

**Produces:** Inspector sections per spec §3 (Timing, Position incl. follow + manual correction keyframe list, Effect, Depth order, Tracking: confidence timeline, "Re-track from here" = add correction at current frame + run `vfx.track`, Reset; Plate: Quick bundled list / Generate plate with seed + raw/matte toggle + threshold/knee sliders + "Use bundled plate instead" button on matte `check`; Audio). Validate after each autosave (debounced 300 ms, latest-revision guard). Buttons: Track (`vfx.track`), Generate effect plate (`vfx.generate_plate`, estimate dialog, explicit click), Render (`vfx.render` with latest fresh track); disabled with reasons when blocking errors; mask diagnostics jump to Mask view at the frame.

- [ ] Tests: late validate response for an older revision is ignored; Render disabled when track stale and enabled after a fresh track; matte `check` never switches plate automatically.
- [ ] Red → green. Commit.

### Task 18: Result view, export, share

**Files:** Create `src/components/{ResultView,CompareView,ExportMenu,ShareView}.tsx`, reuse camera-sketch `model/sync.ts` (move to `@oc/web-core` if identical); Test `tests/results.test.ts`

**Produces:** before/after split slider and toggle at the same timestamp (shared sync helper); badges Quick vs Generated plate, Mock; tracking/mask/matte warnings; Previous version marker when config changed after the render; downloads MP4 / cover / recipe ZIP / layers ZIP; explicit Share via shares API.

- [ ] Tests: Previous-version logic (render revision < current & render-affecting fields changed); badge never "Generated" for a quick render.
- [ ] Manual: sample → peak → anchor → Track → Quick render → Generate plate (mock) → render → compare → download all four. Commit.

## Phase P0-F — Blink Out

### Task 19: Blink Out with clean plate

**Files:** Create `open_creative/pocket_vfx/blink.py`; Modify `procedural.py` (blink particles), `vfx_render.py`, `pocket_vfx/jobs.py` (validate), web Inspector/EffectCards; Test `tests/test_pv_blink.py`

**Produces:** `clean_plate_alignment(plate_rgb, frames_gray, masks) -> {status: ok|mismatch|unknown, median_err_px, inliers}` (ORB + RANSAC homography on background, mismatch if median reprojection > 2 output px or camera motion ≠ fixed); `dissolve_alpha(mask, frame, window, seed) -> float32` (noise threshold rising from peak to end); composite: inside mask blend source → clean plate by dissolve alpha, particles from boundary; ROI = dilated mask ∪ particle bbox. Card shown disabled with "Needs a fixed camera and an empty background frame" until a clean plate is uploaded; mismatch → render 422 `clean_plate_mismatch`.

- [ ] Tests: `stomp_landscape` + `clean_plate.png` → `ok`, at `end` subject pixels equal clean plate (≤ 1/255), ROI QA pass; shifted plate (20 px) → `mismatch`; panned clip → `mismatch`; no plate → validate `clean_plate_required`.
- [ ] Red → green. Commit.

### Task 20: Infra, README, eval sheet, verification

**Files:** Modify `infra/docker-compose.yml` (`web-vfx`: APP pocket-vfx, PORT 3002), `README.md` (Pocket VFX section: effects, Quick vs Generate, limits, mock vs open weights, licenses), `.env.example`; Create `docs/pocket-vfx-eval.md` (3 inputs × each effect: tracking/edge/effect quality log), `docs/pocket-vfx-verification.md`

- [ ] `uv run pytest`, `pnpm -r test && pnpm -r typecheck`, `pnpm --filter @oc/pocket-vfx build` green; `scripts/export_app.sh pocket-vfx <scratch>` builds standalone.
- [ ] Record spec §14 table on mock profile in the verification doc (point track ≤ 5 px on 3 fixtures, mask coordinates, occlusion, timing ±1 frame, ROI ≤ 1/255, correction re-track, reuse, audio). Mark "실추론 (generated plate → matte → composite on the final Comfy deployment)" **pending** — not done.
- [ ] Commit; finish with superpowers:finishing-a-development-branch.

---

## Out of scope / pending (do not build)

- Real `open_weights_v1` graphs for `vfx_masks_v1` / `vfx_plate_v1` (SAM 2.1 small video, LTX-2.3 distilled v1.1): need a Comfy deployment + GPU; export API/UI graphs, add binding manifests, fill sha256s, run the smoke.
- Camera shake toggle, scene depth ordering, moving-camera clean plate, LTX background restoration, mask matting, multi-effect stacks, auto action recognition, pose control, physics, face regeneration, alpha video codecs, 1080p/4K.
