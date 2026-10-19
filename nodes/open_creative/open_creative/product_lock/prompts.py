"""Background preset loading and prompt construction.

Prompts describe only the background; the product is never described to, or
rendered by, the generative model.
"""

from __future__ import annotations

import colorsys
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

EXTRA_PROMPT_MAX = 300
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
DEFAULT_PRESETS = Path(__file__).resolve().parents[4] / "presets" / "product-lock" / "presets.v1.json"
CENTER_COMPOSITION = "empty center area reserved for a product, nothing standing in the middle of the frame"


@dataclass(frozen=True)
class Preset:
    id: str
    version: str
    name: str
    description: str
    prompt: str
    negative: str
    video_prompt: dict
    default_accent: str
    allowed_motion: tuple[str, ...]
    particles: str
    default_shadow: dict
    prompt_fields: tuple[str, ...]
    sample: bool

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def public(self) -> dict:
        return {
            "id": self.id, "version": self.version, "name": self.name, "description": self.description,
            "default_accent": self.default_accent, "allowed_motion": list(self.allowed_motion),
            "prompt_fields": list(self.prompt_fields), "default_shadow": self.default_shadow,
            "particles": self.particles, "sample": self.sample,
        }


PACKAGED_PRESETS = Path(__file__).with_name("presets.v1.json")


def presets_path() -> Path:
    """Env override → repo `presets/product-lock` → copy packaged with the custom node."""
    if os.environ.get("OC_PRODUCT_LOCK_PRESETS"):
        return Path(os.environ["OC_PRODUCT_LOCK_PRESETS"])
    return DEFAULT_PRESETS if DEFAULT_PRESETS.exists() else PACKAGED_PRESETS


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Preset]:
    data = json.loads(Path(path).read_text())
    out = {}
    for p in data["presets"]:
        out[p["id"]] = Preset(
            id=p["id"], version=p["version"], name=p["name"], description=p["description"],
            prompt=p["prompt"], negative=p["negative"], video_prompt=p["video_prompt"],
            default_accent=p["default_accent"], allowed_motion=tuple(p["allowed_motion"]),
            particles=p["particles"], default_shadow=p["default_shadow"],
            prompt_fields=tuple(p["prompt_fields"]), sample=bool(p.get("sample", False)),
        )
    return out


def load_presets(path: str | Path | None = None) -> dict[str, Preset]:
    return _load(str(path or presets_path()))


def color_name(hex_: str) -> str:
    """Describe a hex color in words; diffusion text encoders do not read hex codes."""
    if not HEX_RE.match(hex_):
        raise ValueError(f"invalid color {hex_!r}")
    r, g, b = (int(hex_[i : i + 2], 16) / 255 for i in (1, 3, 5))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.15 or v < 0.12:
        if v < 0.2:
            return "black"
        if v > 0.92:
            return "white"
        return "light gray" if v > 0.6 else "gray"
    deg = h * 360
    for limit, name in ((15, "red"), (40, "orange"), (65, "yellow"), (170, "green"), (200, "teal"),
                        (255, "blue"), (290, "purple"), (345, "pink"), (360, "red")):
        if deg < limit:
            break
    if v < 0.35:
        return f"dark {name}"
    if s < 0.4 and v > 0.8:
        return f"pastel {name}"
    return name


def sanitize_extra(extra: str) -> str:
    if len(extra) > EXTRA_PROMPT_MAX:
        raise ValueError(f"extra prompt must be at most {EXTRA_PROMPT_MAX} characters")
    cleaned = "".join(ch if ch.isprintable() else " " for ch in extra)
    return re.sub(r"\s+", " ", cleaned).strip()


def build_background_prompt(
    preset: Preset, accent_hex: str, composition: str = "center", extra_prompt: str = ""
) -> tuple[str, str]:
    if composition != "center":
        raise ValueError(f"unsupported composition {composition!r}")
    prompt = preset.prompt.replace("{color}", color_name(accent_hex))
    parts = [prompt, CENTER_COMPOSITION]
    extra = sanitize_extra(extra_prompt)
    if extra:
        parts.append(extra)
    return ", ".join(parts), preset.ref


def build_video_prompt(preset: Preset, motion: str) -> str:
    if motion not in preset.allowed_motion:
        raise ValueError(f"motion {motion!r} not allowed for {preset.id}")
    return preset.video_prompt[motion]
