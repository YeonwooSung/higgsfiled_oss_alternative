import { describe, expect, it } from "vitest";
import { topBarMock } from "../../src/shell/topBarMock";

describe("topBarMock", () => {
  it("is true when either the profile or the project upstream is mock", () => {
    expect(topBarMock(true, false)).toBe(true);
    expect(topBarMock(false, true)).toBe(true);
    expect(topBarMock(true, true)).toBe(true);
  });
  it("is false when neither is, and treats undefined as false", () => {
    expect(topBarMock(false, false)).toBe(false);
    expect(topBarMock(undefined, undefined)).toBe(false);
    expect(topBarMock(undefined, false)).toBe(false);
  });
});
