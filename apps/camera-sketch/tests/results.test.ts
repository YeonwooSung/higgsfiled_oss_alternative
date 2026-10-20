import type { CameraSketchConfig, Job, MotionReport } from "@oc/contracts";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { en } from "../src/i18n/en";
import { ko } from "../src/i18n/ko";
import {
  canonicalGeometryJson, cinematicGuide, cinematicWarnings, EXPORT_FORMATS, exportFormats, geometryHash, geometryWarnings, guideWarningCodes, isPreviousVersion, progressPercent,
  pyFloatRepr, qaStatus, renderError, stageKey, targetTrajectory,
} from "../src/model/results";

const fixture = JSON.parse(readFileSync(fileURLToPath(new URL("../../../fixtures/camera-sketch/geometry_hash.v1.json", import.meta.url)), "utf8")) as {
  cases: { name: string; config: CameraSketchConfig; geometry_hash: string }[];
};

describe("pyFloatRepr (Python float repr)", () => {
  it.each([
    [0, "0.0"], [-0, "-0.0"], [1, "1.0"], [50, "50.0"], [0.5, "0.5"], [-6, "-6.0"], [0.1 + 0.2, "0.30000000000000004"],
    [1e-5, "1e-05"], [-1e-5, "-1e-05"], [1.5e-7, "1.5e-07"], [0.0001, "0.0001"], [0.00012, "0.00012"],
    [1e16, "1e+16"], [1234567890123456.0, "1234567890123456.0"], [69.99999999999999, "69.99999999999999"], [123.456, "123.456"],
  ])("%s -> %s", (x, s) => expect(pyFloatRepr(x)).toBe(s));
});

describe("geometryHash parity with oc_api/camera_sketch/jobs.py::geometry_hash", () => {
  for (const c of fixture.cases) {
    it(c.name, async () => {
      expect(await geometryHash(c.config)).toBe(c.geometry_hash);
    });
  }
  it("ignores generation, mode, profile and show_holes; changes with geometry", async () => {
    const base = fixture.cases[1].config;
    const h = await geometryHash(base);
    const other = structuredClone(base);
    other.generation.seed = 1;
    other.generation.extra_prompt = "x";
    other.mode = "cinematic";
    other.model_profile = "x";
    other.scene.show_holes = !other.scene.show_holes;
    expect(await geometryHash(other)).toBe(h);
    other.path.push_ratio = 0.01;
    expect(await geometryHash(other)).not.toBe(h);
  });
  it("canonical json uses sorted keys, no spaces and Python float formatting", () => {
    const s = canonicalGeometryJson(fixture.cases[0].config);
    expect(s.startsWith('{"camera":{"focal_length_mm_equiv":50.0,"roll_deg":0,"sensor_width_mm_assumed":36}')).toBe(true);
    expect(s).toContain('"output":{"aspect":"16:9","duration_ms":5000,"fps":24}');
    expect(s).not.toContain(" ");
  });
});

const job = (over: Partial<Job>): Job => ({
  id: "j", project_id: "p", kind: "camera.guide", state: "succeeded", stage: null, progress: null, input_revision: 3, options: {},
  model_profile: "mock_v1", cancellation_requested: false, error: null, output_asset_ids: [], result: null,
  created_at: "2026-10-20T00:00:00Z", finished_at: null, ...over,
});

