import type {
  ApiErrorBody,
  Asset,
  AssetRole,
  Capabilities,
  Estimate,
  Integrity,
  Job,
  JobKind,
  Preset,
  ProductLockConfig,
  Project,
} from "@oc/contracts";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly field?: string,
    readonly retryable = false,
    readonly requestId?: string,
  ) {
    super(message);
  }
}

type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

export async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export class ApiClient {
  constructor(private fetcher: Fetcher = (u, i) => fetch(u, i), private base = "/api/v1") {}

  private async request<T>(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
    const res = await this.fetcher(`${this.base}${path}`, {
      method,
      credentials: "include",
      headers: { ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...headers },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (res.status === 204) return undefined as T;
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      const e = (data as ApiErrorBody | null)?.error;
      throw new ApiError(res.status, e?.code ?? "http_error", e?.message ?? res.statusText, e?.field, e?.retryable ?? false, e?.request_id);
    }
    return data as T;
  }

  startSession() {
    return this.request<{ session_id: string }>("POST", "/sessions");
  }
  capabilities() {
    return this.request<Capabilities>("GET", "/capabilities?app_slug=product-lock");
  }
  presets() {
    return this.request<{ presets: Preset[] }>("GET", "/product-lock/presets");
  }
  createProject(title: string) {
    return this.request<Project>("POST", "/projects", { app_slug: "product-lock", title });
  }
  getProject(id: string) {
    return this.request<Project>("GET", `/projects/${id}`);
  }
  patchProject(id: string, expectedRevision: number, config: ProductLockConfig) {
    return this.request<Project>("PATCH", `/projects/${id}`, { expected_revision: expectedRevision, config });
  }
  deleteProject(id: string) {
    return this.request<unknown>("DELETE", `/projects/${id}`);
  }
  getAsset(id: string, download = false) {
    return this.request<Asset>("GET", `/assets/${id}${download ? "?download=true" : ""}`);
  }
  createJob(projectId: string, kind: JobKind, revision: number, options: Record<string, unknown>, idempotencyKey?: string) {
    return this.request<Job>("POST", `/projects/${projectId}/jobs`, { kind, revision, options },
      idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {});
  }
  estimate(projectId: string, kind: JobKind, revision: number, options: Record<string, unknown> = {}) {
    return this.request<Estimate>("POST", `/projects/${projectId}/estimates`, { kind, revision, options });
  }
  getJob(id: string) {
    return this.request<Job>("GET", `/jobs/${id}`);
  }
  cancelJob(id: string) {
    return this.request<Job>("POST", `/jobs/${id}/cancel`);
  }
  integrity(jobId: string) {
    return this.request<Integrity>("GET", `/jobs/${jobId}/integrity`);
  }
  createExport(projectId: string, jobId: string, format: "mp4" | "cover_png" | "recipe_zip") {
    return this.request<Job>("POST", `/projects/${projectId}/exports`, { job_id: jobId, format });
  }
  createShare(projectId: string, outputAssetId: string, ttlDays = 7) {
    return this.request<{ id: string; url: string; expires_at: string }>("POST", `/projects/${projectId}/shares`,
      { output_asset_id: outputAssetId, ttl_days: ttlDays });
  }
  revokeShare(id: string) {
    return this.request<void>("DELETE", `/shares/${id}`);
  }

  /** init → PUT bytes to the short-lived URL → complete (server verifies size and sha256). */
  async uploadAsset(projectId: string, role: AssetRole, blob: Blob, filename: string): Promise<{ asset: Asset; inspect_job_id: string }> {
    const buf = await blob.arrayBuffer();
    const init = await this.request<{ asset_id: string; upload_url: string; headers: Record<string, string> }>(
      "POST", `/projects/${projectId}/assets/init`, { role, filename, mime: blob.type, bytes: buf.byteLength });
    const put = await this.fetcher(init.upload_url, { method: "PUT", body: buf, headers: init.headers, credentials: "include" });
    if (!put.ok) throw new ApiError(put.status, "upload_failed", "Upload failed. Try again.", undefined, true);
    return this.request("POST", `/assets/${init.asset_id}/complete`, { sha256: await sha256Hex(buf) });
  }
}

export const api = new ApiClient();
