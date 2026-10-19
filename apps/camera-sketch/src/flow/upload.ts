import type { CameraSketchProject } from "@oc/contracts";
import { api } from "../api/client";

/** One upload path for user files and bundled samples: session -> project -> asset (role `scene`). */
export async function startProjectWithScene(file: File, title: string): Promise<CameraSketchProject> {
  await api.startSession();
  const project = await api.createProject(title.slice(0, 120) || "Untitled");
  await api.uploadAsset(project.id, "scene", file, file.name);
  return project;
}

export async function fetchSample(url: string): Promise<File> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`sample ${url} not found`);
  const name = url.split("/").pop() ?? "sample.png";
  return new File([await res.blob()], name, { type: "image/png" });
}
