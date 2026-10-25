import { describe, expect, it } from "vitest";
import { ownsSpace, shortcutFor } from "../../src/features/camera-sketch/hooks/shortcuts";

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

describe("Space is left to interactive controls", () => {
  it("does not play when a checkbox, radio, button, slider or any input has focus", () => {
    const owned = [
      { tag: "INPUT", type: "checkbox" }, { tag: "INPUT", type: "radio" }, { tag: "INPUT", type: "range" }, { tag: "INPUT", type: "number" },
      { tag: "BUTTON" }, { tag: "SELECT" }, { tag: "TEXTAREA" }, { tag: "A" }, { tag: "DIV", editable: true },
      { tag: "DIV", role: "button" }, { tag: "SPAN", role: "checkbox" }, { tag: "SPAN", role: "radio" }, { tag: "DIV", role: "switch" },
      { tag: "SPAN", role: "slider" },
    ];
    for (const f of owned) {
      expect(ownsSpace(f)).toBe(true);
      expect(shortcutFor(k(" "), false, ownsSpace(f))).toBeNull();
    }
  });
  it("plays from the page, a canvas or a plain container; undo/escape still work on a focused button", () => {
    for (const f of [null, { tag: "CANVAS" }, { tag: "DIV" }, { tag: "SECTION", role: "region" }]) {
      expect(ownsSpace(f)).toBe(false);
      expect(shortcutFor(k(" "), false, ownsSpace(f))).toBe("play");
    }
    expect(shortcutFor(k("z", { metaKey: true }), false, true)).toBe("undo");
    expect(shortcutFor(k("Escape"), false, true)).toBe("cancel");
  });
});
