import type { PocketVfxProject } from "@oc/contracts";
import { api } from "../api/client";

/** Upload role `clip` (PocketVfxApp.upload_roles / upload_limits). */
export const CLIP_MIMES = ["video/mp4", "video/quicktime", "video/webm"] as const;
export const CLIP_MAX_BYTES = 100 * 1024 * 1024;
export const ACCEPT = CLIP_MIMES.join(",");

/** The bundled sample clips (public/samples, MIT, silent). They go through the normal upload path. */
export const SAMPLES = [
  { id: "stomp", url: "/samples/stomp_landscape.mp4", titleKey: "sampleStomp", ratio: "16 / 9" },
  { id: "portrait", url: "/samples/rotated_portrait.mp4", titleKey: "samplePortrait", ratio: "9 / 16" },
] as const;
export type SampleId = (typeof SAMPLES)[number]["id"];

/** Client-side pre-check (the server checks again): i18n key of the problem, or null. */
export function clipFileProblem(file: Pick<File, "type" | "size">): "fileTypeError" | "fileSizeError" | null {
  if (!(CLIP_MIMES as readonly string[]).includes(file.type)) return "fileTypeError";
  if (file.size > CLIP_MAX_BYTES) return "fileSizeError";
  return null;
}

/**
 * One upload path for user clips and bundled samples: session -> project -> asset (role `clip`). The server's
 * `asset.inspect` then probes it and makes the `clip_preview` / `clip_filmstrip` the trim screen shows.
 * `uploadAsset` reads the whole blob for its sha256, acceptable at <= 100 MB.
 */
export async function startProjectWithClip(file: File, title: string): Promise<PocketVfxProject> {
  await api.startSession();
  const project = await api.createProject(title.slice(0, 120) || "Untitled");
  await api.uploadAsset(project.id, "clip", file, file.name);
  return project;
}

export async function fetchSample(url: string): Promise<File> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`sample ${url} not found`);
  const name = url.split("/").pop() ?? "sample.mp4";
  return new File([await res.blob()], name, { type: "video/mp4" });
}
