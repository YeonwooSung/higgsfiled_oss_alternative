"use client";

import { useEffect } from "react";
import { useEditor } from "../state/editorStore";

const NON_TEXT_INPUTS = new Set(["checkbox", "radio", "button", "submit", "reset", "range", "color", "file"]);

/** True while focus is in a text field (shortcuts are disabled while typing, shared spec §9). */
export function isTyping(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  if (el.isContentEditable) return true;
  if (el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true;
  return el instanceof HTMLInputElement && !NON_TEXT_INPUTS.has(el.type);
}

export type ShortcutAction = "undo" | "redo" | "play" | "cancel" | null;

/** Key event -> editor action (pure, for tests). */
export function shortcutFor(e: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "shiftKey" | "altKey">, typing: boolean): ShortcutAction {
  if (typing) return null;
  const mod = e.metaKey || e.ctrlKey;
  if (mod && !e.altKey && e.key.toLowerCase() === "z") return e.shiftKey ? "redo" : "undo";
  if (mod || e.altKey) return null;
  if (e.key === " ") return "play";
  if (e.key === "Escape") return "cancel";
  return null;
}

/** Cmd/Ctrl+Z undo, Shift+Cmd/Ctrl+Z redo, Space play/pause, Escape cancels the current tool. */
export function useEditorShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const action = shortcutFor(e, isTyping(e.target));
      if (!action || (action === "play" && e.repeat)) return;
      const s = useEditor.getState();
      e.preventDefault();
      if (action === "undo") s.undo();
      else if (action === "redo") s.redo();
      else if (action === "play") useEditor.setState({ playing: !s.playing });
      else if (action === "cancel") s.setTool("none");
    };
    // A focused button would also "click" on Space keyup; swallow it when Space is the play shortcut.
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key === " " && !isTyping(e.target) && !(e.metaKey || e.ctrlKey || e.altKey)) e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, []);
}
