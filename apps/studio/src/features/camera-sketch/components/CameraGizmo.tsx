"use client";

/**
 * Top (x–z) and Side (z–y) views of the actual camera path ("Camera path", as opposed to the
 * on-image "Sketch" framing request): source camera, numbered keyframe cameras, the dense path
 * (cyan #66DCF2), the look-at target (amber #F0BA65) and the camera at the current time.
 * Both views share one uniform scale, so lengths are comparable across them.
 */
import type { CompiledPath } from "@oc/contracts";
import { cx } from "@oc/ui";
import { useMemo, useState } from "react";
import { useI18n } from "../i18n";
import { cameraCenter, type Mat4, type Vec2, type Vec3 } from "../model/camera";
import { IDENTITY_POSE, cameraGlyph, fitGizmo, gizmoPlane, poseAtTime, type GizmoView } from "../model/preview";

export const GIZMO_W = 220;
export const GIZMO_H = 160;
const PATH = "#66DCF2";
const TARGET = "#F0BA65";
const MUTED = "#A3ACB9";
const GRID = "#303845";
const PAD = 22;

export interface CameraGizmoProps {
  path: CompiledPath;
  targetWorld: Vec3;
  timeMs: number;
  /** Reduced-motion proposal, drawn dashed next to the current (solid) path. */
  comparePath?: CompiledPath | null;
  className?: string;
}

export type GizmoFitMode = "path" | "all";
/** Smallest world span the "fit path" zoom shows (keeps a static camera from filling the view). */
const PATH_MIN_SPAN = 0.02;

/**
 * Points the views are fitted to: "path" = source camera + camera paths (the target may then sit
 * outside and is drawn clamped to the view edge); "all" = also the target.
 */
function fitPoints(p: CameraGizmoProps, mode: GizmoFitMode): Vec3[] {
  const cams = (q: CompiledPath | null | undefined) => (q ? [...q.samples.map((s) => cameraCenter(s.T_cw)), ...q.keyframes.map((k) => cameraCenter(k.T_cw))] : []);
  return [[0, 0, 0], ...cams(p.path), ...cams(p.comparePath), ...(mode === "all" ? [p.targetWorld] : [])];
}

/** Clamp an SVG point into the view (inset `m`) along the ray from the view centre. */
function clampToView([x, y]: Vec2, m: number): { q: Vec2; inside: boolean } {
  const cx = GIZMO_W / 2, cy = GIZMO_H / 2;
  const dx = x - cx, dy = y - cy;
  const kx = dx ? (dx > 0 ? (GIZMO_W - m - cx) / dx : (m - cx) / dx) : Infinity;
  const ky = dy ? (dy > 0 ? (GIZMO_H - m - cy) / dy : (m - cy) / dy) : Infinity;
  const k = Math.min(1, kx, ky);
  return { q: [cx + dx * k, cy + dy * k], inside: k >= 1 };
}

const pts = (q: Vec2[]) => q.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");

