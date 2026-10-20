"""Python -> TS parity fixture for ``oc_api.camera_sketch.jobs.geometry_hash`` (``geometry_hash.v1.json``).

Run: uv run python fixtures/camera-sketch/make_geometry_hash.py
``apps/camera-sketch/tests/results.test.ts`` recomputes every hash in the browser port
(``src/model/results.ts::geometryHash``) and ``services/api/tests/test_camera_jobs.py`` checks the
committed file equals a fresh build. The cases cover integer-valued floats (``50.0``), tiny values that
Python prints in exponent form (``1e-05``), round-off reprs, ``-0.0``, null/set asset ids and sketches.
"""

from __future__ import annotations

import json
from pathlib import Path

from oc_api.camera_sketch.config import CameraSketchConfig
from oc_api.camera_sketch.jobs import geometry_hash

OUT = Path(__file__).with_name("geometry_hash.v1.json")


def cases() -> list[dict]:
    base = CameraSketchConfig(model_profile="mock_v1").model_dump(mode="json")
    ids = {"scene_asset_id": "0b6f5d2e-3c1a-4f7e-9d2b-1a2b3c4d5e6f",
           "depth_asset_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7"}
    out = [{"name": "default", "config": base}]
    a = json.loads(json.dumps(base))
    a.update(ids)
    a["target"] = {"u": 0.3, "v": 0.30000000000000004, "depth_scale": 1.25}
    a["camera"]["focal_length_mm_equiv"] = 35
    a["path"].update({"preset": "slide", "lateral_ratio": -0.00001, "push_ratio": 0.0, "arc_deg": 0.0,
                      "easing": "linear",
                      "sketch_points": [{"t": 0, "u": 0.5, "v": 0.5}, {"t": 0.4, "u": 0.55, "v": 0.123456789012345},
                                        {"t": 1, "u": 0.6, "v": 0.5}]})
    a["scene"] = {"depth_contrast": 1.5, "edge_protection": 0.05, "show_holes": True}
    a["generation"] = {"seed": 7, "control_preset": "high", "extra_prompt": "ignored ✓"}
    out.append({"name": "sketch_small_values", "config": a})
    b = json.loads(json.dumps(a))
    b["path"].update({"preset": "small_arc", "arc_deg": -6.0, "lateral_ratio": -0.0, "sketch_points": []})
    b["output"]["aspect"] = "9:16"
    b["target"]["depth_scale"] = 0.5
    b["camera"]["focal_length_mm_equiv"] = 69.99999999999999
    out.append({"name": "portrait_arc_negzero", "config": b})
    for c in out:
        c["geometry_hash"] = geometry_hash(CameraSketchConfig.model_validate(c["config"]))
        c["config"] = CameraSketchConfig.model_validate(c["config"]).model_dump(mode="json")
    return out


def build() -> dict:
    return {"version": 1, "cases": cases()}


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}")
