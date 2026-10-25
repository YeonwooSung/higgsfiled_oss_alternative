"use client";

import type { Box } from "@oc/contracts";
import { useEffect, useRef, useState } from "react";
import { clipBox } from "../model/mask";
import { useEditor } from "../state/editorStore";
import { useMask } from "../state/maskStore";

interface Props {
  product: HTMLImageElement;
  width: number;
  height: number;
  protectedLabel: string;
  onClipped: () => void;
}

/** Source-space editor: brush add/remove, click prompts, label protection boxes. */
export function MaskStage({ product, width, height, protectedLabel, onClipped }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const { tool, brush, config, update } = useEditor();
  const mask = useMask();
  const drag = useRef<{ x: number; y: number } | null>(null);
  const [draft, setDraftState] = useState<Box | null>(null);
  const draftRef = useRef<Box | null>(null);
  const setDraft = (b: Box | null) => {
    draftRef.current = b; // read by pointerup even before React re-renders
    setDraftState(b);
  };
  const iw = product.naturalWidth;
  const ih = product.naturalHeight;
  const scale = Math.min(width / iw, height / ih);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    c.width = Math.round(iw * scale);
    c.height = Math.round(ih * scale);
    const ctx = c.getContext("2d")!;
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.drawImage(product, 0, 0, c.width, c.height);
    if (mask.buffer && mask.buffer.width === iw && mask.buffer.height === ih) {
      const off = document.createElement("canvas");
      off.width = iw;
      off.height = ih;
      const octx = off.getContext("2d")!;
      const img = octx.createImageData(iw, ih);
      for (let i = 0; i < mask.buffer.data.length; i++) {
        const inside = mask.buffer.data[i] >= 128;
        img.data[i * 4] = inside ? 24 : 40;
        img.data[i * 4 + 1] = inside ? 121 : 40;
        img.data[i * 4 + 2] = inside ? 78 : 44;
        img.data[i * 4 + 3] = inside ? 40 : 150;
      }
      octx.putImageData(img, 0, 0);
      ctx.drawImage(off, 0, 0, c.width, c.height);
    }
    const boxes = draft ? [...config!.protect_boxes, draft] : config!.protect_boxes;
    const hatch = document.createElement("canvas");
    hatch.width = hatch.height = 8;
    const hctx = hatch.getContext("2d")!;
    hctx.strokeStyle = "rgba(24,121,78,0.6)";
    hctx.lineWidth = 2;
    hctx.beginPath();
    hctx.moveTo(0, 8);
    hctx.lineTo(8, 0);
    hctx.stroke();
    for (const b of boxes) {
      const r = [b.x * c.width, b.y * c.height, b.w * c.width, b.h * c.height] as const;
      ctx.fillStyle = ctx.createPattern(hatch, "repeat")!;
      ctx.fillRect(...r);
      ctx.strokeStyle = "#18794E";
      ctx.lineWidth = 2;
      ctx.strokeRect(...r);
      ctx.fillStyle = "#18794E";
      ctx.font = "600 11px Inter, sans-serif";
      ctx.fillText(protectedLabel, r[0] + 4, r[1] + 13);
    }
    for (const pt of mask.points) {
      ctx.beginPath();
      ctx.arc(pt.x * c.width, pt.y * c.height, 6, 0, Math.PI * 2);
      ctx.fillStyle = pt.label ? "#18794E" : "#B42318";
      ctx.fill();
      ctx.strokeStyle = "#fff";
      ctx.stroke();
    }
  }, [product, iw, ih, scale, mask.version, mask.buffer, mask.points, config, draft, protectedLabel]);

  const toSource = (e: React.PointerEvent) => {
    const rect = canvas.current!.getBoundingClientRect();
    return { x: ((e.clientX - rect.left) / rect.width) * iw, y: ((e.clientY - rect.top) / rect.height) * ih };
  };

  const down = (e: React.PointerEvent) => {
    const p = toSource(e);
    try {
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
    } catch {
      /* pointer no longer active; drawing still works without capture */
    }
    if (tool === "select") {
      mask.addPoint({ x: p.x / iw, y: p.y / ih, label: e.shiftKey ? 0 : 1 });
      return;
    }
    drag.current = p;
    if (tool === "add" || tool === "remove") {
      mask.beginStroke();
      mask.paint(p.x, p.y, p.x, p.y, brush, tool === "add" ? 255 : 0);
    }
  };
  const move = (e: React.PointerEvent) => {
    if (!drag.current) return;
    const p = toSource(e);
    if (tool === "add" || tool === "remove") {
      mask.paint(drag.current.x, drag.current.y, p.x, p.y, brush, tool === "add" ? 255 : 0);
      drag.current = p;
    } else if (tool === "protect") {
      const a = drag.current;
      setDraft({ x: Math.min(a.x, p.x) / iw, y: Math.min(a.y, p.y) / ih, w: Math.abs(p.x - a.x) / iw, h: Math.abs(p.y - a.y) / ih });
    }
  };
  const up = () => {
    const draft = draftRef.current;
    if (tool === "protect" && draft && draft.w > 0.01 && draft.h > 0.01) {
      const { box, clipped } = clipBox(draft);
      if (box) update((c) => { c.protect_boxes.push(box); });
      if (clipped) onClipped();
    }
    setDraft(null);
    drag.current = null;
  };

  return (
    <canvas ref={canvas} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
      className="checker touch-none rounded-[4px] shadow-sm" style={{ cursor: tool === "select" ? "crosshair" : "cell" }}
      aria-label="product mask editor" />
  );
}
