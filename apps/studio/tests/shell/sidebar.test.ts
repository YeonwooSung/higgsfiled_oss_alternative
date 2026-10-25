import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { ProjectSummary } from "@oc/contracts";
import { describe, expect, it, vi } from "vitest";
import { COLLAPSED_KEY, readCollapsed, writeCollapsed } from "../../src/shell/sidebarState";
import { activeToolForPath, projectHref, TOOLS, toolForSlug } from "../../src/shell/tools";

const summary = (app_slug: ProjectSummary["app_slug"], id = "abc"): ProjectSummary => ({
  id, app_slug, title: "t", created_at: "2026-10-25T00:00:00Z", updated_at: "2026-10-25T00:00:00Z",
});

describe("readCollapsed", () => {
  it("uses the oc.sidebar.collapsed key", () => {
    expect(COLLAPSED_KEY).toBe("oc.sidebar.collapsed");
  });
  it("is false without storage", () => {
    expect(readCollapsed(null)).toBe(false);
  });
  it("is false when getItem throws", () => {
    expect(readCollapsed({ getItem: () => { throw new Error("SecurityError"); } })).toBe(false);
  });
  it("is false for a missing key", () => {
    expect(readCollapsed({ getItem: () => null })).toBe(false);
  });
  it("is true for '1' and false for anything else", () => {
    const getItem = vi.fn((k: string) => (k === COLLAPSED_KEY ? "1" : null));
    expect(readCollapsed({ getItem })).toBe(true);
    expect(getItem).toHaveBeenCalledWith("oc.sidebar.collapsed");
    expect(readCollapsed({ getItem: () => "0" })).toBe(false);
  });
});

describe("writeCollapsed", () => {
  it("stores '1' or '0' under the key", () => {
    const setItem = vi.fn();
    writeCollapsed({ setItem }, true);
    writeCollapsed({ setItem }, false);
    expect(setItem.mock.calls).toEqual([[COLLAPSED_KEY, "1"], [COLLAPSED_KEY, "0"]]);
  });
  it("swallows storage errors and a missing storage", () => {
    expect(() => writeCollapsed({ setItem: () => { throw new Error("QuotaExceeded"); } }, true)).not.toThrow();
    expect(() => writeCollapsed(null, true)).not.toThrow();
  });
});

describe("TOOLS", () => {
  it("lists Camera Sketch, Product Lock, Pocket VFX in that order", () => {
    expect(TOOLS.map((t) => [t.id, t.slug, t.href])).toEqual([
      ["camera", "camera-sketch", "/camera"],
      ["product", "product-lock", "/product"],
      ["vfx", "pocket-vfx", "/vfx"],
    ]);
  });
  it("maps every slug back to its tool", () => {
    for (const t of TOOLS) expect(toolForSlug(t.slug)).toBe(t);
  });
  it("keeps each accent equal to its theme's --oc-accent in globals.css", () => {
    const css = readFileSync(fileURLToPath(new URL("../../src/app/globals.css", import.meta.url)), "utf8");
    for (const t of TOOLS) {
      const body = new RegExp(`\\.theme-${t.id}\\s*\\{([^}]*)\\}`).exec(css)![1];
      expect(body).toContain(`--oc-accent: ${t.accent};`);
    }
  });
});

describe("projectHref", () => {
  it("routes each slug to its tool's editor", () => {
    expect(projectHref(summary("camera-sketch", "p1"))).toBe("/camera/p/p1");
    expect(projectHref(summary("product-lock", "p2"))).toBe("/product/p/p2");
    expect(projectHref(summary("pocket-vfx", "p3"))).toBe("/vfx/p/p3");
  });
});

describe("activeToolForPath", () => {
  it("finds the tool from its start and editor routes only", () => {
    expect(activeToolForPath("/camera")?.id).toBe("camera");
    expect(activeToolForPath("/product/p/x")?.id).toBe("product");
    expect(activeToolForPath("/vfx/p/x")?.id).toBe("vfx");
    expect(activeToolForPath("/cameras")).toBeUndefined();
    expect(activeToolForPath("/s/tok")).toBeUndefined();
    expect(activeToolForPath(null)).toBeUndefined();
  });
});
