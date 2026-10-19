import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError, sha256Hex } from "../src/api/client";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("ApiClient", () => {
  it("raises typed errors from the envelope", async () => {
    const fetcher = vi.fn(async () =>
      json(409, { error: { code: "revision_conflict", message: "changed", retryable: false, request_id: "r1" } }),
    );
    const api = new ApiClient(fetcher);
    const err = await api.patchProject("p", 1, {} as never).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe("revision_conflict");
    expect(err.requestId).toBe("r1");
  });

  it("sends Idempotency-Key for job creation", async () => {
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => json(202, { id: "j" }));
    const api = new ApiClient(fetcher);
    await api.createJob("p", "product.background", 2, { seed: 1 }, "key-1");
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/api/v1/projects/p/jobs");
    expect((init!.headers as Record<string, string>)["Idempotency-Key"]).toBe("key-1");
    expect(JSON.parse(init!.body as string)).toEqual({ kind: "product.background", revision: 2, options: { seed: 1 } });
    expect(init!.credentials).toBe("include");
  });

  it("hashes bytes as lowercase hex sha256", async () => {
    const hex = await sha256Hex(new TextEncoder().encode("abc").buffer as ArrayBuffer);
    expect(hex).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
});
