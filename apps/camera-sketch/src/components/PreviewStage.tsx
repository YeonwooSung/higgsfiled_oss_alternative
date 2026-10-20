"use client";

/**
 * Center camera view: the textured depth mesh seen from the compiled path at `timeMs`
 * (plain three, rendered on demand — one frame per prop change, coalesced to rAF).
 * The canvas renders at its CSS size x min(devicePixelRatio, 2); the camera uses the OUTPUT
 * intrinsics, so the view is the 1280x720 / 720x1280 frame scaled by CSS.
 */
import type { CompiledPath } from "@oc/contracts";
import { cx } from "@oc/ui";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { LinearFilter, LinearMipmapLinearFilter, PerspectiveCamera, Scene, Texture, WebGLRenderer } from "three";
import type { PreviewScene } from "../hooks/previewScene";
import { useI18n } from "../i18n";
import { poseAtTime } from "../model/preview";
import { toThreeCamera } from "../model/three";
import { DepthMesh } from "./DepthMesh";

const NEAR = 0.05; // = render.NEAR (three's default 0.1 would clip the push-in)
const FAR = 100; // = render.FAR (> Z_FAR_CAP)
const MAX_DPR = 2;
const STAGE_BG = 0x0a0c10;

export interface PreviewStageProps {
  /** Scene image, decoded once per asset id (see `useSceneBitmap`). */
  bitmap: ImageBitmap | null;
  scene: PreviewScene | null;
  path: CompiledPath;
  timeMs: number;
  outW: number;
  outH: number;
  showHoles: boolean;
  /** True until the server compile for the current revision has arrived. */
  provisional: boolean;
  /** Depth or scene image failed to load / prepare. */
  error?: boolean;
  /** Layer drawn over the frame (sketch, reticle, tools), given the frame's CSS size. */
  overlay?: (size: { w: number; h: number }) => ReactNode;
  className?: string;
}

/** Scene bitmap -> texture (uv rows are top-down, so no flipY); disposed when the bitmap changes. */
function useTexture(bitmap: ImageBitmap | null): Texture | null {
  const tex = useMemo(() => {
    if (!bitmap) return null;
    const t = new Texture(bitmap);
    t.flipY = false;
    t.minFilter = LinearMipmapLinearFilter;
    t.magFilter = LinearFilter;
    t.needsUpdate = true;
    return t;
  }, [bitmap]);
  useEffect(() => () => tex?.dispose(), [tex]);
  return tex;
}

