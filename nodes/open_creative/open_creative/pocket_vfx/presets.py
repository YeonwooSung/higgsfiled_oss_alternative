"""Pocket VFX presets loader (repo ``presets/pocket-vfx`` -> copy packaged with the node)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DEFAULT_PRESETS = Path(__file__).resolve().parents[4] / "presets" / "pocket-vfx" / "presets.v1.json"
PACKAGED_PRESETS = Path(__file__).with_name("presets.v1.json")


def presets_path() -> Path:
    return DEFAULT_PRESETS if DEFAULT_PRESETS.exists() else PACKAGED_PRESETS


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_presets() -> dict[str, dict]:
    return {p["id"]: p for p in _load(str(presets_path()))["presets"]}


def load_palettes() -> dict[str, dict]:
    return _load(str(presets_path()))["palettes"]