describe("qaStatus", () => {
  const rep = (status: MotionReport["status"], reasons: MotionReport["reasons"] = []): MotionReport => ({
    status, reasons, tracked_points: 10, target_drift_px: null, similarity_mean: null, similarity_window_min: null, similarity_min: null,
  });
  it("ok -> pass", () => expect(qaStatus(rep("ok"))).toEqual({ kind: "pass", titleKey: "qaPassed", reasonKeys: [] }));
  it("check -> subject change review with mapped reasons", () => {
    expect(qaStatus(rep("check", ["subject_change", "target_drift"]))).toEqual({
      kind: "check", titleKey: "qaCheck", reasonKeys: ["qa_subject_change", "qa_target_drift"],
    });
  });
  it("unknown never renders as pass, with its reason", () => {
    const s = qaStatus(rep("unknown", ["low_texture_generated"]));
    expect(s.kind).toBe("unknown");
    expect(s.titleKey).toBe("qaUnknown");
    expect(s.reasonKeys).toEqual(["qa_low_texture_generated"]);
  });
  it.each([null, undefined, {}, { status: "pass" }, { status: "ok", reasons: "x" }, { status: 1 }])("malformed %j -> unknown", (r) => {
    const s = qaStatus(r as never);
    expect(s.kind).toBe("unknown");
    expect(s.reasonKeys.length).toBeGreaterThan(0);
  });
  it("an unknown reason code maps to the generic unknown reason", () => {
    expect(qaStatus({ ...rep("unknown"), reasons: ["weird" as never] }).reasonKeys).toEqual(["qa_unknown"]);
    expect(qaStatus(rep("unknown")).reasonKeys).toEqual(["qa_unknown"]);
  });
  it("every QA key exists in both languages", () => {
    for (const k of ["qaPassed", "qaCheck", "qaUnknown", "qa_no_frames", "qa_length_mismatch", "qa_unsupported_frame", "qa_shape_mismatch",
      "qa_low_texture_generated", "qa_low_texture_geometry", "qa_tracking_lost_geometry", "qa_tracking_lost_generated", "qa_target_drift",
      "qa_subject_change", "qa_unknown"]) {
      expect(en).toHaveProperty(k);
      expect(ko).toHaveProperty(k);
    }
    expect(en.qaPassed.toLowerCase()).not.toContain("exact");
  });
});

describe("isPreviousVersion", () => {
  it("is true only when the job's input revision differs from the current revision", () => {
    expect(isPreviousVersion(job({ input_revision: 3 }), 3)).toBe(false);
    expect(isPreviousVersion(job({ input_revision: 3 }), 4)).toBe(true);
    expect(isPreviousVersion(undefined, 4)).toBe(false);
  });
});

describe("cinematicGuide (staleness)", () => {
  const g = (id: string, hash: string, created: string, state: Job["state"] = "succeeded", frames = 120) =>
    job({ id, created_at: created, state, result: state === "succeeded" ? { geometry_hash: hash, frame_count: frames } : null });
  it("needs a guide first", () => {
    expect(cinematicGuide({}, "h1")).toEqual({ guide: null, reason: "guide_missing" });
  });
  it("picks the newest succeeded guide with the current geometry hash", () => {
    const jobs = { a: g("a", "h1", "2026-01-01"), b: g("b", "h1", "2026-01-02"), c: g("c", "h2", "2026-01-03") };
    expect(cinematicGuide(jobs, "h1")).toEqual({ guide: jobs.b, reason: null });
  });
  it("reports a stale guide when no succeeded guide matches", () => {
    expect(cinematicGuide({ a: g("a", "old", "2026-01-01") }, "h1")).toEqual({ guide: null, reason: "guide_stale" });
  });
  it("ignores running/failed guides and guides with the wrong frame count", () => {
    expect(cinematicGuide({ a: g("a", "h1", "2026-01-01", "running") }, "h1").reason).toBe("guide_running");
    expect(cinematicGuide({ a: g("a", "h1", "2026-01-01", "failed") }, "h1").reason).toBe("guide_missing");
    expect(cinematicGuide({ a: g("a", "h1", "2026-01-01", "succeeded", 121) }, "h1").reason).toBe("frame_count_mismatch");
  });
  it("is pending while the current hash is not known yet", () => {
    expect(cinematicGuide({ a: g("a", "h1", "2026-01-01") }, null).reason).toBe("hash_pending");
  });
});

describe("export formats per kind", () => {
  it("guide: mp4, path json, guides zip, recipe zip; cinematic has no guides zip", () => {
    expect(exportFormats("camera.guide")).toEqual(["mp4", "path_json", "guides_zip", "recipe_zip"]);
    expect(exportFormats("camera.cinematic")).toEqual(["mp4", "path_json", "recipe_zip"]);
    expect(exportFormats("camera.analyze")).toEqual([]);
    expect(Object.keys(EXPORT_FORMATS).sort()).toEqual(["camera.cinematic", "camera.guide"]);
  });
});

describe("progress", () => {
  it("percent only when the API gives one, never 100 before success", () => {
    expect(progressPercent(job({ state: "running", progress: null }))).toBeNull();
    expect(progressPercent(job({ state: "running", progress: 0.42 }))).toBe(42);
    expect(progressPercent(job({ state: "running", progress: 1 }))).toBe(99);
    expect(progressPercent(job({ state: "succeeded", progress: 1 }))).toBeNull();
  });
  it("maps stage names to i18n keys with a generic fallback", () => {
    expect(stageKey("render")).toBe("stageName_render");
    expect(stageKey("generate")).toBe("stageName_generate");
    expect(stageKey("weird")).toBe("jobRunning");
    expect(stageKey(null)).toBe("jobRunning");
    for (const s of ["compile", "render", "encode", "prepare", "generate", "postprocess", "qa", "depth", "path_json", "guides_zip", "recipe"]) {
      expect(en).toHaveProperty(`stageName_${s}`);
    }
  });
});

