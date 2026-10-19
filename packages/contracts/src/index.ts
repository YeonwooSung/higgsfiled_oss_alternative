/**
 * Types for the Open Creative Apps API (`/api/v1`). They mirror the FastAPI
 * serializers in services/api/oc_api; `pnpm --filter @oc/contracts gen:openapi`
 * dumps the request schemas for reference.
 */

export type Uuid = string;
export type Aspect = "9:16" | "16:9";
export type Motion = "subtle" | "balanced";
export type AppSlug = "product-lock" | "camera-sketch";
export type ProductLockJobKind =
  | "product.segment"
  | "product.background"
  | "product.background_video"
  | "product.render"
  | "product.inspect"
  | "asset.inspect"
  | "export";
export type CameraJobKind = "camera.analyze" | "camera.guide" | "camera.cinematic" | "camera.export" | "asset.inspect";
export type JobKind = ProductLockJobKind | CameraJobKind;
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
  | "path_artifact"
  | "geometry_video"
  | "guide_depth_video"
  | "guide_validity"
  | "cinematic_video"
  | "motion_report"
  | "path_export"
  | "guides_export";

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

export interface ProjectOf<S extends AppSlug, C> {
  id: Uuid;
  app_slug: S;
  title: string;
  current_revision: number;
  config: C;
  assets: Asset[];
  recent_jobs: Job[];
  created_at: string;
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
