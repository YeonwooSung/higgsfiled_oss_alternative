"use client";

import type { CameraPreset, CompileResponse } from "@oc/contracts";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "../api/client";
import { compilePathProvisional, PRESETS_V1, sceneInfoFromTarget } from "../model/path";
import { outputSize } from "../model/preview";
import { motionSuggestion, scaleSketchPoints } from "../model/sketch";
import { assetById, currentServerPath, renderGate, useEditor, type RenderGate } from "../state/editorStore";

/** `GET /camera-sketch/presets` (bounds, defaults, labels); the bundled v1 mirror until it arrives. */
export function usePresets(): CameraPreset[] {
  const q = useQuery({ queryKey: ["camera-presets"], queryFn: () => api.presets(), staleTime: Infinity });
  return q.data?.presets?.length ? q.data.presets : PRESETS_V1;
}

/**
 * The camera path shown in the stage and gizmo: the server compile for the current revision when it
 * has arrived, else the browser's provisional compile of the same config (badge "Provisional").
 * Also the reduced-motion proposal (compiler `suggested_params`, or halved motion when holes > 8 %)
 * and its provisional path for the before/after overlay.
 */
export function useCameraPath() {
  const project = useEditor((s) => s.project);
  const config = useEditor((s) => s.config);
  const serverPath = useEditor(currentServerPath);
  const serverCompile = useEditor((s) => s.serverCompile);
  const presets = usePresets();
  const scene = assetById(project, config?.scene_asset_id);
  const [outW, outH] = outputSize(config?.output.aspect ?? "16:9");
  const pathCfg = config?.path;
  const u = config?.target.u, v = config?.target.v, depthScale = config?.target.depth_scale;
  const focalMm = config?.camera.focal_length_mm_equiv, sensorWMm = config?.camera.sensor_width_mm_assumed;
  const srcW = scene?.width, srcH = scene?.height;
  const info = useMemo(() => (srcW && srcH && u !== undefined && v !== undefined && depthScale !== undefined && focalMm !== undefined
    ? sceneInfoFromTarget({ focalMm, srcW, srcH, u, v, depthScale, sensorWMm })
    : null), [srcW, srcH, u, v, depthScale, focalMm, sensorWMm]);
  const provisional = useMemo(() => (info && pathCfg ? compilePathProvisional(pathCfg, info, outW, outH, { presets }) : null), [pathCfg, info, outW, outH, presets]);
  const path = serverPath ?? provisional;
  const response: CompileResponse | null = serverPath && serverCompile ? serverCompile.response : null;
  const holeRatio = response?.hole_ratio ?? null;
  const suggestion = useMemo(() => motionSuggestion(path, holeRatio, pathCfg?.sketch_points ?? []), [path, holeRatio, pathCfg]);
  const suggestedPath = useMemo(() => {
    if (!suggestion || !info || !pathCfg) return null;
    const sketch = suggestion.sketchScale !== 1 && pathCfg.sketch_points.length ? scaleSketchPoints(pathCfg.sketch_points, suggestion.sketchScale) : pathCfg.sketch_points;
    return compilePathProvisional({ ...pathCfg, ...suggestion.params, sketch_points: sketch }, info, outW, outH, { presets, suggest: false });
  }, [suggestion, info, pathCfg, outW, outH, presets]);
  return { info, path, provisional: !serverPath, response, holeRatio, suggestion, suggestedPath, outW, outH, srcW, srcH };
}

export type CameraPathState = ReturnType<typeof useCameraPath>;

/** `canRender` / `blockReasons` for the Render buttons (Task 16). */
export function useRenderGate(): RenderGate {
  const serverCompile = useEditor((s) => s.serverCompile);
  const revision = useEditor((s) => s.revision);
  const saveState = useEditor((s) => s.saveState);
  const config = useEditor((s) => s.config);
  const compileError = useEditor((s) => s.compileError);
  return useMemo(() => renderGate({ serverCompile, revision, saveState, config, compileError }), [serverCompile, revision, saveState, config, compileError]);
}
