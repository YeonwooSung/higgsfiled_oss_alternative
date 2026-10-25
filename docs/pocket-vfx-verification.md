# Pocket VFX verification (spec section 14) - mock profile

Source for the PR body. Branch `feat/pocket-vfx`. Everything below ran on the **mock_v1** profile (CPU, no GPU, no
Comfy). Masks there are fixture ground truth or GrabCut, and "generated" plates are a procedural stand-in, so nothing here
says anything about SAM 2.1 or LTX quality. See the last row and the eval sheet.

Test paths are abbreviated: `pv_*` = `nodes/open_creative/tests/test_pv_*.py`, `worker` = `services/worker/tests/`,
`api` = `services/api/tests/`, `app` = `apps/pocket-vfx/tests/`.

## Section 14 table

| Item | Pass condition (spec) | Evidence on mock profile | Result |
|---|---|---|---|
| Point track | Visible-frame median error <= 5 output px on 3 fixed fixtures with annotation; lost frames invalid | Fixtures: `stomp_landscape`, `rotated_portrait`, `vfr` (plus `occluded_hand` for loss). `pv_track::test_stomp_hand_median_error_within_5_output_px` (measured median 0.44 px, max 1.02 px, 120/120 valid); `pv_track::test_occluded_frames_are_invalid_not_copied` (frames 50-62 invalid with NaN position and confidence 0, median 0.41 px over frames 0-49); `worker/test_vfx_coordinates::test_gt_point_tracks_and_renders_in_output_space[rotated_portrait]` and `[vfr]` (every tracked frame <= 5 px; measured max 2.87 px / median 1.51 px rotated, max 1.93 px / median 1.30 px vfr); `worker/test_vfx_executors::test_occluded_track_hides_effect_and_retrack_reuses_segments`; `pv_track::test_feature_less_frames_are_invalid`, `::test_point_leaving_the_frame_is_invalid`, `::test_smoothing_pins_keyframes_and_never_bridges_gaps` | PASS (synthetic fixtures) |
| Mask coordinates | Portrait / rotated input: selected subject and mask align, no overlay drift | `worker/test_vfx_coordinates` (source display space -> PTS-mapped CFR work clip -> proxy -> output, rotated 9:16 and VFR; the render's effect centroid at the peak is within 5 px of the click, measured 0.86 px rotated, 2.49 px vfr); `worker/test_vfx_executors::test_portrait_masks_align_with_gt_at_output_size`, `::test_portrait_grabcut_masks_are_upright`, `::test_rotated_portrait_renders_portrait`; `worker/test_mock_vfx::test_fixture_gt_follows_the_pts_map`; `pv_masks::test_edit_to_proxy_any_size`, `::test_upscale_soft_and_binary`; `pv_parity::test_committed_parity_fixture_matches_fresh_build` and `app/parity.test.ts` (front/server frame mapping) | PASS (GT or GrabCut masks) |
| Occlusion | In `behind` mode the effect does not cover the subject core | `pv_composite::test_behind_subject_keeps_core_untouched`, `::test_in_front_covers_core` (control), `::test_split_glow_masks_behind_and_clips_glow_to_roi`; `worker/test_vfx_executors::test_behind_subject_render_never_covers_the_subject_core` (render occlusion QA 0), `::test_split_glow_draws_a_front_glow_over_the_subject_inside_the_roi`, `::test_energy_aura_render_draws_the_contour_band_behind_the_subject`; `worker/test_vfx_export::test_layers_zip_has_every_frame_with_alpha_only_inside_the_roi` (no effect alpha on the core); `pv_procedural::test_aura_mask_band_not_filled_inside_silhouette` | PASS |
| Timing | First occurrence / peak within +-1 frame of the requested timestamp | `pv_timeline::test_frame_index_rule`, `::test_effect_window` (1800 ms -> frame 43, window 34/43/82); `pv_procedural::test_impact_invisible_before_start_and_after_end`, `::test_impact_peak_has_largest_alpha_sum` (argmax within 1 of peak for 4 windows); `worker/test_vfx_executors::test_quick_render_with_static_anchor` (luma-gain argmax of the render within 1 frame of the peak, zero ROI pixels before start); `worker/test_vfx_coordinates` (peak == frame 43); `pv_blink::test_dissolve_progress_runs_from_peak_to_end` | PASS |
| Original preserved | Outside the allowed ROI, pre-compression RGB max error <= 1/255 | `pv_composite::test_no_effect_is_byte_identical_with_empty_roi`, `::test_random_effect_never_changes_outside_roi` (max_err 0, asserted `== 0` on the composite), `::test_accumulator_fails_on_single_pixel_err_two` (a single pixel at error 2 fails), `::test_accumulator_tolerates_one_and_ignores_inside_roi`; end-to-end `roi_qa.status == "pass"` and `max_err <= 1` over 120 frames in `worker/test_vfx_executors::test_quick_render_with_static_anchor`, `::test_behind_subject_render_never_covers_the_subject_core`, `::test_blink_render_dissolves_the_subject_into_the_clean_plate`, `worker/test_vfx_plate::test_render_with_generated_plate`; `worker/test_vfx_executors::test_outside_roi_change_fails_the_render` (a violation fails the job). Measured ROI max_err on the mock renders: 0 in the composite tests. H.264 re-encoding is reported separately and not part of this claim. | PASS |
| Correction | One correction keyframe re-tracks a lost span; render uses the new revision | `pv_track::test_one_correction_after_occlusion_recovers_the_track` (invalid exactly [50, 62], median 0.46 px over frames 63-119); `worker/test_vfx_executors::test_occluded_track_hides_effect_and_retrack_reuses_segments` (invalid [[50,62]], 1 of 3 segments reused, render `track_job_id` = the new track); `::test_correction_only_retrack_reuses_masks_without_a_submission`; `api/test_vfx_jobs::test_render_track_from_older_anchor_is_stale`, `::test_render_track_stale_on_track_inputs`; `pv_track::test_correction_wins_over_anchor_on_same_frame`, `::test_segment_between_keyframes_blends_both_directions` | PASS |
| Reuse | Changing scale / intensity does not rerun track or plate stages | `pv_keys::test_track_key_ignores_effect_plate_and_audio`, `::test_masks_key_ignores_corrections_and_anchor`; `worker/test_vfx_executors::test_anchor_only_retrack_reuses_masks_without_a_submission`; `api/test_vfx_jobs::test_render_only_intensity_changed_reuses_track`; `worker/test_vfx_plate::test_intensity_revision_reuses_track_and_plate`; `worker/test_vfx_executors::test_render_resumes_after_restart_from_stage_outputs`; `worker/test_vfx_export::test_layers_export_resumes_without_rerunning_the_stage`; `app/renderGate.test.ts` | PASS |
| Audio | Original audio trimmed, SFX peak position, A/V sync checked on fixtures | `pv_audio::test_original_only_keeps_sync_and_duration`, `::test_short_audio_is_padded_to_the_video`, `::test_sfx_peak_lands_on_the_effect_peak[impact_wave_v1, energy_aura_v1, blink_out_v1]` (SFX peak within 1 frame of the effect peak, duration within 1 frame), `::test_sfx_with_trimmed_head_keeps_its_peak`, `::test_mixed_audio_never_clips`, `::test_bundled_sfx_peak_offsets_are_measured`, `::test_filter_graph_is_numbers_only`; `worker/test_vfx_audio` (original kept and muxed, keep-original off drops it, SFX on a silent clip, missing SFX is a stage failure); `worker/test_vfx_executors::test_analyze_trims_and_extracts_audio`; `app/audio.test.ts` | PASS |
| Real inference | generated plate -> matte -> composite completed on the final Comfy deployment | **PENDING** - needs a Comfy deployment with a GPU. `vfx_masks_v1` (SAM 2.1 hiera small video predictor) and `vfx_plate_v1` (LTX-2.3 distilled v1.1) are not exported, binding manifests are not added, the model sha256 values in `models/pocket-vfx.manifest.json` are null, so `open_weights_v1` reports unavailable (`pv_nodes::test_open_weights_vfx_unavailable_until_graphs_exist`). `PVSubjectMasks` is tested only against a faked SAM 2 API (`pv_nodes::test_subject_masks_video_predictor_per_call_state`). Not run. | PENDING |

Manual evaluation (3 inputs per effect, plate aesthetics, matte quality): sheet in [`pocket-vfx-eval.md`](pocket-vfx-eval.md), **not yet run**.
Performance targets (proxy preview >= 15 fps, UI timing/scale change reflected within 100 ms, GPU latencies) were not measured
in a browser or on a GPU in this task; the only timing test is `pv_composite::test_full_frame_composite_speed`.

## Known limitations

- **Thresholds tuned on synthetic fixtures only**: track validity, mask diagnostics (`area_jump`, `holes`), matte QA
  (`halo_risk`, `flicker`), the Blink Out alignment check and the camera-motion classification were set against the committed
  procedural fixtures. Real footage may need retuning; the point-track numbers above are not a claim about real clips.
- **Layers ZIP size**: two RGBA PNG sequences (effect, foreground) of 120 frames at 1280x720 are not compressed further, so the
  ZIP can be large.
- **S3 read URLs need CORS**: with `OC_STORAGE_BACKEND=s3` the browser reads signed bucket URLs cross-origin (GET with `Range`);
  the bucket needs a CORS rule for the Pocket VFX origin (`http://localhost:3002` in dev). The local backend needs no setup.
- **Long decodes and the job lease**: `asset.inspect` and `vfx.analyze` heartbeat (and honour cancels) from a per-frame
  callback of the PTS probe and the decode, so a 60 s 4K30 upload no longer outlives the 60 s lease. A single frame that
  takes longer than the lease to decode (no output at all from ffmpeg/ffprobe) would still lapse it. The decoder now scales and
  crops in ffmpeg (same cover-fit geometry as `cover_fit`; pixel values differ slightly from the former OpenCV resize), and a
  trim more than 2 s into the clip seeks there first.
- SAM masks are segmentation, not matting; Blink Out never restores a background (it uses the user's clean plate and refuses a mismatch).

Resolved after this verification run:

- **SQLite worker claim race** (fixed): a claim is now a compare-and-set `UPDATE … WHERE` the row is still eligible and
  `attempts` is unchanged since the `SELECT`; a worker that loses the race sees `rowcount == 0` and picks again, so
  `--concurrency 2` on SQLite never hands one job to two threads
  (`worker/test_lease_and_stages::test_concurrent_claims_never_hand_out_the_same_job`). PostgreSQL still takes the row with
  `FOR UPDATE SKIP LOCKED`, under which the conditional `UPDATE` always matches.
- **Share links in Product Lock** (fixed): its result view now reads `GET /projects/{id}/shares` like Camera Sketch and Pocket
  VFX (shared helper `shareRowsFor` in `@oc/web-core`), so a link stays listed and revocable after leaving the view; the URL
  itself is still shown only right after creation, since only the token hash is stored.

## Commands run (final state of the branch)

| Command | Result | Time |
|---|---|---|
| `uv run pytest -q` | 787 passed, 1 skipped, 18 deselected (after the final-review fixes) | 776 s (13 min) |
| `uv run pytest -q -m slow` | 18 passed, 788 deselected | 269 s |
| `pnpm -r test` | web-core 30, product-lock 11, pocket-vfx 108 (14 files), camera-sketch 347 tests passed | 4 s |
| `pnpm -r typecheck` | all 6 projects clean | 3 s |
| `pnpm --filter @oc/pocket-vfx build` | builds (routes `/`, `/p/[id]`, `/s/[token]`) | 9 s |
| `pnpm --filter @oc/camera-sketch build` | builds (ShareLink now reads the server share list) | 9 s |
| `docker compose -f infra/docker-compose.yml config` | valid; services include `web` (:3000), `web-camera` (:3001), `web-vfx` (:3002, `APP=pocket-vfx`, `PORT=3002`) | < 1 s |
| `scripts/export_app.sh pocket-vfx <scratch>` then `pnpm install --frozen-lockfile` and `pnpm --filter @oc/pocket-vfx build` there | exports (includes `docs/03_POCKET_VFX_SPEC.md`); install 3 s; build succeeds | 3 s / 22 s |

Measured numbers above (track median/max, coordinate max and centroid errors) come from a one-off run of the same fixtures
and helpers the tests use, with the values printed; the tests themselves assert the <= 5 px bounds.

Not run: `docker build` of the `web-vfx` image and the full `docker compose up` stack; a manual browser walk-through of the UI;
the PostgreSQL-backed test run (`OC_TEST_DATABASE_URL`); the Python half of the standalone export (`uv sync` in the exported tree).

Follow-up (2026-10-25, branch `chore/ci`): the PostgreSQL-backed run was done against a throwaway `postgres:16` container:
`OC_TEST_DATABASE_URL=postgresql+psycopg://oc:oc@127.0.0.1:55432/oc_test uv run pytest services` gave 363 passed, 6 deselected
(slow), 0 skipped (so `test_lease_postgres.py` ran), in 18 min; no fixes were needed. CI (`.github/workflows/ci.yml`) now runs it on every push/PR.
