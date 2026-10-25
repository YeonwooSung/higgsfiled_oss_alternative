"use client";

/**
 * Konva layer over the camera view (OUTPUT frame, uv01 <-> CSS px of the fitted frame):
 * the "Sketch" framing request (numbered control points), the amber target reticle at the current
 * time, the live stroke while drawing, and the pointer handling of the three tools.
 * Client-only (loaded with next/dynamic, ssr: false).
 */
import type { SketchPoint } from "@oc/contracts";
import type Konva from "konva";
import { useEffect, useRef, useState } from "react";
import { Circle, Group, Layer, Line, Stage, Text } from "react-konva";
import type { Tool } from "../state/editorStore";

const TARGET = "#F0BA65";
const SKETCH = "#F0F3F8";
const STAGE = "#0A0C10";
/** Pointer samples closer than this (CSS px) are skipped while drawing. */
const MIN_STEP_PX = 1.5;

export interface SketchOverlayProps {
  w: number;
  h: number;
  tool: Tool;
  points: readonly SketchPoint[];
  /** Target position in the output frame at the playhead, or null when it is behind the camera. */
  targetUv: readonly number[] | null;
  /** Target at t = 0 (start of an implicit sketch for Move keyframe without a sketch). */
  targetStartUv: readonly number[] | null;
  labels: { sketch: string; target: string };
  onTarget: (uvOut: [number, number]) => void;
  onStroke: (stroke: [number, number][]) => void;
  /** Drag of control point `i` to output uv; `implicit` = no sketch yet (create one from the target). */
  onMovePoint: (i: number, u: number, v: number, implicit: boolean) => void;
}

export default function SketchOverlay(p: SketchOverlayProps) {
  const [stroke, setStroke] = useState<[number, number][] | null>(null);
  const strokeRef = useRef<[number, number][] | null>(null);
  const props = useRef(p);
  props.current = p;

  // Escape / tool change cancels a stroke in progress.
  useEffect(() => {
    strokeRef.current = null;
    setStroke(null);
  }, [p.tool]);

  // Finish a stroke even when the pointer is released outside the frame.
  useEffect(() => {
    const up = () => {
      const s = strokeRef.current;
      strokeRef.current = null;
      setStroke(null);
      if (s?.length) props.current.onStroke(s.map(([x, y]) => [x / props.current.w, y / props.current.h]));
    };
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    return () => {
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
  }, []);

  const pos = (e: Konva.KonvaEventObject<PointerEvent>): [number, number] | null => {
    const q = e.target.getStage()?.getPointerPosition();
    return q ? [Math.min(p.w, Math.max(0, q.x)), Math.min(p.h, Math.max(0, q.y))] : null;
  };

  const onDown = (e: Konva.KonvaEventObject<PointerEvent>) => {
    const q = pos(e);
    if (!q) return;
    if (p.tool === "target") p.onTarget([q[0] / p.w, q[1] / p.h]);
    else if (p.tool === "sketch") {
      strokeRef.current = [q];
      setStroke([q]);
    }
  };
  const onMove = (e: Konva.KonvaEventObject<PointerEvent>) => {
    const s = strokeRef.current;
    if (!s) return;
    const q = pos(e);
    if (!q) return;
    const last = s[s.length - 1];
    if (Math.hypot(q[0] - last[0], q[1] - last[1]) < MIN_STEP_PX) return;
    s.push(q);
    setStroke([...s]);
  };

  const implicit = !p.points.length && p.tool === "keyframe" && !!p.targetStartUv;
  const shown: readonly SketchPoint[] = implicit
    ? [{ t: 0, u: p.targetStartUv![0], v: p.targetStartUv![1] }, { t: 1, u: p.targetStartUv![0], v: p.targetStartUv![1] }]
    : p.points;
  const px = shown.map((q) => [q.u * p.w, q.v * p.h] as const);
  const draggable = p.tool === "keyframe";
  const active = p.tool !== "none";
  const tgt = p.targetUv ? [p.targetUv[0] * p.w, p.targetUv[1] * p.h] : null;

  return (
    <Stage width={p.w} height={p.h} onPointerDown={onDown} onPointerMove={onMove}
      style={{ position: "absolute", inset: 0, pointerEvents: active ? "auto" : "none", touchAction: active ? "none" : "auto",
        cursor: p.tool === "target" || p.tool === "sketch" ? "crosshair" : "default" }}>
      <Layer>
        {/* the Sketch: on-image framing request, numbered in time order */}
        {px.length > 1 && (
          <Line points={px.flat()} stroke={SKETCH} strokeWidth={2} dash={[6, 4]} lineCap="round" lineJoin="round"
            shadowColor={STAGE} shadowBlur={3} shadowOpacity={0.9} listening={false} />
        )}
        {px.length > 1 && (
          // label beside the last point, flipped inward near the right/top edge so it stays in frame
          <Text x={px[px.length - 1][0] > p.w - 80 ? px[px.length - 1][0] - 90 : px[px.length - 1][0] + 12}
            y={px[px.length - 1][1] < 28 ? px[px.length - 1][1] + 12 : px[px.length - 1][1] - 22} width={78}
            align={px[px.length - 1][0] > p.w - 80 ? "right" : "left"} text={p.labels.sketch} fontSize={12} fontStyle="600"
            fill={SKETCH} shadowColor={STAGE} shadowBlur={4} shadowOpacity={1} listening={false} />
        )}
        {px.map(([x, y], i) => (
          <Group key={i} x={x} y={y} draggable={draggable}
            dragBoundFunc={(q) => ({ x: Math.min(p.w, Math.max(0, q.x)), y: Math.min(p.h, Math.max(0, q.y)) })}
            onDragMove={(e) => p.onMovePoint(i, e.target.x() / p.w, e.target.y() / p.h, implicit)}
            onMouseEnter={(e) => { if (draggable) e.target.getStage()!.container().style.cursor = "grab"; }}
            onMouseLeave={(e) => { e.target.getStage()!.container().style.cursor = "default"; }}>
            {/* larger invisible hit area for touch */}
            <Circle radius={draggable ? 18 : 9} fill="rgba(0,0,0,0.001)" />
            <Circle radius={9} fill={STAGE} stroke={SKETCH} strokeWidth={1.5} opacity={0.92} />
            <Text text={String(i + 1)} fontSize={11} fontStyle="700" fill={SKETCH} width={18} height={18} offsetX={9} offsetY={6} align="center" listening={false} />
          </Group>
        ))}
        {/* live stroke */}
        {stroke && stroke.length > 1 && <Line points={stroke.flat()} stroke={SKETCH} strokeWidth={2} lineCap="round" lineJoin="round" listening={false} />}
        {/* amber target reticle */}
        {tgt && (
          <Group x={tgt[0]} y={tgt[1]} listening={false}>
            <Circle radius={11} stroke={TARGET} strokeWidth={2} shadowColor={STAGE} shadowBlur={3} shadowOpacity={0.9} />
            <Line points={[-18, 0, -6, 0]} stroke={TARGET} strokeWidth={2} />
            <Line points={[6, 0, 18, 0]} stroke={TARGET} strokeWidth={2} />
            <Line points={[0, -18, 0, -6]} stroke={TARGET} strokeWidth={2} />
            <Line points={[0, 6, 0, 18]} stroke={TARGET} strokeWidth={2} />
            <Text x={14} y={10} text={p.labels.target} fontSize={11} fontStyle="600" fill={TARGET} shadowColor={STAGE} shadowBlur={4} shadowOpacity={1} />
          </Group>
        )}
      </Layer>
    </Stage>
  );
}
