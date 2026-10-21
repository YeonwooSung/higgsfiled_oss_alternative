import type { ShareSummary } from "@oc/contracts";
import { describe, expect, it } from "vitest";
import { activeSharesFor, sharesQueryKey } from "../src/shares";

const s = (id: string, asset: string, expires: string): ShareSummary => ({
  id, output_asset_id: asset, created_at: "2026-10-20T00:00:00+00:00", expires_at: expires,
});

describe("activeSharesFor", () => {
  const now = Date.parse("2026-10-21T00:00:00Z");
  const list = [s("a", "v1", "2026-10-27T00:00:00Z"), s("b", "v2", "2026-10-27T00:00:00Z"), s("c", "v1", "2026-10-20T23:59:00Z")];

  it("keeps the unexpired links of one output, in server order", () => {
    expect(activeSharesFor(list, "v1", now).map((x) => x.id)).toEqual(["a"]);
    expect(activeSharesFor(list, "v2", now).map((x) => x.id)).toEqual(["b"]);
    expect(activeSharesFor(list, "v3", now)).toEqual([]);
  });

  it("is empty while the list is loading", () => {
    expect(activeSharesFor(undefined, "v1", now)).toEqual([]);
  });

  it("keys the server list by project", () => {
    expect(sharesQueryKey("p1")).toEqual(["shares", "p1"]);
  });
});
