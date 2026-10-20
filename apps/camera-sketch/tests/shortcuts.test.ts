import { describe, expect, it } from "vitest";
import { shortcutFor } from "../src/hooks/shortcuts";

const k = (key: string, m: Partial<Record<"metaKey" | "ctrlKey" | "shiftKey" | "altKey", boolean>> = {}) =>
  ({ key, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...m });

describe("editor shortcuts (shared spec §9)", () => {
  it("maps undo/redo on Cmd and Ctrl, Space and Escape", () => {
    expect(shortcutFor(k("z", { metaKey: true }), false)).toBe("undo");
    expect(shortcutFor(k("z", { ctrlKey: true }), false)).toBe("undo");
    expect(shortcutFor(k("Z", { metaKey: true, shiftKey: true }), false)).toBe("redo");
    expect(shortcutFor(k("z", { ctrlKey: true, shiftKey: true }), false)).toBe("redo");
    expect(shortcutFor(k(" "), false)).toBe("play");
    expect(shortcutFor(k("Escape"), false)).toBe("cancel");
    expect(shortcutFor(k("z"), false)).toBeNull();
    expect(shortcutFor(k(" ", { metaKey: true }), false)).toBeNull();
  });
  it("is disabled while typing in a field", () => {
    for (const e of [k("z", { metaKey: true }), k("z", { metaKey: true, shiftKey: true }), k(" "), k("Escape")]) expect(shortcutFor(e, true)).toBeNull();
  });
});
