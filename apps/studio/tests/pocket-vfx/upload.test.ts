import { describe, expect, it } from "vitest";
import { ACCEPT, clipFileProblem, clipMime, SAMPLES, withClipMime } from "../../src/features/pocket-vfx/flow/upload";

describe("clip upload pre-check", () => {
  it("accepts MP4, MOV and WebM up to 100 MB", () => {
    expect(clipFileProblem({ type: "video/mp4", size: 100 * 1024 * 1024 })).toBeNull();
    expect(clipFileProblem({ type: "video/quicktime", size: 1 })).toBeNull();
    expect(clipFileProblem({ type: "video/webm", size: 1 })).toBeNull();
    expect(clipFileProblem({ type: "video/mp4", size: 100 * 1024 * 1024 + 1 })).toBe("fileSizeError");
    expect(clipFileProblem({ type: "image/png", size: 1 })).toBe("fileTypeError");
    expect(clipFileProblem({ type: "", size: 1 })).toBe("fileTypeError");
    expect(ACCEPT).toBe("video/mp4,video/quicktime,video/webm");
  });
  it("falls back to the extension when the browser gives no MIME type", () => {
    expect(clipMime({ type: "", name: "clip.mp4" })).toBe("video/mp4");
    expect(clipMime({ type: "", name: "IMG_0001.MOV" })).toBe("video/quicktime");
    expect(clipMime({ type: "", name: "a.b.webm" })).toBe("video/webm");
    expect(clipMime({ type: "", name: "clip.avi" })).toBe("");
    expect(clipMime({ type: "", name: "mp4" })).toBe("");
    expect(clipMime({ type: "video/quicktime", name: "clip.mp4" })).toBe("video/quicktime"); // a browser type wins
    expect(clipFileProblem({ type: "", name: "clip.mov", size: 1 })).toBeNull();
    expect(clipFileProblem({ type: "", name: "clip.mkv", size: 1 })).toBe("fileTypeError");
    expect(clipFileProblem({ type: "", name: "clip.mp4", size: 100 * 1024 * 1024 + 1 })).toBe("fileSizeError");
  });
  it("uploads an untyped file with the MIME type from its extension", () => {
    const untyped = new File([new Uint8Array([1, 2, 3])], "clip.mov");
    const typed = withClipMime(untyped);
    expect(typed.type).toBe("video/quicktime");
    expect(typed.name).toBe("clip.mov");
    expect(typed.size).toBe(3);
    const already = new File([new Uint8Array([1])], "clip.mp4", { type: "video/mp4" });
    expect(withClipMime(already)).toBe(already);
  });
  it("ships a landscape and a portrait sample from public/samples", () => {
    expect(SAMPLES.map((s) => s.url)).toEqual(["/samples/vfx/stomp_landscape.mp4", "/samples/vfx/rotated_portrait.mp4"]);
  });
});
