import type {
  ApiErrorBody,
  AppSlug,
  Asset,
  AssetRole,
  Capabilities,
  Estimate,
  Job,
  JobKind,
  ProjectSummary,
  ShareSummary,
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

export type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

export async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

const API_BASE = "/api/v1";

/**
 * App-agnostic API client. `P` is the project type and `C` its config type; apps pin them by
 * extending the class (or via `createApiClient`) and add their own endpoints on top.
 */
export class ApiClient<C = unknown, P extends { config: C } = { config: C }> {
  constructor(
    readonly appSlug: AppSlug,
    protected fetcher: Fetcher = (u, i) => fetch(u, i),
    protected base = API_BASE,
  ) {}

  protected async request<T>(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
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
    return this.request<Capabilities>("GET", `/capabilities?app_slug=${this.appSlug}`);
  }
  createProject(title: string) {
    return this.request<P>("POST", "/projects", { app_slug: this.appSlug, title });
  }
  /** The session's projects across every tool, newest first. */
  listProjects(limit?: number) {
    return this.request<{ projects: ProjectSummary[] }>("GET", `/projects${limit !== undefined ? `?limit=${limit}` : ""}`);
  }
  getProject(id: string) {
    return this.request<P>("GET", `/projects/${id}`);
  }
  patchProject(id: string, expectedRevision: number, config: C) {
    return this.request<P>("PATCH", `/projects/${id}`, { expected_revision: expectedRevision, config });
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
  /** SSE endpoint of a job; pass to `new EventSource(url)`. */
  jobEventsUrl(id: string): string {
    return `${this.base}/jobs/${id}/events`;
  }
  createExport(projectId: string, jobId: string, format: string) {
    return this.request<Job>("POST", `/projects/${projectId}/exports`, { job_id: jobId, format });
  }
  createShare(projectId: string, outputAssetId: string, ttlDays = 7) {
    return this.request<{ id: string; url: string; expires_at: string }>("POST", `/projects/${projectId}/shares`,
      { output_asset_id: outputAssetId, ttl_days: ttlDays });
  }
  /** Active links of the project, newest first (no URL: it is shown once, at creation). */
  async listShares(projectId: string): Promise<ShareSummary[]> {
    return (await this.request<{ shares: ShareSummary[] }>("GET", `/projects/${projectId}/shares`)).shares;
  }
  revokeShare(id: string) {
    return this.request<void>("DELETE", `/shares/${id}`);
  }
  /**
   * Copy a finished result into a new project of `targetApp` (server allowlist). The new project's input is then
   * inspected like an upload (`inspect_job_id`); the target editor links it itself.
   */
  handoff(sourceAssetId: string, targetApp: AppSlug, idempotencyKey: string) {
    return this.request<{ project_id: string; asset_id: string; app_slug: AppSlug; inspect_job_id: string }>(
      "POST", "/handoffs", { source_asset_id: sourceAssetId, target_app: targetApp }, { "Idempotency-Key": idempotencyKey });
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

export function createApiClient(appSlug: AppSlug, fetcher?: Fetcher): ApiClient {
  return new ApiClient(appSlug, fetcher);
}
