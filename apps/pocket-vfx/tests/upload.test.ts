import { describe, expect, it } from "vitest";
import { ACCEPT, clipFileProblem, SAMPLES } from "../src/flow/upload";

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
  it("ships a landscape and a portrait sample from public/samples", () => {
    expect(SAMPLES.map((s) => s.url)).toEqual(["/samples/stomp_landscape.mp4", "/samples/rotated_portrait.mp4"]);
  });
});
