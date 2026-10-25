"use client";

import type { Box, ProductLockConfig } from "@oc/contracts";
import Konva from "konva";
import { useEffect, useMemo, useRef } from "react";
import { Ellipse, Group, Image as KImage, Layer, Rect, Stage, Text } from "react-konva";
import { outputSize, placeProduct } from "../model/geometry";

function hatchPattern(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = c.height = 8;
  const ctx = c.getContext("2d")!;
  ctx.strokeStyle = "rgba(24,121,78,0.55)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(0, 8);
  ctx.lineTo(8, 0);
  ctx.stroke();
  return c;
}

function coverRect(iw: number, ih: number, w: number, h: number) {
  const s = Math.max(w / iw, h / ih);
  return { x: (w - iw * s) / 2, y: (h - ih * s) / 2, width: iw * s, height: ih * s };
}

export interface ComposeStageProps {
  config: ProductLockConfig;
  cutout: HTMLCanvasElement | null;
  background: HTMLImageElement | null;
  hasVideo: boolean;
  scale: number;
  showCore: boolean;
  draftLabel: string;
  protectedLabel: string;
  onMove: (centerX: number, centerY: number) => void;
}

/** CPU-only placement preview. Pixel positions follow the server's place_product exactly. */
export default function ComposeStage(p: ComposeStageProps) {
  const [outW, outH] = outputSize(p.config.output.aspect);
  const shadowRef = useRef<Konva.Ellipse>(null);
  const pattern = useMemo(() => (typeof document === "undefined" ? null : hatchPattern()), []);
  const placement = p.cutout
    ? placeProduct(p.cutout.width, p.cutout.height, outW, outH, p.config.transform.center_x, p.config.transform.center_y,
        p.config.transform.width_ratio)
    : null;
  const shadow = p.config.shadow;

  useEffect(() => {
    shadowRef.current?.cache();
    shadowRef.current?.getLayer()?.batchDraw();
  }, [shadow.opacity, shadow.blur_px, shadow.enabled, placement?.x0, placement?.y0, placement?.w, placement?.h]);

  const brightness = p.config.lighting.background_brightness;
  const boxes: Box[] = p.config.protect_boxes;

  return (
    <Stage width={outW * p.scale} height={outH * p.scale} scaleX={p.scale} scaleY={p.scale} className="shadow-sm">
      <Layer listening={false}>
        {!p.hasVideo && (p.background
          ? <KImage image={p.background} {...coverRect(p.background.naturalWidth, p.background.naturalHeight, outW, outH)} />
          : <Rect width={outW} height={outH} fill="#E9E8E3" />)}
        {brightness !== 0 && (
          <Rect width={outW} height={outH} fill={brightness > 0 ? "#ffffff" : "#000000"} opacity={Math.abs(brightness)} />
        )}
        {placement && shadow.enabled && shadow.opacity > 0 && (
          <Ellipse ref={shadowRef} x={placement.x0 + placement.w / 2} y={placement.y0 + placement.h}
            radiusX={placement.w / 2} radiusY={Math.max(2, placement.h * 0.04)} fill="black" opacity={shadow.opacity}
            filters={[Konva.Filters.Blur]} blurRadius={shadow.blur_px / 2} />
        )}
      </Layer>
      <Layer>
        {placement && p.cutout && (
          <Group x={placement.x0} y={placement.y0} draggable
            onDragEnd={(e) => {
              const cx = (e.target.x() + placement.w / 2) / outW;
              const cy = (e.target.y() + placement.h / 2) / outH;
              p.onMove(Math.min(1, Math.max(0, cx)), Math.min(1, Math.max(0, cy)));
            }}>
            <KImage image={p.cutout} width={placement.w} height={placement.h} />
            <Rect width={placement.w} height={placement.h} stroke="#18794E" strokeWidth={1 / p.scale} dash={[6 / p.scale, 4 / p.scale]} />
            {p.showCore && pattern && boxes.map((b, i) => (
              <Group key={i} listening={false}>
                <Rect x={b.x * placement.w} y={b.y * placement.h} width={b.w * placement.w} height={b.h * placement.h}
                  fillPatternImage={pattern as unknown as HTMLImageElement} stroke="#18794E" strokeWidth={1 / p.scale} />
                {b.w * placement.w * p.scale > 70 && <Text x={b.x * placement.w + 2} y={b.y * placement.h + 2} text={p.protectedLabel} fontSize={11 / p.scale}
                  fill="#ffffff" padding={2 / p.scale} />}
              </Group>
            ))}
          </Group>
        )}
        <Text x={10} y={10} text={p.draftLabel} fontSize={12 / p.scale + 6} fill="rgba(23,26,24,0.55)" listening={false} />
      </Layer>
    </Stage>
  );
}
