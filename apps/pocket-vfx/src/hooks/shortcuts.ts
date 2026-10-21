"use client";

import { useEffect } from "react";
import { useEditor } from "../state/editorStore";

const NON_TEXT_INPUTS = new Set(["checkbox", "radio", "button", "submit", "reset", "range", "color", "file"]);
const INTERACTIVE_ROLES = new Set(["button", "checkbox", "radio", "switch", "slider", "menuitem", "tab", "option", "spinbutton"]);

/** True while focus is in a text field (shortcuts are disabled while typing, shared spec §9). */
export function isTyping(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  if (el.isContentEditable) return true;
  if (el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true;
  return el instanceof HTMLInputElement && !NON_TEXT_INPUTS.has(el.type);
}

/** Facts about the focused element the shortcut mapping needs (pure input for `shortcutFor`). */
export interface FocusInfo {
  tag: string;
  type?: string;
  role?: string | null;
  editable?: boolean;
}

/**
 * Focus on a control that Space activates or edits itself: any input, select, textarea, button,
 * link, contenteditable, or an element with an interactive ARIA role. Space is left to it.
 */
export function ownsSpace(f: FocusInfo | null): boolean {
  if (!f) return false;
  const tag = f.tag.toUpperCase();
  if (f.editable || ["INPUT", "SELECT", "TEXTAREA", "BUTTON", "A", "SUMMARY"].includes(tag)) return true;
  return !!f.role && INTERACTIVE_ROLES.has(f.role);
}

export function focusInfo(el: EventTarget | null): FocusInfo | null {
  if (!(el instanceof HTMLElement) || el === document.body) return null;
  return { tag: el.tagName, type: el instanceof HTMLInputElement ? el.type : undefined, role: el.getAttribute("role"), editable: el.isContentEditable };
}

export type ShortcutAction = "undo" | "redo" | "play" | "cancel" | null;

/**
 * Key event -> editor action (pure, for tests). `typing`: focus in a text field (all shortcuts off).
 * `spaceOwned`: focus on a control that Space activates (Space is not hijacked for playback).
 */
export function shortcutFor(
  e: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "shiftKey" | "altKey">, typing: boolean, spaceOwned = false,
): ShortcutAction {
  if (typing) return null;
  const mod = e.metaKey || e.ctrlKey;
  if (mod && !e.altKey && e.key.toLowerCase() === "z") return e.shiftKey ? "redo" : "undo";
  if (mod || e.altKey) return null;
  if (e.key === " ") return spaceOwned ? null : "play";
  if (e.key === "Escape") return "cancel";
  return null;
}

/** Cmd/Ctrl+Z undo, Shift+Cmd/Ctrl+Z redo, Space play/pause, Escape cancels the current tool (and its unsaved brush). */
export function useEditorShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const action = shortcutFor(e, isTyping(e.target), ownsSpace(focusInfo(e.target)));
      if (!action || (action === "play" && e.repeat)) return;
      const s = useEditor.getState();
      e.preventDefault();
      if (action === "undo") s.undo();
      else if (action === "redo") s.redo();
      else if (action === "play") s.setPlaying(!s.playing);
      else if (action === "cancel") s.setTool("none");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
