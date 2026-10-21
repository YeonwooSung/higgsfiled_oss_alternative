import { describe, expect, it } from "vitest";
import { clampFrame, firstInvalidFrame, frameMs, frameOfCurrentTime, frameOfMediaTime, invalidFrames, outsideTrackWindow, seekTime } from "../src/model/frames";
import { FPS, frameIndex } from "../src/model/timeline";

const COUNTS = [24, 25, 60, 71, 119, 120];

describe("frame <-> time mapping (one frameIndex, seek to the frame centre)", () => {
  it("seeks frame i to (i + 0.5) / 24 s", () => {
    expect(seekTime(0)).toBeCloseTo(0.5 / FPS, 12);
    expect(seekTime(43)).toBeCloseTo(43.5 / FPS, 12);
  });
  it("round-trips every frame 0..N-1 through the seek time (currentTime) and the presented PTS (mediaTime)", () => {
    for (const n of COUNTS) {
      for (let i = 0; i < n; i++) {
        expect(frameOfCurrentTime(seekTime(i), n)).toBe(i);
        expect(frameOfMediaTime(i / FPS, n)).toBe(i);
      }
    }
  });
  it("uses frameIndex for the presented frame and clamps to 0..N-1", () => {
    expect(frameOfMediaTime(1.3, 120)).toBe(frameIndex(1300));
    expect(frameOfMediaTime(-1, 120)).toBe(0);
    expect(frameOfMediaTime(60, 120)).toBe(119);
    expect(frameOfCurrentTime(5.0, 120)).toBe(119);
    expect(clampFrame(7.6, 5)).toBe(4);
    expect(clampFrame(-3, 5)).toBe(0);
  });
  it("frameMs gives integer ms that map back to the same frame", () => {
    for (const n of COUNTS) {
      for (let i = 0; i < n; i++) {
        const ms = frameMs(i);
        expect(Number.isInteger(ms)).toBe(true);
        expect(frameIndex(ms)).toBe(i);
      }
    }
  });
});

describe("track intervals (inclusive [first, last] pairs)", () => {
  it("expands invalid intervals and finds the first invalid frame", () => {
    const iv: [number, number][] = [[50, 52], [10, 11]];
    expect([...invalidFrames(iv, 60)].sort((a, b) => a - b)).toEqual([10, 11, 50, 51, 52]);
    expect(firstInvalidFrame(iv)).toBe(10);
    expect(firstInvalidFrame([])).toBeNull();
    expect([...invalidFrames([[58, 70]], 60)]).toEqual([58, 59]);
  });
  it("flags a correction outside +-track_window_frames of the anchor frame (Ruling R12)", () => {
    const anchor = { frame: 40, track_window_frames: 10 };
    expect(outsideTrackWindow(anchor, 50)).toBe(false);
    expect(outsideTrackWindow(anchor, 30)).toBe(false);
    expect(outsideTrackWindow(anchor, 51)).toBe(true);
    expect(outsideTrackWindow(anchor, 29)).toBe(true);
    expect(outsideTrackWindow({ frame: 40, track_window_frames: null }, 0)).toBe(false);
  });
});

describe("refine-mask PNG", () => {
  it("encodes the brushed alpha as a binary 8-bit gray PNG (white = subject)", async () => {
    const { decode } = await import("fast-png");
    const { maskEditPng } = await import("../src/flow/maskEdit");
    const rgba = new Uint8ClampedArray([255, 0, 255, 0, 255, 0, 255, 127, 255, 0, 255, 128, 0, 0, 0, 255]);
    const png = decode(maskEditPng(rgba, 2, 2));
    expect([png.width, png.height, png.channels, png.depth]).toEqual([2, 2, 1, 8]);
    expect([...png.data]).toEqual([0, 0, 255, 255]);
  });
});
