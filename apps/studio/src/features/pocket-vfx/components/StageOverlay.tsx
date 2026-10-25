"use client";

/**
 * Konva drawing over the stage (work clip uv01 <-> CSS px of the fitted frame): the tracking trajectory (#69D6A5,
 * invalid stretches as red dashes between the valid ends), the effect anchor (#DD8BFF) and its tracked position at
 * the current frame, correction keyframes, and the subject points of the current frame. Display only: the stage's
 * interaction layer handles the pointer. Client-only (next/dynamic, ssr: false).
 */
import { Circle, Group, Layer, Line, Rect, Stage, Text } from "react-konva";

const EFFECT = "#DD8BFF";
const TRACKING = "#69D6A5";
const WARNING = "#F2C86B";
const INVALID = "#FF6B6B";
const SHADOW = "#080809";

export interface OverlayCorrection {
  frame: number;
  x: number;
  y: number;
  /** outside +-track_window_frames: the tracker ignores it (Ruling R12) */
  outside: boolean;
}

export interface StageOverlayProps {
  w: number;
  h: number;
  /** tracked uv per frame (null = invalid); `stale` draws it faded */
  trajectory: { points: ([number, number] | null)[]; stale: boolean } | null;
  anchor: { x: number; y: number };
  /** the configured anchor is on this frame (or does not follow): full-strength handle */
  anchorHere: boolean;
  /** effect position at the current frame (tracked), when it differs from the configured anchor */
  current: [number, number] | null;
  corrections: OverlayCorrection[];
  frame: number;
  points: { x: number; y: number; label: 0 | 1 }[];
  /** touch placement in progress (committed on release) */
  pending: [number, number] | null;
  labels: { anchor: string };
}

/** Valid runs as polylines, and invalid stretches as segments joining the valid frames on both sides. */
function runs(points: ([number, number] | null)[]) {
  const valid: number[][] = [];
  const gaps: number[][] = [];
  let cur: number[] = [];
  let lastValid: [number, number] | null = null;
  let gap = false;
  for (const p of points) {
    if (p) {
      if (gap && lastValid) gaps.push([...lastValid, ...p]);
      gap = false;
      cur.push(p[0], p[1]);
      lastValid = p;
    } else {
      if (cur.length >= 4) valid.push(cur);
      cur = [];
      gap = true;
    }
  }
  if (cur.length >= 4) valid.push(cur);
  return { valid, gaps };
}

export default function StageOverlay(p: StageOverlayProps) {
  const px = (x: number, y: number): [number, number] => [x * p.w, y * p.h];
  const traj = p.trajectory ? runs(p.trajectory.points.map((q) => (q ? px(q[0], q[1]) : null))) : null;
  const [ax, ay] = px(p.anchor.x, p.anchor.y);
  return (
    <Stage width={p.w} height={p.h} listening={false} style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      <Layer listening={false}>
        {traj && (
          <Group opacity={p.trajectory!.stale ? 0.35 : 0.95}>
            {traj.valid.map((pts, i) => (
              <Line key={`v${i}`} points={pts} stroke={TRACKING} strokeWidth={2} lineCap="round" lineJoin="round" shadowColor={SHADOW} shadowBlur={2} />
            ))}
            {traj.gaps.map((pts, i) => <Line key={`g${i}`} points={pts} stroke={INVALID} strokeWidth={2} dash={[6, 5]} lineCap="round" />)}
          </Group>
        )}
        {p.corrections.map((k) => {
          const [x, y] = px(k.x, k.y);
          const here = k.frame === p.frame;
          return (
            <Rect key={k.frame} x={x} y={y} width={here ? 12 : 9} height={here ? 12 : 9} offsetX={here ? 6 : 4.5} offsetY={here ? 6 : 4.5} rotation={45}
              stroke={k.outside ? WARNING : EFFECT} strokeWidth={1.5} fill={here ? (k.outside ? WARNING : EFFECT) : SHADOW} opacity={here ? 1 : 0.7} />
          );
        })}
        {p.current && (
          <Circle x={px(...p.current)[0]} y={px(...p.current)[1]} radius={7} stroke={TRACKING} strokeWidth={2} shadowColor={SHADOW} shadowBlur={3} />
        )}
        <Group x={ax} y={ay} opacity={p.anchorHere ? 1 : 0.45}>
          <Circle radius={11} stroke={EFFECT} strokeWidth={2} shadowColor={SHADOW} shadowBlur={3} shadowOpacity={0.9} />
          <Line points={[-18, 0, -6, 0]} stroke={EFFECT} strokeWidth={2} />
          <Line points={[6, 0, 18, 0]} stroke={EFFECT} strokeWidth={2} />
          <Line points={[0, -18, 0, -6]} stroke={EFFECT} strokeWidth={2} />
          <Line points={[0, 6, 0, 18]} stroke={EFFECT} strokeWidth={2} />
          <Text x={ax > p.w - 80 ? -76 : 14} y={ay < 24 ? 10 : -24} width={62} align={ax > p.w - 80 ? "right" : "left"} text={p.labels.anchor}
            fontSize={11} fontStyle="600" fill={EFFECT} shadowColor={SHADOW} shadowBlur={4} shadowOpacity={1} />
        </Group>
        {p.points.map((q, i) => {
          const [x, y] = px(q.x, q.y);
          const color = q.label ? TRACKING : INVALID;
          return (
            <Group key={i} x={x} y={y}>
              <Circle radius={8} fill={SHADOW} stroke={color} strokeWidth={2} opacity={0.95} />
              <Line points={[-4, 0, 4, 0]} stroke={color} strokeWidth={2} />
              {q.label === 1 && <Line points={[0, -4, 0, 4]} stroke={color} strokeWidth={2} />}
            </Group>
          );
        })}
        {p.pending && <Circle x={px(...p.pending)[0]} y={px(...p.pending)[1]} radius={14} stroke={EFFECT} strokeWidth={1.5} dash={[4, 3]} />}
      </Layer>
    </Stage>
  );
}
