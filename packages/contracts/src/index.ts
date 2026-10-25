/**
 * Types for the Open Creative Apps API (`/api/v1`). They mirror the FastAPI
 * serializers in services/api/oc_api; `pnpm --filter @oc/contracts gen:openapi`
 * dumps the request schemas for reference.
 */

export type Uuid = string;
export type Aspect = "9:16" | "16:9";
export type Motion = "subtle" | "balanced";
export type AppSlug = "product-lock" | "camera-sketch" | "pocket-vfx";
export type ProductLockJobKind =
  | "product.segment"
  | "product.background"
  | "product.background_video"
  | "product.render"
  | "product.inspect"
  | "asset.inspect"
  | "export";
export type CameraJobKind = "camera.analyze" | "camera.guide" | "camera.cinematic" | "camera.export" | "asset.inspect";
export type VfxJobKind = "vfx.analyze" | "vfx.track" | "vfx.generate_plate" | "vfx.render" | "vfx.export";
export type JobKind = ProductLockJobKind | CameraJobKind | VfxJobKind;
export type JobState =
  | "queued"
  | "preparing"
  | "submitting"
  | "running"
  | "postprocessing"
  | "succeeded"
  | "failed"
  | "cancel_requested"
  | "cancelled"
  | "submission_unknown";

export const TERMINAL_JOB_STATES: readonly JobState[] = ["succeeded", "failed", "cancelled", "submission_unknown"];

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ProductLockConfig {
  schema_version: 1;
  product_asset_id: Uuid | null;
  mask_asset_id: Uuid | null;
  core_mask_asset_id: Uuid | null;
  transform: { center_x: number; center_y: number; width_ratio: number; rotation_deg: 0; feather_px: number };
  scene: {
    preset_id: string;
    accent_hex: string;
    extra_prompt: string;
    category: "beverage" | "cosmetics" | "food" | "household" | "electronics" | "other";
    background_asset_id: Uuid | null;
    background_video_asset_id: Uuid | null;
  };
  shadow: { enabled: boolean; opacity: number; blur_px: number };
  lighting: { background_brightness: number };
  motion: Motion;
  output: { aspect: Aspect; duration_ms: 5000; fps: 24 };
  protect_boxes: Box[];
  seed: number;
  model_profile: string;
}

export type AssetRole =
  | "product"
  | "mask"
  | "core_mask"
  | "background"
  | "background_video"
  | "render"
  | "cover"
  | "integrity"
  | "inspection"
  | "cutout_preview"
  | "recipe"
  // Camera Sketch
  | "scene"
  | "depth"
  | "depth_preview"
  | "depth_lowres"
  | "path_artifact"
  | "geometry_video"
  | "guide_depth_video"
  | "guide_validity"
  | "cinematic_video"
  | "motion_report"
  | "path_export"
  | "guides_export"
  // Pocket VFX
  | "clip"
  | "clip_preview"
  | "clip_filmstrip"
  | "clean_plate"
  | "mask_edit"
  | "work_clip"
  | "work_proxy"
  | "filmstrip"
  | "first_frame"
  | "source_audio"
  | "track_json"
  | "track_segments"
  | "masks_zip"
  | "mask_preview"
  | "raw_plate"
  | "plate_matte_preview"
  | "matte_qa"
  | "render_layers"
  | "render_video"
  | "roi_qa"
  | "layers_export";

export interface Asset {
  id: Uuid;
  project_id: Uuid;
  role: AssetRole;
  state: "pending_upload" | "uploaded" | "ready" | "rejected";
  sha256: string | null;
  mime: string | null;
  bytes: number | null;
  width: number | null;
  height: number | null;
  frame_count: number | null;
  fps_num: number | null;
  fps_den: number | null;
  duration_ms: number | null;
  error_code: string | null;
  filename: string | null;
  source_asset_id: Uuid | null;
  job_id: Uuid | null;
  meta: Record<string, unknown>;
  created_at: string;
  read_url?: string;
  download_url?: string;
}