function View({ view, path, targetWorld, timeMs, scale, comparePath, fitTo, minSpan }: CameraGizmoProps & { view: GizmoView; scale: number; fitTo: Vec3[]; minSpan: number }) {
  const { t } = useI18n();
  const centers = useMemo(() => path.samples.map((s) => cameraCenter(s.T_cw)), [path.samples]);
  const compare = useMemo(() => comparePath?.samples.map((s) => cameraCenter(s.T_cw)) ?? [], [comparePath]);
  const fit = useMemo(() => fitGizmo(fitTo.map((p) => gizmoPlane(p, view)), GIZMO_W, GIZMO_H, PAD, minSpan, scale).map, [fitTo, view, scale, minSpan]);
  const glyphSize = 14 / scale; // world units for ~14 px
  const wedge = (T: Mat4) => cameraGlyph(T, view, glyphSize).wedge.map(fit);
  const { q: tgt, inside: tgtInside } = clampToView(fit(gizmoPlane(targetWorld, view)), 10);
  const now = poseAtTime(path.samples, timeMs);
  const nowC = fit(gizmoPlane(cameraCenter(now), view));
  const title = view === "top" ? t("viewTop") : t("viewSide");
  const axes = view === "top" ? ["x", "z"] : ["z", "y"];
  return (
    <figure data-region={view === "top" ? "view-top" : "view-side"} className="flex flex-col gap-1">
      <figcaption className="flex items-baseline justify-between text-[11px] text-muted">
        <span className="font-semibold uppercase tracking-wide">{title}</span>
        <span className="tabular">{axes[0]}–{axes[1]}</span>
      </figcaption>
      <svg width={GIZMO_W} height={GIZMO_H} viewBox={`0 0 ${GIZMO_W} ${GIZMO_H}`} role="img"
        aria-label={`${title}: ${t("cameraPath")}`} className="rounded-panel bg-stage ring-1 ring-border">
        {/* axes through the source camera */}
        {(() => {
          const o = fit([0, 0]);
          return (
            <g stroke={GRID} strokeWidth={1}>
              <line x1={0} y1={o[1]} x2={GIZMO_W} y2={o[1]} />
              <line x1={o[0]} y1={0} x2={o[0]} y2={GIZMO_H} />
            </g>
          );
        })()}
        {/* line of sight from the current camera to the target */}
        <line x1={nowC[0]} y1={nowC[1]} x2={tgt[0]} y2={tgt[1]} stroke={MUTED} strokeWidth={1} strokeDasharray="3 3" opacity={0.6} />
        {/* source camera */}
        <polygon points={pts(wedge(IDENTITY_POSE))} fill="none" stroke={MUTED} strokeWidth={1.25}>
          <title>{t("sourceCamera")}</title>
        </polygon>
        {/* reduced-motion proposal (dashed) */}
        {compare.length > 1 && <polyline points={pts(compare.map((c) => fit(gizmoPlane(c, view))))} fill="none" stroke={PATH} strokeWidth={1.5} strokeDasharray="4 3" opacity={0.85} />}
        {/* camera path */}
        {centers.length > 1 && <polyline points={pts(centers.map((c) => fit(gizmoPlane(c, view))))} fill="none" stroke={PATH} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />}
        {/* numbered keyframe cameras */}
        {path.keyframes.map((k, i) => {
          const w = wedge(k.T_cw);
          return (
            <g key={i}>
              <polygon points={pts(w)} fill={PATH} fillOpacity={0.18} stroke={PATH} strokeWidth={1.25} />
              {/* alternate above / below so labels of nearby keyframes do not collide */}
              <text x={w[0][0] + (i % 2 ? -12 : 6)} y={w[0][1] + (i % 2 ? 14 : -5)} fill={PATH} fontSize={10} fontWeight={600}>{i + 1}</text>
            </g>
          );
        })}
        {/* current camera */}
        <circle cx={nowC[0]} cy={nowC[1]} r={3} fill="#F0F3F8" />
        {/* target */}
        <g stroke={TARGET} strokeWidth={1.5} strokeDasharray={tgtInside ? undefined : "2 2"}>
          <title>{t("target")}</title>
          <circle cx={tgt[0]} cy={tgt[1]} r={5} fill="none" />
          <line x1={tgt[0] - 8} y1={tgt[1]} x2={tgt[0] + 8} y2={tgt[1]} />
          <line x1={tgt[0]} y1={tgt[1] - 8} x2={tgt[0]} y2={tgt[1] + 8} />
        </g>
      </svg>
    </figure>
  );
}

export function CameraGizmo(p: CameraGizmoProps) {
  const { t } = useI18n();
  const [mode, setMode] = useState<GizmoFitMode>("path");
  const fitTo = useMemo(() => fitPoints(p, mode), [p, mode]);
  const minSpan = mode === "path" ? PATH_MIN_SPAN : 0.2;
  // One uniform scale for both views: the smaller of the two independent fits.
  const scale = useMemo(
    () => Math.min(...(["top", "side"] as const).map((v) => fitGizmo(fitTo.map((q) => gizmoPlane(q, v)), GIZMO_W, GIZMO_H, PAD, minSpan).scale)),
    [fitTo, minSpan],
  );
  return (
    <section aria-label={t("cameraPath")} className={cx("flex flex-col gap-2", p.className)}>
      <div className="flex items-center justify-end gap-1 text-[11px]" role="radiogroup" aria-label={t("gizmoZoom")}>
        {(["path", "all"] as const).map((m) => (
          <button key={m} type="button" role="radio" aria-checked={mode === m} onClick={() => setMode(m)}
            className={cx("min-h-[32px] rounded-button px-2", mode === m ? "bg-app font-medium text-ink ring-1 ring-border" : "text-muted hover:text-ink")}>
            {m === "path" ? t("zoomPath") : t("zoomAll")}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-3">
        <View {...p} view="top" scale={scale} fitTo={fitTo} minSpan={minSpan} />
        <View {...p} view="side" scale={scale} fitTo={fitTo} minSpan={minSpan} />
      </div>
      <ul className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
        <li className="flex items-center gap-1.5"><span aria-hidden className="h-0.5 w-4 rounded" style={{ background: PATH }} />{t("cameraPath")}</li>
        {p.comparePath && <li className="flex items-center gap-1.5"><span aria-hidden className="h-0 w-4 border-t-2 border-dashed" style={{ borderColor: PATH }} />{t("suggestedPath")}</li>}
        <li className="flex items-center gap-1.5"><span aria-hidden className="h-2 w-2 rounded-full border" style={{ borderColor: TARGET }} />{t("target")}</li>
        <li className="flex items-center gap-1.5"><span aria-hidden className="h-2 w-2 border" style={{ borderColor: MUTED }} />{t("sourceCamera")}</li>
      </ul>
    </section>
  );
}
