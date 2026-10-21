"use client";

/**
 * Editor stage: the work proxy drawn frame-accurately into a canvas with the client "Draft preview" on top
 * (`PreviewRenderer`), the Konva overlay (anchor, trajectory, points), and the tool pointer handling:
 * - Anchor: click / drag places the anchor (a correction keyframe on other frames once tracked).
 * - Select subject: click adds a positive point (Alt or the "−" toggle: negative); clicking a point removes it.
 * - Refine mask: brush on the current frame (Alt or "−": erase); Apply uploads it as a `mask_edit`.
 * - Clean plate: shows the empty background frame over the clip (Blink Out).
 * Touch: the placement shows in a loupe above the finger and commits on release; two fingers pinch-zoom the stage
 * (view only, never the effect scale). Fine-adjust arrows move the anchor by one proxy pixel.
 */
import type { TrackJson } from "@oc/contracts";
import { Button, cx } from "@oc/ui";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Check, CircleAlert, Loader2, Minus, Plus, X, ZoomOut } from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { maskEditPng, uploadMaskEdit } from "../flow/maskEdit";
import { useAssetUrl } from "../hooks/assets";
import { useIsDesktop } from "../hooks/media";
import { useProxyPlayback, type FrameSequence } from "../hooks/useFrames";
import { useI18n } from "../i18n";
import { outsideTrackWindow } from "../model/frames";
import { anchorAt, planPreview, PreviewRenderer } from "../model/preview";
import { effectWindow } from "../model/timeline";
import { api } from "../api/client";
import { assetById, useEditor, type EditorNote } from "../state/editorStore";
import { Loupe } from "./Loupe";
import { ViewToggle } from "./ViewToggle";

const StageOverlay = dynamic(() => import("./StageOverlay"), { ssr: false });

const BRUSH_COLOR = "#DD8BFF";
const HIT_PX = 14;

export interface StageProps {
  projectId: string;
  proxyUrl: string | undefined;
  proxySize: [number, number];
  n: number;
  track: TrackJson | null;
  trackFresh: boolean;
  mask: FrameSequence | null;
  plate: { seq: FrameSequence; frames: number; loopable: boolean } | null;
  policy: "hide" | "freeze";
}

interface Zoom {
  k: number;
  x: number;
  y: number;
}

