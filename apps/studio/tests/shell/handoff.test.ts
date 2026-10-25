import { ApiError } from "@oc/web-core";
import { describe, expect, it } from "vitest";
import { en } from "../../src/shell/i18n/en";
import { ko } from "../../src/shell/i18n/ko";
import { handoffButtons, handoffErrorText, handoffTargets, provenanceHref } from "../../src/shell/handoff";

describe("handoffTargets", () => {
  it("allows every pair of the server allowlist", () => {
    expect(handoffTargets("product-lock", "render")).toEqual(["pocket-vfx"]);
    expect(handoffTargets("product-lock", "cover")).toEqual(["camera-sketch"]);
    expect(handoffTargets("camera-sketch", "geometry_video")).toEqual(["pocket-vfx"]);
    expect(handoffTargets("camera-sketch", "cinematic_video")).toEqual(["pocket-vfx"]);
  });
  it("gives nothing for any other pair", () => {
    expect(handoffTargets("product-lock", "product")).toEqual([]);
    expect(handoffTargets("pocket-vfx", "render_video")).toEqual([]);
    expect(handoffTargets("pocket-vfx", "cover")).toEqual([]);
    expect(handoffTargets("camera-sketch", "scene")).toEqual([]);
    expect(handoffTargets("camera-sketch", "render")).toEqual([]);
  });
});

describe("handoffButtons", () => {
  it("makes one button per allowed (asset, target) with a stable key and the target's label", () => {
    expect(handoffButtons("product-lock", [
      { id: "v", role: "render" }, { id: "c", role: "cover" }, { id: "p", role: "product" },
    ])).toEqual([
      { key: "v:pocket-vfx", assetId: "v", target: "pocket-vfx", labelKey: "handoffToVfx" },
      { key: "c:camera-sketch", assetId: "c", target: "camera-sketch", labelKey: "handoffToCamera" },
    ]);
  });
  it("keeps an asset's caption and skips missing assets", () => {
    expect(handoffButtons("camera-sketch", [
      { id: "g", role: "geometry_video", label: "Geometry" }, undefined, { id: "s", role: "scene" },
    ])).toEqual([{ key: "g:pocket-vfx", assetId: "g", target: "pocket-vfx", labelKey: "handoffToVfx", caption: "Geometry" }]);
    expect(handoffButtons("pocket-vfx", [{ id: "r", role: "render_video" }])).toEqual([]);
  });
});

describe("handoffErrorText", () => {
  const t = (k: keyof typeof en) => en[k];
  it("maps the hand-off error codes to shell copy", () => {
    expect(handoffErrorText(new ApiError(409, "asset_not_ready", "x"), t)).toBe(en.handoffNotReady);
    expect(handoffErrorText(new ApiError(409, "asset_missing", "x"), t)).toBe(en.handoffMissing);
    expect(handoffErrorText(new ApiError(422, "handoff_not_allowed", "x"), t)).toBe(en.handoffNotAllowed);
    expect(handoffErrorText(new ApiError(413, "too_large", "x"), t)).toBe(en.handoffTooLarge);
    expect(handoffErrorText(new ApiError(404, "not_found", "x"), t)).toBe(en.handoffMissing);
  });
  it("falls back to the server message, then to generic copy", () => {
    expect(handoffErrorText(new ApiError(500, "boom", "Server said no"), t)).toBe("Server said no");
    expect(handoffErrorText(new Error("internal"), t)).toBe(en.handoffFailed);
  });
  it("has every hand-off string in Korean", () => {
    for (const k of ["handoffNext", "handoffToVfx", "handoffToCamera", "handoffFrom", "handoffWorking", "handoffNotReady",
      "handoffMissing", "handoffNotAllowed", "handoffTooLarge", "handoffFailed", "demoChainHint", "demoChainLink"] as const) {
      expect(ko[k]).toBeTruthy();
      expect(ko[k]).not.toBe(en[k]);
    }
    expect(en.handoffFrom).toContain("{tool}");
    expect(ko.handoffFrom).toContain("{tool}");
  });
});

describe("provenanceHref", () => {
  const h = { source_app: "product-lock" as const, source_project_id: "p1", source_title: "Shoe", mock: false };
  it("links to the source project only while it exists", () => {
    expect(provenanceHref({ ...h, source_exists: true })).toBe("/product/p/p1");
    expect(provenanceHref({ ...h, source_exists: false })).toBeNull();
  });
  it("is null for a tool this build does not know", () => {
    expect(provenanceHref({ ...h, source_app: "nope" as never, source_exists: true })).toBeNull();
  });
});