describe("renderError", () => {
  it("maps cinematic 422/503 codes to explanations", () => {
    expect(renderError("guide_stale")).toBe("err_guide_stale");
    expect(renderError("frame_count_mismatch")).toBe("err_frame_count_mismatch");
    expect(renderError("guide_not_finished")).toBe("err_guide_not_finished");
    expect(renderError("profile_unavailable")).toBe("err_profile_unavailable");
    expect(renderError("other")).toBeNull();
  });
});

describe("targetTrajectory (path overlay)", () => {
  it("projects the target through every sampled pose (identity -> the cover-fit target pixel)", () => {
    const I = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
    const shifted = [[1, 0, 0, -0.1], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
    const K = [[1000, 0, 640], [0, 1000, 360], [0, 0, 1]];
    const art = {
      user_config: { target: { u: 0.5, v: 0.5, depth_scale: 1 } },
      compiled: {
        K, source_to_output: { scale: 1, offset_x: 0, offset_y: 0, out_w: 1280, out_h: 720, src_w: 1280, src_h: 720 },
        samples: [{ t_ms: 0, T_cw: I }, { t_ms: 1000, T_cw: shifted }],
      },
    };
    const tr = targetTrajectory(art)!;
    expect(tr).toHaveLength(2);
    expect(tr[0].uv![0]).toBeCloseTo(0.5, 9);
    expect(tr[0].uv![1]).toBeCloseTo(0.5, 9);
    expect(tr[1].uv![0]).toBeCloseTo((640 - 100) / 1280, 9);
    expect(tr[1].tMs).toBe(1000);
  });
  it("returns null for a malformed artifact", () => {
    expect(targetTrajectory(null)).toBeNull();
    expect(targetTrajectory({ compiled: {} })).toBeNull();
  });
});

describe("geometry warnings (confirm before Render Geometry)", () => {
  const path = (status: "ok" | "warning" | "invalid", reasons: string[] = [], vis: number | null = 1) =>
    ({ status, reasons, diagnostics: { target_in_frame_fraction: vis } }) as never;
  it("none for an ok path with few holes", () => expect(geometryWarnings(path("ok"), 0.02)).toEqual([]));
  it("lists compiler warnings, holes > 8 % and visibility < 90 %", () => {
    expect(geometryWarnings(path("warning", ["framing_clamped"], 0.85), 0.09)).toEqual(["framing_clamped", "holes_high", "visibility_low"]);
    expect(geometryWarnings(path("ok"), 0.08)).toEqual([]);
  });
  it("invalid paths are blocked elsewhere, not confirmed", () => expect(geometryWarnings(path("invalid", ["non_finite"]), 0.5)).toEqual([]));
  it("maps guide result warnings to reason codes", () => {
    expect(guideWarningCodes(["hole_ratio_high", "target_visibility_low", "framing_clamped", 3])).toEqual(["holes_high", "visibility_low", "framing_clamped"]);
    expect(guideWarningCodes(null)).toEqual([]);
  });
});

describe("cinematicWarnings (estimate dialog, spec §12)", () => {
  it("lists the guide's warnings and re-derives holes / visibility from its stats", () => {
    expect(cinematicWarnings({ warnings: ["hole_ratio_high"], hole_ratio_max: 0.115, target_visibility_min: 1 })).toEqual(["holes_high"]);
    expect(cinematicWarnings({ warnings: [], hole_ratio_max: 0.2, target_visibility_min: 0.5 })).toEqual(["holes_high", "visibility_low"]);
    expect(cinematicWarnings({ warnings: ["framing_clamped"], hole_ratio_max: 0.01, target_visibility_min: 1 })).toEqual(["framing_clamped"]);
    expect(cinematicWarnings({ warnings: [], hole_ratio_max: 0.08, target_visibility_min: 0.9 })).toEqual([]);
    expect(cinematicWarnings(null)).toEqual([]);
  });
  it("has en/ko text for the reduce-motion hint", () => {
    expect(en).toHaveProperty("estimateWarnings");
    expect(ko).toHaveProperty("estimateWarnings");
  });
});