export function PreviewStage(p: PreviewStageProps) {
  const { t } = useI18n();
  const figRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const gl = useRef<{ renderer: WebGLRenderer; scene: Scene; camera: PerspectiveCamera } | null>(null);
  const meshRef = useRef<DepthMesh | null>(null);
  const frame = useRef(0);
  const props = useRef(p);
  props.current = p;
  const [glError, setGlError] = useState(false);
  const [fit, setFit] = useState<{ w: number; h: number } | null>(null);
  const [drawError, setDrawError] = useState(false);
  const texture = useTexture(p.bitmap);

  const draw = () => {
    const g = gl.current;
    if (!g) return;
    try {
      drawFrame(g);
      setDrawError(false);
    } catch {
      setDrawError(true); // e.g. a K that Three cannot represent; never leave a stale frame unexplained
    }
  };
  const drawFrame = (g: NonNullable<typeof gl.current>) => {
    const { path, timeMs, outW, outH, showHoles } = props.current;
    const T = poseAtTime(path.samples, timeMs);
    const cam = toThreeCamera(T, path.K, outW, outH);
    g.camera.position.copy(cam.position);
    g.camera.quaternion.copy(cam.quaternion);
    g.camera.fov = cam.fov;
    g.camera.aspect = cam.aspect;
    g.camera.updateProjectionMatrix();
    meshRef.current?.update(T, path.K, showHoles, g.renderer.getPixelRatio());
    g.renderer.render(g.scene, g.camera);
  };
  const requestDraw = () => {
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      draw();
    });
  };

  // Fit the output aspect into the figure: width and height on desktop, width only on mobile
  // (where the page scrolls and the stage sits above the panels).
  useEffect(() => {
    const fig = figRef.current!;
    const measure = () => {
      const a = props.current.outW / props.current.outH;
      const W = fig.clientWidth, H = fig.clientHeight;
      const desktop = window.matchMedia("(min-width: 768px)").matches;
      const w = Math.max(1, Math.floor(desktop && H > 0 ? Math.min(W, H * a) : W));
      setFit((f) => (f && f.w === w && f.h === Math.round(w / a) ? f : { w, h: Math.round(w / a) }));
    };
    const ro = new ResizeObserver(measure);
    ro.observe(fig);
    measure();
    return () => ro.disconnect();
  }, [p.outW, p.outH]);

  // Renderer lifetime = component lifetime.
  useEffect(() => {
    const canvas = canvasRef.current!;
    let renderer: WebGLRenderer;
    try {
      renderer = new WebGLRenderer({ canvas, antialias: true, stencil: true, alpha: false, powerPreference: "high-performance" });
    } catch {
      setGlError(true);
      return;
    }
    renderer.setClearColor(STAGE_BG, 1);
    const camera = new PerspectiveCamera(50, 16 / 9, NEAR, FAR);
    gl.current = { renderer, scene: new Scene(), camera };
    return () => {
      cancelAnimationFrame(frame.current);
      frame.current = 0;
      gl.current = null;
      renderer.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // GPU mesh per (scene mesh, texture); disposed on change/unmount.
  useEffect(() => {
    const g = gl.current;
    if (!g || !p.scene || !texture) return;
    const m = new DepthMesh(p.scene.mesh, texture);
    g.scene.add(m.group);
    meshRef.current = m;
    requestDraw();
    return () => {
      m.dispose();
      if (meshRef.current === m) meshRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.scene, texture]);

  useEffect(() => {
    const g = gl.current;
    if (!g || !fit) return;
    g.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, MAX_DPR));
    g.renderer.setSize(fit.w, fit.h, false);
    requestDraw();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fit]);

  useEffect(requestDraw, [p.path, p.timeMs, p.outW, p.outH, p.showHoles]); // eslint-disable-line react-hooks/exhaustive-deps

  const ready = !!p.scene && !!texture;
  const failed = glError || drawError || !!p.error;
  const status = failed ? t("previewFailed") : !ready ? t("previewPreparing") : p.path.status === "invalid" ? t("pathInvalidPreview") : null;
  return (
    <figure ref={figRef} data-region="camera-view" className={cx("flex min-h-0 min-w-0 flex-col items-center justify-center", p.className)}>
      <figcaption className="sr-only">{t("cameraView")}</figcaption>
      <div className="relative shrink-0 overflow-hidden rounded-panel bg-stage ring-1 ring-border"
        style={fit ? { width: fit.w, height: fit.h } : { width: "100%", aspectRatio: `${p.outW} / ${p.outH}` }}>
        <canvas ref={canvasRef} className="block h-full w-full" aria-label={t("cameraView")} role="img" />
        {fit && p.overlay?.(fit)}
        <div className="pointer-events-none absolute left-2 top-2 flex flex-wrap gap-1.5">
          {p.provisional && (
            <span title={t("provisionalHint")} className="rounded-button border border-border bg-panel/90 px-2 py-0.5 text-[11px] font-medium text-muted">
              {t("provisional")}
            </span>
          )}
        </div>
        {p.showHoles && ready && (
          <div className="pointer-events-none absolute bottom-2 left-2 flex items-center gap-1.5 rounded-button bg-panel/90 px-2 py-0.5 text-[11px] text-muted">
            <span aria-hidden className="inline-block h-3 w-3 rounded-sm" style={{ background: "repeating-linear-gradient(135deg,#E5484D 0 2px,#2a1214 2px 6px)" }} />
            {t("holes")}
          </div>
        )}
        {status && (
          <p role={failed ? "alert" : "status"} className="pointer-events-none absolute inset-x-0 bottom-8 mx-auto w-fit max-w-[90%] rounded-button bg-panel/90 px-3 py-1 text-center text-xs text-muted">
            {status}
          </p>
        )}
      </div>
    </figure>
  );
}
