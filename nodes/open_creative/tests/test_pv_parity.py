"""The committed TS parity fixture (fixtures/pocket-vfx/parity.v1.json) equals a fresh build."""

import importlib.util
import json
import math
from pathlib import Path

# Imported here so the generator (loaded from its file below) resolves the installed package even
# under --import-mode=importlib, which registers the custom-node root as ``open_creative``.
from open_creative.pocket_vfx import matte as _matte  # noqa: F401
from open_creative.pocket_vfx import procedural as _procedural  # noqa: F401
from open_creative.pocket_vfx import timeline as _timeline  # noqa: F401

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "fixtures" / "pocket-vfx"


def _load_generator():
    spec = importlib.util.spec_from_file_location("pv_make_fixtures", FIX / "make_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _close(a, b, path="$"):
    """Structural equality; floats within 1e-9 (libm may differ in the last bits per platform)."""
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
    fresh = json.loads(gen.parity_dumps(gen.parity_build()))
    committed = json.loads((FIX / "parity.v1.json").read_text())
    _close(fresh, committed)


def test_parity_fixture_covers_required_cases():
    data = json.loads((FIX / "parity.v1.json").read_text())
    frames = {c["t_ms"]: c["frame"] for c in data["frame_index"]}
    assert frames[20.8333] == 0 and frames[20.84] == 1  # half-frame edges
    assert {e["code"] for c in data["timing_errors"] for e in c["errors"]} == {"timing_order", "timing_range"}
    assert any(not c["errors"] for c in data["timing_errors"])
    taus = {c["tau"] for c in data["impact"]}
    assert 0.75 in taus and 3.0 in taus  # both clamp ends of the flash decay
    for c in data["impact"]:
        w = c["window"]
        before = [s for s in c["samples"] if s["frame"] < w[0]]
        assert before and all(s["glow"] is None and s["curve"] is None for s in before)
        assert any(s["curve"] is not None for s in c["samples"])
    kinds = {c["kind"] for c in data["plate_indices"]}
    assert kinds == {"trim", "retime", "loop", "plate_length", "invalid"}
