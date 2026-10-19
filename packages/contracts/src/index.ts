/**
 * Types for the Open Creative Apps API (`/api/v1`). They mirror the FastAPI
 * serializers in services/api/oc_api; `pnpm --filter @oc/contracts gen:openapi`
 * dumps the request schemas for reference.
 */

export type Uuid = string;
export type Aspect = "9:16" | "16:9";
export type Motion = "subtle" | "balanced";
export type JobKind =
  | "product.segment"
  | "product.background"
  | "product.background_video"
  | "product.render"
  | "product.inspect"
  | "asset.inspect"
  | "export";
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
  | "recipe";

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

export interface Job {
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
  result: Record<string, unknown> | null;
  created_at: string;
  finished_at: string | null;
  stages?: { name: string; state: string; attempt: number; error_code: string | null }[];
}

export interface Project {
  id: Uuid;
  app_slug: "product-lock";
  title: string;
  current_revision: number;
  config: ProductLockConfig;
  assets: Asset[];
  recent_jobs: Job[];
  created_at: string;
}

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
