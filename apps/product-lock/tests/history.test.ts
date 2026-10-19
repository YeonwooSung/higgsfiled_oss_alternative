import { describe, expect, it } from "vitest";
import { History } from "../src/model/history";

describe("History", () => {
  it("undoes and redoes", () => {
    const h = new History<number>(0);
    h.push(1);
    h.push(2);
    expect(h.undo()).toBe(1);
    expect(h.undo()).toBe(0);
    expect(h.canUndo).toBe(false);
    expect(h.redo()).toBe(1);
    h.push(5);
    expect(h.canRedo).toBe(false);
    expect(h.current).toBe(5);
  });

  it("keeps at most 50 undo steps", () => {
    const h = new History<number>(0);
    for (let i = 1; i <= 60; i++) h.push(i);
    let n = 0;
    while (h.canUndo) {
      h.undo();
      n++;
    }
    expect(n).toBe(50);
    expect(h.current).toBe(10);
  });

  it("ignores pushes equal to the current value", () => {
    const h = new History({ a: 1 });
    h.push({ a: 1 });
    expect(h.canUndo).toBe(false);
  });
});
