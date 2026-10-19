"""Execution profiles. A profile is exposed as available only after its smoke
preconditions hold; it is never assumed from documentation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .bindings import REQUIRED_BINDINGS, BindingError, validate_manifest

MOCK_PROFILE = "mock_v1"
OPEN_WEIGHTS_PROFILE = "open_weights_v1"

# FLUX latents need multiples of 16; LTX needs multiples of 32 and 8n+1 frames.
_IMAGE_SIZES = {"9:16": (720, 1280), "16:9": (1280, 720)}
_VIDEO_SHAPES = {"9:16": (736, 1280, 121), "16:9": (1280, 736, 121)}

# Upper bounds of provider GPU time reserved before submit (unit: gpu_seconds).
# They are configured caps, not measured costs or prices.
GPU_SECONDS_CAP = {
    "product.segment": 60,
    "product.background": 120,
    "product.background_video": 600,
    "product.render": 0,
    "product.inspect": 0,
}


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    is_mock: bool
    description: str


PROFILES = {
    MOCK_PROFILE: Profile(MOCK_PROFILE, "Mock", True,
                          "Deterministic local stand-in (GrabCut mask, synthetic studio backgrounds). Not generative."),
    OPEN_WEIGHTS_PROFILE: Profile(OPEN_WEIGHTS_PROFILE, "Open weights", False,
                                  "SAM 2.1 small + FLUX.1-schnell + LTX-2.3 distilled on a Comfy API deployment."),
}


@dataclass(frozen=True)
class ProfileStatus:
    id: str
    label: str
    is_mock: bool
    available: bool
    reason: str | None


def get_profile(profile_id: str) -> Profile:
    return PROFILES[profile_id]


def load_workflow(workflows_dir: Path, name: str) -> tuple[dict, dict]:
    manifest = json.loads((workflows_dir / f"{name}.bindings.json").read_text())
    graph = json.loads((workflows_dir / manifest["graph"]).read_text())
    return graph, manifest


def profile_status(profile_id: str, workflows_dir: Path, comfy_base_url: str | None) -> ProfileStatus:
    p = get_profile(profile_id)

    def status(ok: bool, reason: str | None = None) -> ProfileStatus:
        return ProfileStatus(p.id, p.label, p.is_mock, ok, reason)

    if p.is_mock:
        return status(True)
    if not comfy_base_url:
        return status(False, "COMFY_BASE_URL is not configured")
    for name in REQUIRED_BINDINGS:
        manifest_path = workflows_dir / f"{name}.bindings.json"
        if not manifest_path.exists():
            return status(False, f"{name}: binding manifest not exported")
        try:
            graph, manifest = load_workflow(workflows_dir, name)
            validate_manifest(graph, manifest)
        except FileNotFoundError:
            return status(False, f"{name}: API graph not exported")
        except (BindingError, json.JSONDecodeError, KeyError) as e:
            return status(False, f"{name}: {e}")
    return status(True)


def background_image_size(aspect: str) -> tuple[int, int]:
    return _IMAGE_SIZES[aspect]


def background_video_shape(aspect: str) -> tuple[int, int, int]:
    return _VIDEO_SHAPES[aspect]
