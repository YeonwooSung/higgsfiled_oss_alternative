"use client";

import { create } from "zustand";
import { MaskBuffer, stampLine } from "../model/mask";

const MASK_UNDO = 20;

interface MaskState {
  sourceAssetId: string | null;
  buffer: MaskBuffer | null;
  dirty: boolean;
  version: number;
  undoStack: MaskBuffer[];
  points: { x: number; y: number; label: 0 | 1 }[];
  setBuffer: (assetId: string | null, buffer: MaskBuffer | null) => void;
  beginStroke: () => void;
  paint: (x0: number, y0: number, x1: number, y1: number, size: number, value: number) => void;
  undo: () => void;
  markClean: () => void;
  addPoint: (p: { x: number; y: number; label: 0 | 1 }) => void;
  clearPoints: () => void;
}

export const useMask = create<MaskState>((set, get) => ({
  sourceAssetId: null,
  buffer: null,
  dirty: false,
  version: 0,
  undoStack: [],
  points: [],
  setBuffer: (sourceAssetId, buffer) => set({ sourceAssetId, buffer, dirty: false, undoStack: [], version: get().version + 1 }),
  beginStroke: () => {
    const b = get().buffer;
    if (b) set({ undoStack: [...get().undoStack, b.clone()].slice(-MASK_UNDO) });
  },
  paint: (x0, y0, x1, y1, size, value) => {
    const b = get().buffer;
    if (!b) return;
    stampLine(b, x0, y0, x1, y1, size, value);
    set({ dirty: true, version: get().version + 1 });
  },
  undo: () => {
    const stack = [...get().undoStack];
    const prev = stack.pop();
    if (prev) set({ buffer: prev, undoStack: stack, dirty: true, version: get().version + 1 });
  },
  markClean: () => set({ dirty: false }),
  addPoint: (p) => set({ points: [...get().points, p].slice(-20) }),
  clearPoints: () => set({ points: [] }),
}));
