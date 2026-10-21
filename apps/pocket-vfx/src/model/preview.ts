/**
 * Client proxy preview ("Draft preview"): what to draw for one frame (`planPreview`, pure and parity-tested on
 * radius/alpha) and a Canvas 2D renderer for it (`PreviewRenderer`). The effect layer is built with additive
 * ('lighter') blending, cut by the subject mask when the layer order puts it behind the subject, and added over the
 * proxy frame. A generated plate (raw_plate) is layered when present; the bundled plates are not served to the
 * browser, so a Quick preview shows the procedural layer only. Nothing here calls the server: scale, timing,
 * intensity, palette and rotation edits redraw immediately.
 */
import type { TrackJson, VfxLayerOrder, VfxPalette, VfxPresetId } from "@oc/contracts";
import { auraEnvelope, impactCurve, impactGlow, PLATE_GAIN, PLATE_SIDE, plateEnvelope, rampColor, SPLIT_GLOW_ALPHA } from "./procedural";
import { PlateLengthError, plateIndices, type EffectWindow } from "./timeline";

export interface PreviewEffect {
  preset_id: VfxPresetId;
  scale: number;
  intensity: number;
  squash: number;
  rotation_deg: number;
  palette: VfxPalette;
  layer_order: VfxLayerOrder;
}

export interface PreviewInput {
  window: EffectWindow;
  frame: number;
  effect: PreviewEffect;
  /** canvas size in px (the proxy size) */
  width: number;
  height: number;
  /** effect anchor at this frame in canvas px; null = hidden (invalid track frame) */
  anchor: [number, number] | null;
  /** a subject mask frame is available */
  mask: boolean;
  /** generated plate frame count (null = no plate in the preview) */
  plate: { frames: number; loopable?: boolean } | null;
}

export interface PreviewPlan {
  glow: { x: number; y: number; radius: number; alpha: number; heat: number } | null;
  ring: { x: number; y: number; radius: number; alpha: number; s: number; width: number; squash: number; rotation: number } | null;
  /** Energy Aura: anchor glow (when anchored) and the mask contour band (when a mask exists) */
  aura: { anchor: [number, number] | null; radius: number; alpha: number; band: boolean; size: number } | null;
  plate: { index: number; gain: number; side: number; x: number; y: number } | null;
  /** cut the effect by the subject mask (behind_subject / split_glow with a mask) */
  occlude: boolean;
  /** alpha of the unoccluded front glow (split_glow) */
  frontGlow: number;
}

/** Effect anchor (uv01) at `frame` (port of track.anchor_positions): fixed when not following or untracked. */
export function anchorAt(
  track: Pick<TrackJson, "points"> | null,
  anchor: { x: number; y: number; follow: boolean },
  frame: number,
  policy: "hide" | "freeze",
): [number, number] | null {
  if (!track || !anchor.follow) return [anchor.x, anchor.y];
  let last: [number, number] | null = null;
  for (const p of track.points) {
    if (p.frame > frame) break;
    const pos: [number, number] | null = p.valid && p.x !== null && p.y !== null ? [p.x, p.y] : null;
    if (p.frame === frame) return pos ?? (policy === "freeze" ? last : null);
    if (pos) last = pos;
  }
  return policy === "freeze" ? last : null;
}

export function planPreview(i: PreviewInput): PreviewPlan {
  const { window: w, frame, effect: e, anchor } = i;
  const short = Math.min(i.width, i.height);
  const params = { scale: e.scale, intensity: e.intensity };
  const occlude = i.mask && e.layer_order !== "in_front";
  const plan: PreviewPlan = { glow: null, ring: null, aura: null, plate: null, occlude, frontGlow: occlude && e.layer_order === "split_glow" ? SPLIT_GLOW_ALPHA : 0 };
  if (e.preset_id === "impact_wave_v1" && anchor) {
    const g = impactGlow(w, frame, params, short);
    if (g) plan.glow = { x: anchor[0], y: anchor[1], radius: g[0], alpha: g[1], heat: g[2] };
    const c = impactCurve(w, frame, params, short);
    if (c) {
      const s = Math.min((frame - w.peak) / Math.max(w.end - w.peak, 1), 1);
      plan.ring = { x: anchor[0], y: anchor[1], radius: c[0], alpha: c[1], s, width: 0.04 * short * e.scale, squash: e.squash,
        rotation: (e.rotation_deg * Math.PI) / 180 };
    }
  } else if (e.preset_id === "energy_aura_v1" && (anchor || i.mask)) {
    const env = auraEnvelope(w, frame);
    const size = short * e.scale;
    if (env > 0) plan.aura = { anchor, radius: 0.12 * size, alpha: e.intensity * env, band: i.mask, size };
  }
  if (i.plate && anchor && e.preset_id !== "blink_out_v1") {
    const gain = e.intensity * plateEnvelope(w, frame) * PLATE_GAIN;
    if (gain > 0) {
      try {
        const idx = plateIndices(i.plate.frames, w.end - w.start + 1, { loopable: !!i.plate.loopable, seamOk: !!i.plate.loopable });
        plan.plate = { index: idx[frame - w.start], gain, side: PLATE_SIDE * short * e.scale, x: anchor[0], y: anchor[1] };
      } catch (err) {
        if (!(err instanceof PlateLengthError)) throw err;
      }
    }
  }
  return plan;
}

