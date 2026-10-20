import { describe, expect, it } from "vitest";
import { FRAME_S, mirrorFollower, needsSeek, type MediaLike } from "../src/model/sync";

class FakeMedia implements MediaLike {
  seeks: number[] = [];
  plays = 0;
  pauses = 0;
  private t: number;
  constructor(t = 0, public paused = true, public playbackRate = 1, public readyState = 4, public duration = 5) {
    this.t = t;
  }
  get currentTime() { return this.t; }
  set currentTime(v: number) { this.t = v; this.seeks.push(v); }
  play() { this.plays += 1; this.paused = false; return Promise.resolve(); }
  pause() { this.pauses += 1; this.paused = true; }
}

describe("sync drift rule", () => {
  it("one frame at 24 fps is the tolerance", () => {
    expect(FRAME_S).toBeCloseTo(1 / 24, 12);
  });
  it("seeks only when drift exceeds 1/24 s", () => {
    expect(needsSeek(1.0, 1.0)).toBe(false);
    expect(needsSeek(1.0, 1.0 + 1 / 24 - 1e-6)).toBe(false);
    expect(needsSeek(1.0, 1.0 + 1 / 24)).toBe(false); // exactly one frame: not more than
    expect(needsSeek(1.0, 1.0 + 1 / 24 + 1e-3)).toBe(true);
    expect(needsSeek(2.0, 1.9)).toBe(true);
  });
  it("mirrorFollower seeks a drifted follower to the leader's time", () => {
    const leader = new FakeMedia(2.5, false);
    const f = new FakeMedia(2.4, false);
    expect(mirrorFollower(leader, f)).toBe(true);
    expect(f.seeks).toEqual([2.5]);
  });
  it("mirrorFollower leaves a follower within one frame alone", () => {
    const leader = new FakeMedia(2.5, false);
    const f = new FakeMedia(2.5 + 0.03, false);
    expect(mirrorFollower(leader, f)).toBe(false);
    expect(f.seeks).toEqual([]);
  });
  it("mirrors play / pause and playback rate", () => {
    const leader = new FakeMedia(1, false, 0.5);
    const f = new FakeMedia(1, true, 1);
    mirrorFollower(leader, f);
    expect(f.plays).toBe(1);
    expect(f.playbackRate).toBe(0.5);
    leader.paused = true;
    mirrorFollower(leader, f);
    expect(f.pauses).toBe(1);
    expect(f.paused).toBe(true);
  });
  it("clamps the seek target to the follower's duration and skips unloaded followers", () => {
    const leader = new FakeMedia(4.99, true);
    const short = new FakeMedia(0, true, 1, 4, 4.0);
    mirrorFollower(leader, short);
    expect(short.seeks).toEqual([4.0]);
    const unloaded = new FakeMedia(0, true, 1, 0, Number.NaN);
    expect(mirrorFollower(leader, unloaded)).toBe(false);
    expect(unloaded.seeks).toEqual([]);
  });
});
