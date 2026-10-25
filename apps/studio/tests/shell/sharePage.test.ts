import { describe, expect, it } from "vitest";
import { pickShareView } from "../../src/shell/pickShareView";

describe("pickShareView", () => {
  it("uses app_slug when present", () => {
    expect(pickShareView({ app_slug: "camera-sketch", role: "x" })).toBe("camera");
    expect(pickShareView({ app_slug: "product-lock", role: "x" })).toBe("product");
    expect(pickShareView({ app_slug: "pocket-vfx", role: "x" })).toBe("vfx");
  });
  it("app_slug wins over role (a vfx cover picks vfx)", () => {
    expect(pickShareView({ app_slug: "pocket-vfx", role: "cover" })).toBe("vfx");
    expect(pickShareView({ app_slug: "product-lock", role: "render_video" })).toBe("product");
  });
  it("falls back by role", () => {
    expect(pickShareView({ role: "geometry_video" })).toBe("camera");
    expect(pickShareView({ role: "cinematic_video" })).toBe("camera");
    expect(pickShareView({ role: "render_video" })).toBe("vfx");
    expect(pickShareView({ role: "result_video" })).toBe("product");
    expect(pickShareView({ role: "cover" })).toBe("product");
  });
});