// ---------------------------------------------------------------------------------------------- Canvas 2D drawing

export type PreviewView = "source" | "mask" | "matte" | "composite";

export interface PreviewLayers {
  /** current video frame */
  source: CanvasImageSource | null;
  /** subject mask of this frame (alpha = mask) */
  mask: CanvasImageSource | null;
  /** plate frame `plan.plate.index` (RGB on black) */
  plate: CanvasImageSource | null;
}

const MASK_TINT = "rgba(221,139,255,0.5)";
const STOPS = 24;
const rgba = ([r, g, b]: [number, number, number], a: number) => `rgba(${r},${g},${b},${Math.min(Math.max(a, 0), 1).toFixed(4)})`;

function canvas(w: number, h: number): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return c;
}

function ctx2d(c: HTMLCanvasElement): CanvasRenderingContext2D {
  const x = c.getContext("2d");
  if (!x) throw new Error("2D canvas unavailable");
  return x;
}

/** Ground ring: sharp leading edge, soft trailing edge, faint halo (procedural._ring) as a radial gradient. */
function drawRing(x: CanvasRenderingContext2D, r: NonNullable<PreviewPlan["ring"]>, palette: VfxPalette) {
  if (r.alpha <= 0 || r.radius <= 0) return;
  const sigOut = 0.35 * r.width, sigIn = 0.85 * r.width, sigHalo = 1.5 * r.width;
  const r0 = Math.max(0, r.radius - 3 * sigHalo), r1 = r.radius + 3 * sigHalo;
  x.save();
  x.translate(r.x, r.y);
  x.rotate(r.rotation); // y down: positive = clockwise on screen, like the procedural layer
  x.scale(1, r.squash);
  const g = x.createRadialGradient(0, 0, r0, 0, 0, r1);
  for (let k = 0; k <= STOPS; k++) {
    const d = r0 + ((r1 - r0) * k) / STOPS - r.radius;
    const crest = Math.exp(-((d / (d > 0 ? sigOut : sigIn)) ** 2));
    const halo = Math.exp(-((d / sigHalo) ** 2));
    const heat = crest ** 1.5 * (0.35 + 0.65 * (1 - r.s));
    g.addColorStop(k / STOPS, rgba(rampColor(palette, heat), r.alpha * (crest + 0.18 * halo)));
  }
  x.fillStyle = g;
  x.beginPath();
  x.arc(0, 0, r1, 0, 2 * Math.PI);
  x.fill();
  x.restore();
}

/** Round emissive glow (Gaussian body + tight core); `body`/`core` weights and heat per procedural._glow/_aura_local. */
function drawGlow(x: CanvasRenderingContext2D, cx: number, cy: number, radius: number, alpha: number, heatAmp: number, palette: VfxPalette,
  body: number, core: number) {
  if (alpha <= 0 || radius <= 0) return;
  const g = x.createRadialGradient(cx, cy, 0, cx, cy, 3 * radius);
  for (let k = 0; k <= STOPS; k++) {
    const d2 = ((3 * k) / STOPS) ** 2;
    const gb = Math.exp(-d2), gc = Math.exp(-9 * d2);
    g.addColorStop(k / STOPS, rgba(rampColor(palette, heatAmp * (0.5 * gb + 0.5 * gc)), alpha * (body * gb + core * gc)));
  }
  x.fillStyle = g;
  x.fillRect(cx - 3 * radius, cy - 3 * radius, 6 * radius, 6 * radius);
}

/** Cover-fit `img` into the square `side` centred on (x, y). */
function drawCover(x: CanvasRenderingContext2D, img: CanvasImageSource & { width: number; height: number }, cx: number, cy: number, side: number) {
  const k = side / Math.min(img.width, img.height);
  const w = img.width * k, h = img.height * k;
  x.save();
  x.beginPath();
  x.rect(cx - side / 2, cy - side / 2, side, side);
  x.clip();
  x.drawImage(img, cx - w / 2, cy - h / 2, w, h);
  x.restore();
}

