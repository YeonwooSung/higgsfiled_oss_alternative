"""The committed TS parity fixture (fixtures/camera-sketch/parity.v1.json) equals a fresh build."""

import importlib.util
import json
import math
from pathlib import Path

# Imported here so the generator (loaded from its file below) resolves the installed package even
# under --import-mode=importlib, which registers the custom-node root as ``open_creative``.
from open_creative.camera_sketch import camera as _camera  # noqa: F401
from open_creative.camera_sketch import path as _path  # noqa: F401

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "fixtures" / "camera-sketch"


def _load_generator():
    spec = importlib.util.spec_from_file_location("cs_make_parity", FIX / "make_parity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _close(a, b, path="$"):
    """Structural equality; floats within 1e-9 (BLAS/libm may differ in the last bits per platform)."""
    if isinstance(a, float) or isinstance(b, float):
        assert isinstance(a, (int, float)) and isinstance(b, (int, float)), path
        assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9), f"{path}: {a} != {b}"
    elif isinstance(a, dict):
        assert isinstance(b, dict) and list(a) == list(b), path
        for k in a:
            _close(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _close(x, y, f"{path}[{i}]")
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


def test_committed_parity_fixture_matches_fresh_build():
    gen = _load_generator()
    fresh = json.loads(gen.dumps(gen.build()))
    committed = json.loads((FIX / "parity.v1.json").read_text())
    _close(fresh, committed)


def test_parity_fixture_covers_required_cases():
    data = json.loads((FIX / "parity.v1.json").read_text())
    cases = {c["id"]: c for c in data["compile"]}
    for scene in ("room", "facade"):  # landscape and portrait sources
        for aspect in ("16x9", "9x16"):
            for preset in ("slide", "push_in", "small_arc"):
                c = cases[f"{scene}_{aspect}_{preset}"]
                assert c["status"] == "ok" and len(c["keyframes"]) == 3
                assert len(c["samples"]) == 10 and len(c["target_px"]) == 10
    assert cases["room_16x9_slide_push"]["params"]["push_ratio"] > 0
    invalid = [c for c in data["compile"] if c["status"] == "invalid"]
    assert {r for c in invalid for r in c["reasons"]} >= {
        "param_out_of_bounds", "target_behind_camera", "target_out_of_frame", "framing_unsolvable",
        "invalid_input", "unknown_preset"}
    assert any(c["warning"] == "unsupported_sketch" for c in data["simplify_sketch"])
    assert data["solve_framing"] and data["cover_fit"]
    assert any(c["wraps"] for c in data["framing_rotation"])
    mesh = {c["id"]: c for c in data["mesh"]["grid"]}
    assert set(mesh) == {"box_16x9_s1", "box_16x9_s3", "box_16x9_s4_nan"}
    for c in mesh.values():  # the box cuts faces into the edge layer, never into nothing
        assert c["n_faces"] > 0 and c["n_edge_faces"] > 0 and len(c["stretch"]) == c["n_edge_faces"]
    assert mesh["box_16x9_s3"]["grid_shape"] == [4, 7]  # 16 = 5*3 + 1: partial last column
    s1 = mesh["box_16x9_s1"]
    assert s1["n_faces"] + s1["n_edge_faces"] == 2 * 9 * 16  # grid 10x17: nothing dropped
    nan = mesh["box_16x9_s4_nan"]
    assert nan["n_faces"] + nan["n_edge_faces"] < 2 * 3 * 4  # NaN pixels drop faces
    assert data["prepare_z"]["room"]["smooth"]["target_raw"] > 0
