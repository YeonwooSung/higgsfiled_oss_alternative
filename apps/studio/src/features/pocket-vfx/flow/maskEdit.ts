/**
 * Refine-mask output: the brushed frame as an 8-bit gray PNG (white = subject), uploaded with role `mask_edit`.
 * Gray, not RGBA: the inspect step reads a PNG's alpha when it has one while the masks stage reads luminance, so an
 * opaque gray image means the same to both.
 */
import { encode } from "fast-png";
import { api } from "../api/client";

/** Binary gray PNG of an RGBA canvas read-back whose alpha is the mask. */
export function maskEditPng(rgba: Uint8ClampedArray, width: number, height: number): Uint8Array {
  const gray = new Uint8Array(width * height);
  for (let i = 0; i < gray.length; i++) gray[i] = rgba[i * 4 + 3] >= 128 ? 255 : 0;
  return encode({ width, height, data: gray, channels: 1, depth: 8 });
}

/** Uploads the edit of `frame`; returns the new asset id for `config.mask_edits`. */
export async function uploadMaskEdit(projectId: string, frame: number, png: Uint8Array): Promise<string> {
  const blob = new Blob([png.slice().buffer as ArrayBuffer], { type: "image/png" });
  const { asset } = await api.uploadAsset(projectId, "mask_edit", blob, `mask_edit_${frame}.png`);
  return asset.id;
}
