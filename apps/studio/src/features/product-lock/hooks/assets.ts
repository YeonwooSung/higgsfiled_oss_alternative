"use client";

import { useEffect, useState } from "react";
import { MaskBuffer } from "../model/mask";

/** One shared, expiry-safe hook for signed asset URLs (see shell/assetUrl). */
export { useAssetUrl } from "../../../shell/assetUrl";

export function useImage(url: string | undefined): HTMLImageElement | null {
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  useEffect(() => {
    if (!url) {
      setImg(null);
      return;
    }
    let alive = true;
    const el = new Image();
    el.crossOrigin = "anonymous";
    el.onload = () => alive && setImg(el);
    el.src = url;
    return () => {
      alive = false;
    };
  }, [url]);
  return img;
}

export function readPixels(img: HTMLImageElement): ImageData {
  const c = document.createElement("canvas");
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  const ctx = c.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(img, 0, 0);
  return ctx.getImageData(0, 0, c.width, c.height);
}

export function maskFromImage(img: HTMLImageElement): MaskBuffer {
  const px = readPixels(img);
  return MaskBuffer.fromLuma(px.width, px.height, px.data);
}

/** Product pixels with the mask as alpha, for the CPU-only placement preview. */
export function cutoutCanvas(product: HTMLImageElement, mask: MaskBuffer | null): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = product.naturalWidth;
  c.height = product.naturalHeight;
  const ctx = c.getContext("2d")!;
  ctx.drawImage(product, 0, 0);
  if (mask && mask.width === c.width && mask.height === c.height) {
    const px = ctx.getImageData(0, 0, c.width, c.height);
    for (let i = 0; i < mask.data.length; i++) px.data[i * 4 + 3] = mask.data[i];
    ctx.putImageData(px, 0, 0);
  }
  return c;
}

export async function maskToPngBlob(mask: MaskBuffer): Promise<Blob> {
  const c = document.createElement("canvas");
  c.width = mask.width;
  c.height = mask.height;
  c.getContext("2d")!.putImageData(new ImageData(mask.toRGBA(), mask.width, mask.height), 0, 0);
  return new Promise((resolve, reject) => c.toBlob((b) => (b ? resolve(b) : reject(new Error("encode failed"))), "image/png"));
}