/** Draws one preview frame into a visible canvas, reusing its offscreen layers. */
export class PreviewRenderer {
  private effect: HTMLCanvasElement | null = null;
  private scratch: HTMLCanvasElement | null = null;

  private layer(which: "effect" | "scratch", w: number, h: number) {
    let c = this[which];
    if (!c || c.width !== w || c.height !== h) c = this[which] = canvas(w, h);
    const x = ctx2d(c);
    x.globalCompositeOperation = "source-over";
    x.globalAlpha = 1;
    x.filter = "none";
    x.clearRect(0, 0, w, h);
    return { c, x };
  }

  /** The effect layer (premultiplied, transparent background) of `plan`; mask-cut when `cut`. */
  private drawEffect(plan: PreviewPlan, palette: VfxPalette, layers: PreviewLayers, w: number, h: number, cut: boolean) {
    const { c, x } = this.layer("effect", w, h);
    x.globalCompositeOperation = "lighter";
    const plate = layers.plate as (CanvasImageSource & { width: number; height: number }) | null;
    if (plan.plate && plate) {
      x.globalAlpha = Math.min(plan.plate.gain, 1);
      drawCover(x, plate, plan.plate.x, plan.plate.y, plan.plate.side);
      x.globalAlpha = 1;
    }
    if (plan.glow) drawGlow(x, plan.glow.x, plan.glow.y, plan.glow.radius, plan.glow.alpha, plan.glow.heat, palette, 0.8, 0.2);
    if (plan.ring) drawRing(x, plan.ring, palette);
    if (plan.aura) {
      const a = plan.aura;
      if (a.band && layers.mask) {
        // contour band: the silhouette blurred outward; the inside is removed below
        const s = this.layer("scratch", w, h);
        s.x.fillStyle = rgba(rampColor(palette, 0.6), 1);
        s.x.fillRect(0, 0, w, h);
        s.x.globalCompositeOperation = "destination-in";
        s.x.drawImage(layers.mask, 0, 0, w, h);
        x.filter = `blur(${Math.max(1, 0.03 * a.size).toFixed(1)}px)`;
        x.globalAlpha = Math.min(1, 0.85 * a.alpha * 1.6);
        x.drawImage(s.c, 0, 0);
        x.filter = "none";
        x.globalAlpha = 1;
      }
      if (a.anchor) drawGlow(x, a.anchor[0], a.anchor[1], a.radius, a.alpha, 1, palette, 0.75 * 0.775, 0.35);
      if (layers.mask) cut = true; // the aura never fills the silhouette (procedural._mask_out)
    }
    if (cut && layers.mask) {
      x.globalCompositeOperation = "destination-out";
      x.drawImage(layers.mask, 0, 0, w, h);
    }
    return c;
  }

  draw(target: HTMLCanvasElement, plan: PreviewPlan | null, palette: VfxPalette, layers: PreviewLayers, view: PreviewView) {
    const w = target.width, h = target.height;
    const x = ctx2d(target);
    x.globalCompositeOperation = "source-over";
    x.globalAlpha = 1;
    if (view === "matte") {
      x.fillStyle = "#000";
      x.fillRect(0, 0, w, h);
    } else if (layers.source) x.drawImage(layers.source, 0, 0, w, h);
    else x.clearRect(0, 0, w, h);

    if (view === "mask") {
      if (!layers.mask) return;
      const s = this.layer("scratch", w, h);
      s.x.fillStyle = MASK_TINT;
      s.x.fillRect(0, 0, w, h);
      s.x.globalCompositeOperation = "destination-in";
      s.x.drawImage(layers.mask, 0, 0, w, h);
      x.drawImage(s.c, 0, 0);
      return;
    }
    if (view === "source" || !plan) return;
    const occlude = plan.occlude && !!layers.mask;
    if (view === "composite" && occlude && plan.frontGlow > 0) {
      x.globalCompositeOperation = "lighter";
      x.globalAlpha = plan.frontGlow;
      x.drawImage(this.drawEffect(plan, palette, layers, w, h, false), 0, 0);
      x.globalAlpha = 1;
    }
    const fx = this.drawEffect(plan, palette, layers, w, h, occlude);
    if (view === "matte") {
      // effect alpha as gray: the layer turned white, keeping its alpha
      const fxc = ctx2d(fx);
      fxc.globalCompositeOperation = "source-in";
      fxc.fillStyle = "#fff";
      fxc.fillRect(0, 0, w, h);
      x.globalCompositeOperation = "source-over";
      x.drawImage(fx, 0, 0);
      return;
    }
    x.globalCompositeOperation = "lighter";
    x.drawImage(fx, 0, 0);
    x.globalCompositeOperation = "source-over";
  }
}
