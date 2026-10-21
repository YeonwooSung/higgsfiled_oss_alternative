"use client";

/**
 * Frame-accurate video in the editor.
 * - `useProxyPlayback`: the work proxy `<video>` drives the current frame. Each presented frame is reported through
 *   `requestVideoFrameCallback` (its `mediaTime` mapped with the single frame rule), or a rAF poll of `currentTime`
 *   where rVFC is missing. Store `playing` plays/pauses it; a store frame change while paused seeks to the frame
 *   centre `(i + 0.5) / 24`.
 * - `useDecodedFrames`: the mask preview (gray, turned into alpha) or a plate video decoded once into small bitmaps,
 *   so the preview can draw any frame without seeking a second video in step with the first.
 */
import { useEffect, useRef, useState } from "react";
import { frameOfCurrentTime, frameOfMediaTime, seekTime } from "../model/frames";
import { useEditor } from "../state/editorStore";

type FrameCallback = (now: number, meta: { mediaTime: number }) => void;
/** rVFC is missing in some browsers (older Firefox): typed optional here. */
type RvfcVideo = Omit<HTMLVideoElement, "requestVideoFrameCallback" | "cancelVideoFrameCallback"> & {
  requestVideoFrameCallback?: (cb: FrameCallback) => number;
  cancelVideoFrameCallback?: (id: number) => void;
};

/** Follows the proxy video; `onFrame(frame)` runs for every presented frame (playback, seek, first load). */
export function useProxyPlayback(video: HTMLVideoElement | null, n: number, onFrame: (frame: number) => void) {
  const cb = useRef(onFrame);
  cb.current = onFrame;
  const presented = useRef(-1);

  useEffect(() => {
    const v = video as RvfcVideo | null;
    if (!v || n < 1) return;
    let stop = false;
    const report = (frame: number) => {
      presented.current = frame;
      const s = useEditor.getState();
      if (s.frame !== frame && !v.paused) s.setFrame(frame);
      cb.current(frame);
    };
    let handle = 0;
    const request = v.requestVideoFrameCallback?.bind(v);
    const rvfc = !!request;
    if (request) {
      const tick: FrameCallback = (_now, meta) => {
        if (stop) return;
        report(frameOfMediaTime(meta.mediaTime, n));
        handle = request(tick);
      };
      handle = request(tick);
    } else {
      let last = -1;
      const poll = () => {
        if (stop) return;
        const f = frameOfCurrentTime(v.currentTime, n);
        if (f !== last && v.readyState >= 2) {
          last = f;
          report(f);
        }
        handle = requestAnimationFrame(poll);
      };
      handle = requestAnimationFrame(poll);
    }
    // a seek that lands on the frame already shown presents nothing new: redraw anyway
    const onSeeked = () => cb.current(frameOfCurrentTime(v.currentTime, n));
    const onEnded = () => useEditor.getState().setPlaying(false);
    const onLoaded = () => {
      v.currentTime = seekTime(useEditor.getState().frame);
    };
    v.addEventListener("seeked", onSeeked);
    v.addEventListener("ended", onEnded);
    v.addEventListener("loadeddata", onLoaded);
    if (v.readyState >= 2) onLoaded();

    const unsubscribe = useEditor.subscribe((s, prev) => {
      if (s.playing !== prev.playing) {
        if (s.playing) {
          if (s.frame >= n - 1) v.currentTime = seekTime(0);
          void v.play().catch(() => useEditor.getState().setPlaying(false));
        } else {
          v.pause();
          // settle exactly on the shown frame
          v.currentTime = seekTime(s.frame);
        }
      } else if (!s.playing && s.frame !== prev.frame && s.frame !== presented.current) {
        v.currentTime = seekTime(s.frame);
      }
    });
    return () => {
      stop = true;
      unsubscribe();
      if (rvfc) v.cancelVideoFrameCallback?.(handle);
      else cancelAnimationFrame(handle);
      v.removeEventListener("seeked", onSeeked);
      v.removeEventListener("ended", onEnded);
      v.removeEventListener("loadeddata", onLoaded);
    };
  }, [video, n]);
}

export interface FrameSequence {
  /** bitmap per frame (null until decoded) */
  frames: (ImageBitmap | null)[];
  loaded: number;
  total: number;
}

function seek(v: HTMLVideoElement, t: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => done(new Error("seek timeout")), 5000);
    const ok = () => done();
    const fail = () => done(new Error("video error"));
    function done(e?: Error) {
      clearTimeout(timer);
      v.removeEventListener("seeked", ok);
      v.removeEventListener("error", fail);
      if (e) reject(e);
      else resolve();
    }
    v.addEventListener("seeked", ok);
    v.addEventListener("error", fail);
    v.currentTime = t;
  });
}

/**
 * Decodes `count` frames of `url` at `fps` into bitmaps no larger than `maxSide`. `kind: "mask"` turns the gray
 * mask preview into an alpha mask (white, alpha = gray) for cutting / tinting.
 */
export function useDecodedFrames(url: string | undefined, count: number, fps: number, kind: "mask" | "plate", maxSide: number): FrameSequence | null {
  const [seq, setSeq] = useState<FrameSequence | null>(null);
  useEffect(() => {
    if (!url || count < 1) {
      setSeq(null);
      return;
    }
    let cancelled = false;
    const frames: (ImageBitmap | null)[] = Array.from({ length: count }, () => null);
    const v = document.createElement("video");
    v.muted = true;
    v.playsInline = true;
    v.preload = "auto";
    v.crossOrigin = "anonymous"; // pixels are read back (mask -> alpha)
    v.src = url;
    setSeq({ frames, loaded: 0, total: count });
    (async () => {
      await new Promise<void>((resolve, reject) => {
        if (v.readyState >= 1) return resolve();
        v.addEventListener("loadedmetadata", () => resolve(), { once: true });
        v.addEventListener("error", () => reject(new Error("video error")), { once: true });
      });
      const k = Math.min(1, maxSide / Math.max(v.videoWidth, v.videoHeight));
      const w = Math.max(1, Math.round(v.videoWidth * k)), h = Math.max(1, Math.round(v.videoHeight * k));
      const c = document.createElement("canvas");
      c.width = w;
      c.height = h;
      const x = c.getContext("2d", { willReadFrequently: kind === "mask" })!;
      for (let i = 0; i < count && !cancelled; i++) {
        await seek(v, (i + 0.5) / fps);
        if (cancelled) break;
        x.clearRect(0, 0, w, h);
        x.drawImage(v, 0, 0, w, h);
        let bmp: ImageBitmap;
        if (kind === "mask") {
          const img = x.getImageData(0, 0, w, h);
          const d = img.data;
          for (let p = 0; p < d.length; p += 4) {
            d[p + 3] = d[p];
            d[p] = d[p + 1] = d[p + 2] = 255;
          }
          bmp = await createImageBitmap(img);
        } else bmp = await createImageBitmap(c);
        if (cancelled) {
          bmp.close();
          break;
        }
        frames[i] = bmp;
        if (i % 8 === 7 || i === count - 1) setSeq({ frames, loaded: i + 1, total: count });
      }
    })().catch(() => {
      // a preview layer that cannot be decoded is left out of the draft preview
    });
    return () => {
      cancelled = true;
      v.removeAttribute("src");
      v.load();
      for (const b of frames) b?.close();
    };
  }, [url, count, fps, kind, maxSide]);
  return seq;
}