export function Stage(props: StageProps) {
  const { t } = useI18n();
  const { proxySize: [pw, ph], n } = props;
  const desktop = useIsDesktop();
  const config = useEditor((s) => s.config)!;
  const project = useEditor((s) => s.project)!;
  const frame = useEditor((s) => s.frame);
  const tool = useEditor((s) => s.tool);
  const pointLabel = useEditor((s) => s.pointLabel);
  const note = useEditor((s) => s.note);

  // ---- fit the frame into the available area
  const area = useRef<HTMLDivElement>(null);
  const [avail, setAvail] = useState<[number, number]>([0, 0]);
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setAvail([e.contentRect.width, e.contentRect.height]));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const fit = Math.max(0, Math.min(avail[0] / pw, avail[1] / ph));
  const box: [number, number] = [Math.floor(pw * fit), Math.floor(ph * fit)];

  // ---- frame-accurate drawing
  const [video, setVideo] = useState<HTMLVideoElement | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const renderer = useRef<PreviewRenderer | null>(null);
  const presented = useRef(-1);
  const live = useRef(props);
  live.current = props;
  const draw = useCallback(() => {
    const c = canvas.current;
    if (!c) return;
    const p = live.current;
    const s = useEditor.getState();
    const cfg = s.config;
    if (!cfg) return;
    const f = presented.current >= 0 ? presented.current : s.frame;
    const maskFrame = p.mask && p.mask.total === p.n ? p.mask.frames[f] ?? null : null;
    const uv = p.trackFresh && p.track ? anchorAt(p.track, cfg.anchor, f, p.policy) : [cfg.anchor.x, cfg.anchor.y] as [number, number];
    const plan = planPreview({
      window: effectWindow(cfg.effect, cfg.trim.duration_ms), frame: f, effect: cfg.effect, width: c.width, height: c.height,
      anchor: uv ? [uv[0] * c.width, uv[1] * c.height] : null, mask: !!maskFrame,
      plate: p.plate ? { frames: p.plate.frames, loopable: p.plate.loopable } : null,
    });
    const plateFrame = plan.plate && p.plate ? p.plate.seq.frames[plan.plate.index] ?? null : null;
    renderer.current ??= new PreviewRenderer();
    renderer.current.draw(c, plan, cfg.effect.palette, { source: video && video.readyState >= 2 ? video : null, mask: maskFrame, plate: plateFrame },
      s.effectsOff ? "source" : s.view);
  }, [video]);

  useProxyPlayback(video, n, (f) => {
    presented.current = f;
    draw();
  });
  // Local edits redraw at once (no server call): config, view, effects off; and when preview layers arrive.
  useEffect(() => useEditor.subscribe((s, prev) => {
    if (s.config !== prev.config || s.view !== prev.view || s.effectsOff !== prev.effectsOff) draw();
  }), [draw]);
  useEffect(draw, [draw, props.mask?.loaded, props.plate?.seq.loaded, props.track, props.trackFresh, props.policy]);

  // ---- overlay data
  const trajectory = useMemo(() => {
    if (!props.track) return null;
    const pts: ([number, number] | null)[] = Array.from({ length: n }, () => null);
    for (const q of props.track.points) if (q.valid && q.x !== null && q.y !== null && q.frame < n) pts[q.frame] = [q.x, q.y];
    return { points: pts, stale: !props.trackFresh };
  }, [props.track, props.trackFresh, n]);
  const tracked = !!props.track && props.trackFresh && config.anchor.follow;
  const current = tracked ? anchorAt(props.track, config.anchor, frame, props.policy) : null;
  const corrections = config.corrections.map((k) => ({ ...k, outside: outsideTrackWindow(config.anchor, k.frame) }));
  const points = config.subject_prompts.find((q) => q.frame === frame)?.points ?? [];
  const here = corrections.find((k) => k.frame === frame);
  const shownNote: EditorNote | null = note ?? (here?.outside ? { code: "outside_window", frame, window: config.anchor.track_window_frames! } : null);

  // ---- zoom (pinch on touch; view only)
  const [zoom, setZoom] = useState<Zoom>({ k: 1, x: 0, y: 0 });
  const touches = useRef(new Map<number, [number, number]>());
  const pinch = useRef<{ d: number; mid: [number, number]; z: Zoom } | null>(null);

  // ---- tool pointer state
  const frameBox = useRef<HTMLDivElement>(null);
  const [pending, setPending] = useState<{ uv: [number, number]; at: [number, number] } | null>(null);
  const dragging = useRef(false);
  const uvOf = (e: { clientX: number; clientY: number }): [number, number] => {
    const r = frameBox.current!.getBoundingClientRect();
    return [Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1), Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1)];
  };
  const atOf = (e: { clientX: number; clientY: number }): [number, number] => {
    const r = area.current!.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };

  // ---- Refine mask brush (proxy-size canvas; alpha = mask)
  const brush = useRef<HTMLCanvasElement>(null);
  const [brushSize, setBrushSize] = useState(12);
  const [brushDirty, setBrushDirtyState] = useState(false);
  const brushDirtyRef = useRef(false);
  const brushKey = useRef<string | null>(null);
  const setBrushDirty = (v: boolean) => {
    brushDirtyRef.current = v;
    setBrushDirtyState(v);
  };
  const [applying, setApplying] = useState(false);
  const [brushError, setBrushError] = useState<string | null>(null);
  const lastPaint = useRef<[number, number] | null>(null);
  const editId = config.mask_edits.find((m) => m.frame === frame)?.asset_id;
  const maskBitmap = props.mask && props.mask.total === n ? props.mask.frames[frame] ?? null : null;
  useEffect(() => {
    const c = brush.current;
    if (tool !== "mask" || !c) {
      brushKey.current = null; // leaving the tool discards the strokes
      brushDirtyRef.current = false;
      return;
    }
    // Same frame and edit as the strokes in progress (e.g. the mask frame decoded late): keep the strokes.
    const key = `${frame}|${editId ?? ""}`;
    if (brushKey.current === key && brushDirtyRef.current) return;
    brushKey.current = key;
    let cancelled = false;
    const x = c.getContext("2d")!;
    x.globalCompositeOperation = "source-over";
    x.clearRect(0, 0, c.width, c.height);
    setBrushDirty(false);
    setBrushError(null);
    const tint = (img: CanvasImageSource) => {
      if (brushDirtyRef.current) return; // the user started painting before the start mask arrived
      x.fillStyle = BRUSH_COLOR;
      x.fillRect(0, 0, c.width, c.height);
      x.globalCompositeOperation = "destination-in";
      x.drawImage(img, 0, 0, c.width, c.height);
      x.globalCompositeOperation = "source-over";
    };
    if (editId) {
      // an earlier edit of this frame (gray PNG): gray -> alpha
      api.getAsset(editId).then((a) => {
        if (cancelled || !a.read_url) return;
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = () => {
          if (cancelled) return;
          const tmp = document.createElement("canvas");
          tmp.width = c.width;
          tmp.height = c.height;
          const tx = tmp.getContext("2d")!;
          tx.drawImage(img, 0, 0, c.width, c.height);
          const d = tx.getImageData(0, 0, c.width, c.height);
          for (let i = 0; i < d.data.length; i += 4) d.data[i + 3] = d.data[i];
          tx.putImageData(d, 0, 0);
          tint(tmp);
        };
        img.src = a.read_url;
      }).catch(() => {});
    } else if (maskBitmap) tint(maskBitmap);
    return () => { cancelled = true; };
  }, [tool, frame, editId, maskBitmap]);

  const paint = (uv: [number, number], erase: boolean) => {
    const x = brush.current?.getContext("2d");
    if (!x) return;
    const p: [number, number] = [uv[0] * pw, uv[1] * ph];
    const from = lastPaint.current ?? p;
    x.globalCompositeOperation = erase ? "destination-out" : "source-over";
    x.strokeStyle = x.fillStyle = BRUSH_COLOR;
    x.lineWidth = 2 * brushSize;
    x.lineCap = "round";
    x.beginPath();
    x.moveTo(from[0], from[1]);
    x.lineTo(p[0], p[1]);
    x.stroke();
    x.beginPath();
    x.arc(p[0], p[1], brushSize, 0, 2 * Math.PI);
    x.fill();
    x.globalCompositeOperation = "source-over";
    lastPaint.current = p;
    setBrushDirty(true);
  };
  const applyBrush = async () => {
    const c = brush.current;
    if (!c) return;
    setApplying(true);
    setBrushError(null);
    try {
      const png = maskEditPng(c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data, c.width, c.height);
      const id = await uploadMaskEdit(props.projectId, frame, png);
      useEditor.getState().setMaskEdit(frame, id);
      setBrushDirty(false);
    } catch {
      setBrushError(t("maskEditFailed"));
    } finally {
      setApplying(false);
    }
  };

  // ---- pointer handling (interaction layer)
  const commit = (uv: [number, number], label: 0 | 1) => {
    const s = useEditor.getState();
    if (s.tool === "anchor") s.placeAnchor(uv[0], uv[1]);
    else if (s.tool === "subject") s.togglePromptPoint(uv[0], uv[1], label, HIT_PX / Math.max(box[0] * zoom.k, 1));
  };
  const onDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const s = useEditor.getState();
    if (e.pointerType === "touch") {
      touches.current.set(e.pointerId, [e.clientX, e.clientY]);
      if (touches.current.size === 2) {
        const [a, b] = [...touches.current.values()];
        pinch.current = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], z: zoom };
        setPending(null);
        lastPaint.current = null;
        return;
      }
    }
    if (s.tool === "none" || s.tool === "clean_plate" || pinch.current) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const uv = uvOf(e);
    if (s.tool === "mask") {
      lastPaint.current = null;
      paint(uv, e.altKey || s.pointLabel === 0);
      if (e.pointerType === "touch") setPending({ uv, at: atOf(e) });
      return;
    }
    if (e.pointerType === "touch") setPending({ uv, at: atOf(e) });
    else if (s.tool === "anchor") {
      dragging.current = true;
      s.placeAnchor(uv[0], uv[1], "anchor-drag");
    } else commit(uv, e.altKey ? 0 : s.pointLabel);
  };
  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "touch" && touches.current.has(e.pointerId)) {
      touches.current.set(e.pointerId, [e.clientX, e.clientY]);
      const pz = pinch.current;
      if (pz && touches.current.size >= 2) {
        const [a, b] = [...touches.current.values()];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
        const k = Math.min(Math.max((pz.z.k * d) / Math.max(pz.d, 1), 1), 4);
        setZoom(k === 1 ? { k: 1, x: 0, y: 0 } : { k, x: pz.z.x + mid[0] - pz.mid[0], y: pz.z.y + mid[1] - pz.mid[1] });
        return;
      }
    }
    if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
    const s = useEditor.getState();
    const uv = uvOf(e);
    if (s.tool === "mask") {
      paint(uv, e.altKey || s.pointLabel === 0);
      if (e.pointerType === "touch") setPending({ uv, at: atOf(e) });
    } else if (e.pointerType === "touch") setPending({ uv, at: atOf(e) });
    else if (dragging.current && s.tool === "anchor") s.placeAnchor(uv[0], uv[1], "anchor-drag");
  };
  const onUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "touch") {
      touches.current.delete(e.pointerId);
      if (pinch.current) {
        if (touches.current.size === 0) pinch.current = null;
        return;
      }
      const p = pending;
      setPending(null);
      if (p && e.type === "pointerup" && useEditor.getState().tool !== "mask") commit(p.uv, useEditor.getState().pointLabel);
    }
    dragging.current = false;
    lastPaint.current = null;
  };
  // Escape / tool change drops a placement in progress
  useEffect(() => {
    setPending(null);
    dragging.current = false;
  }, [tool]);

  const proxyUrl = props.proxyUrl;
  const cleanPlateUrl = useAssetUrl(tool === "clean_plate" ? assetById(project, config.clean_plate_asset_id) : undefined);
  const active = tool !== "none" && tool !== "clean_plate";
  // a nudge on a tracked frame starts from the position shown there (like a click there)
  const nudge = (dx: number, dy: number) => useEditor.getState().nudge(dx, dy, [pw, ph], current);

  return (
    <div ref={area} data-region="stage" className="relative flex min-h-[200px] flex-1 items-center justify-center overflow-hidden bg-stage">
      <div ref={frameBox} className="relative shrink-0 bg-black" style={{ width: box[0], height: box[1],
        transform: `translate(${zoom.x}px, ${zoom.y}px) scale(${zoom.k})` }}>
        {proxyUrl && (
          // decoded for the canvas; kept rendered (invisible) so frame callbacks keep firing
          <video ref={setVideo} src={proxyUrl} muted playsInline preload="auto" crossOrigin="anonymous" aria-hidden
            className="pointer-events-none absolute inset-0 h-full w-full opacity-0" />
        )}
        <canvas ref={canvas} width={pw} height={ph} className="absolute inset-0 h-full w-full" aria-label={t("stageLabel")} role="img" />
        {tool === "mask" && <canvas ref={brush} width={pw} height={ph} className="pointer-events-none absolute inset-0 h-full w-full opacity-50" />}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        {tool === "clean_plate" && cleanPlateUrl && <img src={cleanPlateUrl} alt={t("cleanPlate")} className="pointer-events-none absolute inset-0 h-full w-full object-cover opacity-50" />}
        {box[0] > 0 && (
          <StageOverlay w={box[0]} h={box[1]} trajectory={trajectory} anchor={config.anchor}
            anchorHere={!tracked || frame === config.anchor.frame} current={tracked && frame !== config.anchor.frame ? current : null}
            corrections={corrections} frame={frame} points={points} pending={pending && tool !== "mask" ? pending.uv : null}
            labels={{ anchor: t("anchor") }} />
        )}
        <div className={cx("absolute inset-0", active ? (tool === "mask" ? "cursor-cell" : "cursor-crosshair") : "")}
          style={{ touchAction: "none" }} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
          onContextMenu={(e) => active && e.preventDefault()} data-region="stage-input" />
      </div>
      {pending && <Loupe source={canvas.current} uv={pending.uv} at={pending.at} box={[box[0] * zoom.k, box[1] * zoom.k]} />}

      <div className="pointer-events-none absolute inset-x-2 top-2 flex items-start justify-between gap-2">
        <span className="pointer-events-auto whitespace-nowrap rounded-full border border-border bg-app/90 px-2 py-0.5 text-[11px] font-medium text-muted" data-badge="draft">
          {t("draftPreview")}
        </span>
        <div className="pointer-events-auto"><ViewToggle hasMask={!!props.mask} /></div>
      </div>

      {zoom.k > 1 && (
        <button type="button" onClick={() => setZoom({ k: 1, x: 0, y: 0 })} aria-label={t("resetZoom")}
          className="absolute right-2 top-16 flex h-[44px] w-[44px] items-center justify-center rounded-button border border-border bg-app/90 text-ink md:top-12">
          <ZoomOut className="h-4 w-4" aria-hidden />
        </button>
      )}

      <div className="pointer-events-none absolute inset-x-2 bottom-2 flex flex-col items-start gap-2">
        {shownNote && (
          <p role="status" className="pointer-events-auto flex max-w-full items-start gap-2 rounded-button border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-warning">
            <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <span>{noteText(shownNote, t)}</span>
            {note && (
              <button type="button" onClick={() => useEditor.getState().clearNote()} aria-label={t("dismiss")} className="-m-1 p-1 text-muted hover:text-ink">
                <X className="h-3.5 w-3.5" aria-hidden />
              </button>
            )}
          </p>
        )}
        {tool === "mask" && (
          <div className="pointer-events-auto flex flex-wrap items-center gap-2 rounded-panel border border-border bg-panel/95 px-2 py-1.5" data-region="brush">
            <PlusMinus label={pointLabel} addText={t("brushAdd")} removeText={t("brushErase")} />
            <label className="flex items-center gap-1.5 text-xs text-muted">
              {t("brushSize")}
              <input type="range" min={2} max={48} value={brushSize} onChange={(e) => setBrushSize(Number(e.target.value))} className="h-[44px] w-20 accent-[#FF9A52] md:h-auto" />
            </label>
            <Button size="sm" variant="primary" disabled={!brushDirty || applying} onClick={() => void applyBrush()}
              icon={applying ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Check className="h-4 w-4" aria-hidden />}>
              {t("brushApply", { frame: frame + 1 })}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => useEditor.getState().setTool("none")}>{t("cancel")}</Button>
            {brushError && <span role="alert" className="text-xs text-danger">{brushError}</span>}
          </div>
        )}
        {tool === "subject" && (
          <div className="pointer-events-auto flex items-center gap-2 rounded-panel border border-border bg-panel/95 px-2 py-1.5">
            <PlusMinus label={pointLabel} addText={t("pointAdd")} removeText={t("pointRemove")} />
            {desktop && <span className="text-xs text-muted">{t("altHint")}</span>}
          </div>
        )}
      </div>

      {!desktop && tool === "anchor" && (
        <div className="absolute bottom-2 right-2 grid grid-cols-3 gap-1" role="group" aria-label={t("fineAdjust")} data-region="nudge">
          <span />
          <NudgeButton label={t("nudgeUp")} onClick={() => nudge(0, -1)}><ArrowUp className="h-4 w-4" aria-hidden /></NudgeButton>
          <span />
          <NudgeButton label={t("nudgeLeft")} onClick={() => nudge(-1, 0)}><ArrowLeft className="h-4 w-4" aria-hidden /></NudgeButton>
          <span />
          <NudgeButton label={t("nudgeRight")} onClick={() => nudge(1, 0)}><ArrowRight className="h-4 w-4" aria-hidden /></NudgeButton>
          <span />
          <NudgeButton label={t("nudgeDown")} onClick={() => nudge(0, 1)}><ArrowDown className="h-4 w-4" aria-hidden /></NudgeButton>
          <span />
        </div>
      )}
    </div>
  );
}