export interface Job<R = Record<string, unknown> | null> {
  id: Uuid;
  project_id: Uuid;
  kind: JobKind;
  state: JobState;
  stage: string | null;
  progress: number | null;
  input_revision: number;
  options: Record<string, unknown>;
  model_profile: string;
  cancellation_requested: boolean;
  error: { code: string; message: string } | null;
  output_asset_ids: Uuid[];
  result: R;
  created_at: string;
  finished_at: string | null;
  stages?: { name: string; state: string; attempt: number; error_code: string | null }[];
}

export interface HandoffInfo {
  source_app: AppSlug;
  source_project_id: string;
  source_title: string;
  source_exists: boolean;
  mock: boolean;
}

export interface ProjectSummary {
  id: Uuid;
  app_slug: AppSlug;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectOf<S extends AppSlug, C> {
  id: Uuid;
  app_slug: S;
  title: string;
  current_revision: number;
  config: C;
  assets: Asset[];
  recent_jobs: Job[];
  created_at: string;
  upstream_mock: boolean;
  handoff: HandoffInfo | null;
}
export type Project = ProjectOf<"product-lock", ProductLockConfig>;
export type CameraSketchProject = ProjectOf<"camera-sketch", CameraSketchConfig>;

export interface Preset {
  id: string;
  version: string;
  name: string;
  description: string;
  default_accent: string;
  allowed_motion: Motion[];
  prompt_fields: string[];
  default_shadow: { enabled: boolean; opacity: number; blur_px: number };
  particles: "none" | "bokeh" | "neon";
  sample: boolean;
  thumbnail_url?: string | null;
}

export interface ProfileStatus {
  id: string;
  label: string;
  is_mock: boolean;
  available: boolean;
  reason: string | null;
}

export interface Capabilities {
  app_slug: string;
  default_profile: string;
  profiles: ProfileStatus[];
  kinds: JobKind[];
  presets: Preset[];
  limits: {
    duration_ms: number;
    fps: number;
    aspects: Aspect[];
    output_sizes: Record<Aspect, [number, number]>;
    image_max_bytes: number;
    image_max_pixels: number;
    width_ratio: [number, number];
    feather_px: [number, number];
    shadow_opacity: [number, number];
    shadow_blur_px: [number, number];
    background_brightness: [number, number];
    brush_px: [number, number];
    extra_prompt_max: number;
  };
}

/** `GET /capabilities?app_slug=camera-sketch` (see `CameraSketchApp.capabilities_extra`). */
export interface CameraCapabilities {
  app_slug: "camera-sketch";
  default_profile: string;
  profiles: ProfileStatus[];
  /** Per-kind availability: only the workflows a kind needs (camera.analyze does not need calibrated presets). */
  kind_profiles?: Partial<Record<JobKind, ProfileStatus[]>>;
  kinds: JobKind[];
  presets: CameraPreset[];
  limits: {
    duration_ms: number;
    fps: number;
    aspects: Aspect[];
    output_sizes: Record<Aspect, [number, number]>;
    image_max_bytes: number;
    image_max_pixels: number;
    focal_length_mm_equiv: [number, number];
    lateral_ratio: [number, number];
    push_ratio: [number, number];
    arc_deg: [number, number];
    sketch_raw_points_max: number;
    sketch_control_points_max: number;
    extra_prompt_max: number;
  };
}

/** `GET /projects/{id}/shares` item: an active (not revoked, not expired) public link. The URL is returned only once,
 * by `POST /projects/{id}/shares` (only the token hash is stored). */
export interface ShareSummary {
  id: string;
  output_asset_id: string;
  created_at: string;
  expires_at: string;
}

export interface Estimate {
  kind: JobKind;
  stages: string[];
  uses_gpu: boolean;
  reserved_units: number;
  unit: string;
  price: number | null;
  price_status: "unknown" | "known";
  profile: ProfileStatus;
  notes: string[];
  creates_new_job: boolean;
}

export interface Integrity {
  job_id: Uuid;
  revision: number;
  core_valid: boolean;
  core_pixel_count: number;
  frames_checked: number;
  preencode_max_error: number | null;
  failed_frames: number[];
  threshold: number;
  passed: boolean;
  postencode_metrics: { frames: number; mean_abs_error: number | null; max_error: number | null; ssim_mean: number | null };
  output_check: { ok: boolean; mismatches: Record<string, unknown> };
  preview_asset_ids: Uuid[];
}

export interface ApiErrorBody {
  error: { code: string; message: string; field?: string; retryable: boolean; request_id: string; details?: Record<string, unknown> };
}

// ---- Camera Sketch (mirrors services/api/oc_api/camera_sketch/*, open_creative.camera_sketch.path) ----

export type CameraMode = "geometry" | "cinematic";
export type CameraPresetId = "push_in" | "slide" | "small_arc";
export type CameraEasing = "linear" | "ease_in_out";
export type ControlPreset = "low" | "medium" | "high";

export interface SketchPoint {
  t: number;
  u: number;
  v: number;
}

export interface CameraSketchConfig {
  schema_version: 1;
  scene_asset_id: Uuid | null;
  depth_asset_id: Uuid | null;
  target: { u: number; v: number; depth_scale: number };
  camera: { focal_length_mm_equiv: number; sensor_width_mm_assumed: 36; roll_deg: 0 };
  path: {
    preset: CameraPresetId;
    sketch_space: "target_screen_position";
    sketch_points: SketchPoint[];
    lateral_ratio: number;
    push_ratio: number;
    arc_deg: number;
    easing: CameraEasing;
  };
  mode: CameraMode;
  generation: { seed: number; control_preset: ControlPreset; extra_prompt: string };
  output: { aspect: Aspect; duration_ms: 5000; fps: 24 };
  scene: { depth_contrast: number; edge_protection: number; show_holes: boolean };
  model_profile: string;
}

/** One entry of `GET /camera-sketch/presets` (presets.v1.json `presets[]`). */
export interface CameraPreset {
  id: CameraPresetId;
  label_en: string;
  label_ko: string;
  bounds: Record<"lateral_ratio" | "push_ratio" | "arc_deg", [number, number]>;
  default: Record<"lateral_ratio" | "push_ratio" | "arc_deg", number>;
}

/** 4x4 row-major world-to-camera matrix (OpenCV convention: x right, y down, z forward). */
export type Mat4 = number[][];

export type CompileStatus = "ok" | "warning" | "invalid";

/** Machine codes in `CompiledPath.reasons` / `CompileResponse.warnings`. */
export type PathReason =
  | "unsupported_sketch"
  | "framing_clamped"
  | "param_out_of_bounds"
  | "framing_unsolvable"
  | "camera_upside_down"
  | "camera_turned_around"
  | "target_behind_camera"
  | "target_out_of_frame"
  | "invalid_input"
  | "non_finite"
  | "unknown_easing"
  | "unknown_preset";

export interface CompiledPath {
  schema_version: number;
  solver_version: string;
  coordinate_convention: "opencv_T_cw";
  K: number[][];
  source_to_output: { scale: number; offset_x: number; offset_y: number; out_w: number; out_h: number; src_w: number; src_h: number };
  depth_mapping:
    | ({ kind: "inverse_relative" | "depth_relative"; scale: number; offset: number; eps: number; z_far_cap: number; target_z: number } & Record<string, unknown>)
    | null;
  preset: string;
  params: Record<string, number>;
  easing: string;
  fps: number;
  control_points: number[][];
  keyframes: { t: number; T_cw: Mat4; desired_uv: [number, number]; requested_uv: [number, number] }[];
  samples: { t_ms: number; T_cw: Mat4 }[];
  diagnostics: {
    framing_rms_px: number | null;
    framing_rms_rel: number | null;
    target_in_front_min_z: number | null;
    target_in_frame_fraction: number | null;
    hole_ratio: number | null;
    framing_clamped: boolean;
    details: ({ code: string } & Record<string, unknown>)[];
  };
  status: CompileStatus;
  reasons: string[];
  suggested_params: Record<string, number> | null;
}

export interface CompileRequest {
  revision: number;
  sketch: SketchPoint[];
  preset: string;
  parameters?: {
    lateral_ratio?: number | null;
    push_ratio?: number | null;
    arc_deg?: number | null;
    easing?: CameraEasing | null;
    focal_length_mm_equiv?: number | null;
  };
}

export interface CompileResponse {
  path: CompiledPath;
  control_points: number[][];
  warnings: string[];
  status: CompileStatus;
  hole_ratio: number | null;
  deferred: boolean;
}

export interface CameraPresetsResponse {
  presets: CameraPreset[];
}

export type MotionReason =
  | "no_frames"
  | "length_mismatch"
  | "unsupported_frame"
  | "shape_mismatch"
  | "low_texture_generated"
  | "low_texture_geometry"
  | "tracking_lost_geometry"
  | "tracking_lost_generated"
  | "target_drift"
  | "subject_change";

/** Generated-motion QA; `unknown` (never 0 or pass) when tracking cannot run. */
export interface MotionReport {
  status: "ok" | "check" | "unknown";
  tracked_points: number;
  target_drift_px: number | null;
  similarity_mean: number | null;
  reasons: MotionReason[];
  similarity_window_min: number | null;
  similarity_min: number | null;
  target_px_out?: number[];
  reference?: "geometry_video";
  exact_camera_control?: false;
}

export interface DepthGuideRangeInfo {
  near: number;
  far: number;
  mapping: "inverse_depth_near_white";
  percentiles: number[];
  basis: string;
  fill: string;
}

export interface CameraAnalyzeResult {
  depth_asset_id: Uuid;
  depth_preview_asset_id: Uuid;
  /** Low-res depth copy (long side <= 320) for the compile hole estimate; null for older analyze jobs. */
  depth_lowres_asset_id: Uuid | null;
  convention: { kind: string; scale: number; offset: number; eps: number };
  intrinsics_suggestion: {
    focal_length_mm_equiv: number;
    sensor_width_mm_assumed: number;
    width: number;
    height: number;
    K: number[][];
  };
  applied: false;
  revision: number;
}

export interface CameraGuideResult {
  frame_count: number;
  hole_ratio_max: number;
  hole_ratio_mean: number;
  target_visibility_min: number;
  framing_rms_rel: number | null;
  warnings: string[];
  fill_strategy: Record<string, string>;
  depth_guide: DepthGuideRangeInfo;
  mesh_stride: number;
  path_status: CompileStatus;
  mode: "geometry";
  label: string;
  geometry_hash: string;
  revision: number;
  mock: boolean;
  path_asset_id: Uuid;
  geometry_video_asset_id: Uuid;
  guide_depth_video_asset_id: Uuid;
  guide_validity_asset_id: Uuid;
}

export interface CameraCinematicResult {
  mode: "cinematic";
  label: string;
  exact_camera_control: false;
  frame_count: number;
  guide_job_id: Uuid;
  qa: MotionReport;
  cinematic_video_asset_id: Uuid;
  motion_report_asset_id: Uuid;
  revision: number;
  mock: boolean;
  seed: number;
  control_preset: ControlPreset;
}

export type CameraExportFormat = "mp4" | "path_json" | "guides_zip" | "recipe_zip";

export interface CameraExportResult {
  format: CameraExportFormat;
  source_job_id: Uuid;
  revision: number;
  mode: CameraMode;
}

export interface CameraJobResultMap {
  "camera.analyze": CameraAnalyzeResult;
  "camera.guide": CameraGuideResult;
  "camera.cinematic": CameraCinematicResult;
  "camera.export": CameraExportResult;
}

/** A camera job with `result` typed by kind (null until the job succeeds). */
export type CameraJob<K extends keyof CameraJobResultMap = keyof CameraJobResultMap> = {
  [P in K]: Omit<Job<CameraJobResultMap[P] | null>, "kind"> & { kind: P };
}[K];

// ---- Pocket VFX (mirrors services/api/oc_api/pocket_vfx/*, services/worker/oc_worker/executors/vfx_*) ----

export type VfxPresetId = "impact_wave_v1" | "energy_aura_v1" | "blink_out_v1";
export type VfxPalette = "violet" | "amber" | "cyan";
export type VfxLayerOrder = "behind_subject" | "in_front" | "split_glow";
export type VfxRenderProfile = "quick" | "generated";
export type VfxPlateMode = "bundled" | "generated";

/** Trim of the uploaded clip; every editor time is relative to the trimmed (normalized) clip. */
export interface VfxTrim {
  source_start_ms: number;
  /** 1000..5000 */
  duration_ms: number;
}

/** Effect timing (ms of the trimmed clip) and controls. Order/range are checked by validate, not structurally. */
export interface VfxEffect {
  preset_id: VfxPresetId;
  start_ms: number;
  peak_ms: number;
  end_ms: number;
  /** 0.25..2.0 */
  scale: number;
  /** 0..1 */
  intensity: number;
  palette: VfxPalette;
  /** -180..180 */
  rotation_deg: number;
  /** 0.2..1.0, Impact Wave ground ellipse */
  squash: number;
  layer_order: VfxLayerOrder;
}

/** Coordinates are [0,1] of the work clip. */
export interface VfxAnchor {
  frame: number;
  x: number;
  y: number;
  follow: boolean;
  /** 1..120, null = track the whole clip */
  track_window_frames: number | null;
}

export interface VfxCorrection {
  frame: number;
  x: number;
  y: number;
}

export interface VfxPromptPoint {
  x: number;
  y: number;
  /** 1 = subject, 0 = background */
  label: 0 | 1;
}

export interface VfxSubjectPrompt {
  frame: number;
  points: VfxPromptPoint[];
}

export interface VfxMaskEdit {
  frame: number;
  asset_id: Uuid;
}

export interface VfxMatte {
  /** 0.01..0.3 */
  threshold: number;
  /** 0..0.2 */
  knee: number;
}

export interface VfxPlate {
  mode: VfxPlateMode;
  /** null = the preset's default bundled plate */
  bundled_id: string | null;
  /** raw_plate from vfx.generate_plate */
  asset_id: Uuid | null;
  seed: number;
  matte: VfxMatte;
}

export interface VfxAudio {
  keep_original: boolean;
  sfx_enabled: boolean;
  /** -30..0 dB */
  sfx_gain_db: number;
}

export interface PocketVfxConfig {
  schema_version: 1;
  video_asset_id: Uuid | null;
  /** work_clip from vfx.analyze; the client sets it from the analyze result */
  work_asset_id: Uuid | null;
  trim: VfxTrim;
  effect: VfxEffect;
  anchor: VfxAnchor;
  /** max 32 */
  corrections: VfxCorrection[];
  /** max 8 */
  subject_prompts: VfxSubjectPrompt[];
  /** max 16 */
  mask_edits: VfxMaskEdit[];
  plate: VfxPlate;
  clean_plate_asset_id: Uuid | null;
  audio: VfxAudio;
  output: { aspect: Aspect; fps: 24 };
  render_profile: VfxRenderProfile;
  model_profile: string;
}

export type PocketVfxProject = ProjectOf<"pocket-vfx", PocketVfxConfig>;

/** One entry of `GET /pocket-vfx/presets` (`routes.public_preset`: no prompts or repo file paths). */
export interface VfxPreset {
  id: VfxPresetId;
  version: number;
  label_en: string;
  label_ko: string;
  input_requirements: { subject_mask: boolean; clean_plate: boolean; fixed_camera: boolean };
  plate_modes: VfxPlateMode[];
  controls: Partial<Record<"scale" | "intensity" | "rotation_deg" | "squash", [number, number]>>;
  defaults: {
    scale: number;
    intensity: number;
    palette: VfxPalette;
    rotation_deg: number;
    layer_order: VfxLayerOrder;
    durations_ms: { pre_peak: number; post_peak: number };
    squash?: number;
  };
  invalid_policy: "hide" | "freeze";
  bundled_plates: { id: string; frames: number; loopable: boolean }[];
  availability:
    | { status: "available"; requires: [] }
    | { status: "requires_clean_plate"; requires: ["clean_plate"]; condition_en: string; condition_ko: string };
}

export interface VfxPresetsResponse {
  presets: VfxPreset[];
}

/** `GET /capabilities?app_slug=pocket-vfx` (see `PocketVfxApp.capabilities_extra`). */
export interface PocketVfxCapabilities {
  app_slug: "pocket-vfx";
  default_profile: string;
  profiles: ProfileStatus[];
  kind_profiles?: Partial<Record<JobKind, ProfileStatus[]>>;
  kinds: VfxJobKind[];
  presets: VfxPreset[];
  limits: {
    trim_duration_ms: [number, number];
    source_duration_ms_max: number;
    fps: number;
    aspects: Aspect[];
    output_sizes: Record<Aspect, [number, number]>;
    clip_max_bytes: number;
    corrections_max: number;
    subject_prompts_max: number;
    prompt_points_max: number;
    mask_edits_max: number;
    sfx_gain_db: [number, number];
    seed_max: number;
  };
}

/** `meta.source` of a ready `clip` asset (`open_creative.media.video_ingest.SourceInfo`). */
export interface VfxClipSource {
  /** coded (stored) size */
  width: number;
  height: number;
  /** 0/90/180/270 clockwise, applied by autorotate */
  rotation: number;
  /** size after rotation */
  display_w: number;
  display_h: number;
  codec: string;
  duration_ms: number;
  start_ms: number;
  is_vfr: boolean;
  frame_count: number;
  avg_fps: number;
  has_audio: boolean;
  audio_codec: string | null;
  audio_start_ms: number | null;
}

export interface VfxIssue {
  code: string;
  field: string;
  message: string;
}

/** `POST /projects/{id}/vfx/validate` */
export interface VfxValidateResponse {
  valid: boolean;
  blocking_errors: VfxIssue[];
  warnings: VfxIssue[];
}

/** Track JSON (schema 1, `track.track_to_json` + vfx.track fields); x/y are [0,1] of the work clip, null when invalid. */
export interface TrackJson {
  schema_version: 1;
  proxy_size: [number, number];
  output_size: [number, number];
  points: { frame: number; x: number | null; y: number | null; valid: boolean; confidence: number }[];
  /** inclusive [first, last] frame pairs */
  invalid_intervals: [number, number][];
  algorithm: string;
  params: Record<string, number>;
  track_key: string;
  revision: number;
  smoothing: { sigma_frames: number; max_shift_proxy_px: number };
}

export type VfxCameraMotionStatus = "fixed" | "handheld" | "moving" | "unknown";

export interface VfxAnalyzeResult {
  work_asset_id: Uuid;
  proxy_asset_id: Uuid;
  filmstrip_asset_id: Uuid;
  first_frame_asset_id: Uuid;
  audio_asset_id: Uuid | null;
  motion_asset_id: Uuid;
  frame_count: number;
  has_audio: boolean;
  camera_motion: { status: VfxCameraMotionStatus; median_shift_px: number | null; max_shift_px: number | null };
  filmstrip_frames: number[];
  trim: VfxTrim;
  aspect: Aspect;
  revision: number;
}

export interface VfxMaskDiagnostic {
  frame: number;
  code: "area_jump" | "mask_empty" | "holes";
}

export interface VfxMasksResult {
  masks_key: string;
  size: [number, number];
  frame_count: number;
  diagnostics: VfxMaskDiagnostic[];
  model: string | null;
  mock: boolean;
  source: string | null;
  prompts_hash: string | null;
  masks_zip_asset_id: Uuid;
  mask_preview_asset_id: Uuid;
  reused: boolean;
}

export interface VfxTrackResult {
  track_key: string;
  invalid_intervals: [number, number][];
  valid_fraction: number;
  peak_valid: boolean;
  peak_frame: number;
  segments_total: number;
  segments_reused: number;
  follow: boolean;
  track_asset_id: Uuid;
  track_segments_asset_id: Uuid;
  work_asset_id: Uuid;
  masks: VfxMasksResult | null;
  revision: number;
}

export interface VfxMatteQa {
  status: "ok" | "check";
  bg_nonuniformity: number;
  flicker: number;
  coverage: number;
  reasons: string[];
}

export interface VfxPlateResult {
  plate_asset_id: Uuid;
  matte_preview_asset_id: Uuid;
  matte_qa_asset_id: Uuid;
  matte_qa: VfxMatteQa;
  frames: number;
  width: number;
  height: number;
  seed: number;
  palette: VfxPalette;
  preset_id: VfxPresetId;
  mock: boolean;
  revision: number;
}

export type VfxTrackingWarning =
  | { code: "track_lost"; frames: [number, number][]; policy: "hide" | "freeze" }
  | { code: "track_invalid_at_peak"; frame: number }
  | { code: "camera_not_fixed" | "camera_motion_unknown"; status: VfxCameraMotionStatus | null };

export interface VfxRenderResult {
  render_profile: VfxRenderProfile;
  /** "Quick" | "Generated plate" — never "AI generated" for a Quick render */
  label: string;
  badge: { render_profile: VfxRenderProfile; label: string; label_ko: string; plate_source: string; mock: boolean };
  roi_qa: { status: "pass" | "fail" | "invalid"; max_err: number | null; worst_frame: number | null; frames: number };
  occlusion_qa: number | null;
  occlusion: boolean;
  mask_warnings: VfxMaskDiagnostic[];
  tracking_warnings: VfxTrackingWarning[];
  /** "bundled" | "generated" | "procedural" */
  plate_source: string;
  plate_asset_id: Uuid | null;
  bundled_plate_id: string | null;
  frame_count: number;
  mock: boolean;
  has_audio: boolean;
  audio: {
    original_kept: boolean;
    sfx: boolean;
    sfx_delay_ms: number | null;
    sfx_gain_db: number | null;
    duration_ms: number;
  } | null;
  /** the deliverable MP4 (resolve by this id, never by role: a silent composite may also exist) */
  render_video_asset_id: Uuid;
  cover_asset_id: Uuid;
  roi_qa_asset_id: Uuid;
  layers_asset_id: Uuid;
  track_job_id: Uuid | null;
  revision: number;
  width: number;
  height: number;
}

export type VfxExportFormat = "mp4" | "cover_png" | "recipe_zip" | "layers_zip";

export interface VfxExportResult {
  format: VfxExportFormat;
  render_job_id: Uuid;
  revision: number;
}

export interface VfxJobResultMap {
  "vfx.analyze": VfxAnalyzeResult;
  "vfx.track": VfxTrackResult;
  "vfx.generate_plate": VfxPlateResult;
  "vfx.render": VfxRenderResult;
  "vfx.export": VfxExportResult;
}

/** A Pocket VFX job with `result` typed by kind (null until the job succeeds). */
export type VfxJob<K extends keyof VfxJobResultMap = keyof VfxJobResultMap> = {
  [P in K]: Omit<Job<VfxJobResultMap[P] | null>, "kind"> & { kind: P };
}[K];