type T = ReturnType<typeof useI18n>["t"];

function noteText(n: EditorNote, t: T): string {
  if (n.code === "outside_window") return t("noteOutsideWindow", { frame: n.frame + 1, window: n.window });
  return t(`note_${n.code}`);
}

function PlusMinus({ label, addText, removeText }: { label: 0 | 1; addText: string; removeText: string }) {
  const set = useEditor.getState().setPointLabel;
  return (
    <div role="radiogroup" aria-label={`${addText} / ${removeText}`} className="flex rounded-button border border-border bg-app p-0.5">
      {([[1, addText, Plus], [0, removeText, Minus]] as const).map(([v, text, Icon]) => (
        <button key={v} type="button" role="radio" aria-checked={label === v} onClick={() => set(v)}
          className={cx("flex min-h-[44px] items-center gap-1 rounded-[6px] px-2 text-xs md:min-h-7",
            label === v ? "bg-panel font-medium text-ink" : "text-muted hover:text-ink")}>
          <Icon className="h-3.5 w-3.5" aria-hidden />{text}
        </button>
      ))}
    </div>
  );
}

function NudgeButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick}
      className="flex h-[44px] w-[44px] items-center justify-center rounded-button border border-border bg-app/90 text-ink active:bg-panel">
      {children}
    </button>
  );
}
