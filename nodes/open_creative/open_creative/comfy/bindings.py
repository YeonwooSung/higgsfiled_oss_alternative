"""Binding manifests: the only way runtime values enter an exported API graph.

A manifest maps logical names (prompt, seed, image, …) to `node_id` + `input`
of a graph exported from ComfyUI. Node IDs come from the real export, never
from documentation; `validate_manifest` checks them against the graph file.
"""

from __future__ import annotations

import copy
from typing import Any

# Logical bindings each workflow must expose. Values outside this contract are rejected.
REQUIRED_BINDINGS: dict[str, dict[str, str]] = {
    "product_segment_v1": {"image": "image", "prompts": "json"},
    "product_background_image_v1": {"prompt": "string", "seed": "int", "width": "int", "height": "int"},
    "product_background_video_v1": {
        "image": "image", "prompt": "string", "seed": "int", "width": "int", "height": "int", "frames": "int",
    },
    # Camera Sketch. `camera_guide_v1` / `camera_compare_v1` run on the CPU worker in P0 and have no
    # bindings here. guide_rgb / guide_depth are MP4 files (type "video"), not single images.
    "camera_depth_v1": {"image": "image"},
    "camera_cinematic_v1": {
        "guide_rgb": "video", "guide_depth": "video", "reference": "image", "prompt": "string", "seed": "int",
        "width": "int", "height": "int", "frames": "int", "control_strength": "float",
    },
    # Pocket VFX. SAM 2.1 video masks of the proxy work clip; ``prompts`` = ``pocket_vfx.masks.build_prompts``.
    "vfx_masks_v1": {"frames": "video", "prompts": "json", "width": "int", "height": "int"},
    # LTX-2.3 effect plate on black: 768x448 / 448x768, frames = 8k+1 covering the effect window (Ruling R3).
    "vfx_plate_v1": {"prompt": "string", "negative": "string", "seed": "int", "width": "int", "height": "int",
                     "frames": "int"},
}
# Which app (workflows/<app>/ directory) owns each workflow.
WORKFLOW_APP: dict[str, str] = {
    **{name: "product-lock" for name in REQUIRED_BINDINGS if name.startswith("product_")},
    "camera_depth_v1": "camera-sketch",
    "camera_cinematic_v1": "camera-sketch",
    "vfx_masks_v1": "pocket-vfx",
    "vfx_plate_v1": "pocket-vfx",
}
REQUIRED_OUTPUTS: dict[str, tuple[str, ...]] = {
    "product_segment_v1": ("mask",),
    "product_background_image_v1": ("image",),
    "product_background_video_v1": ("video",),
    "camera_depth_v1": ("depth",),
    "camera_cinematic_v1": ("video",),
    "vfx_masks_v1": ("masks",),  # ZIP of mask_%04d.png (8-bit gray, proxy size) + manifest.json
    "vfx_plate_v1": ("video",),  # raw emissive plate MP4 (sRGB, black background; the worker mattes it)
}
TYPES = {"string", "int", "float", "image", "video", "json"}


class BindingError(ValueError):
    pass


def validate_manifest(graph: dict, manifest: dict) -> None:
    workflow = manifest.get("workflow")
    if workflow not in REQUIRED_BINDINGS:
        raise BindingError(f"unknown workflow {workflow!r}")
    bindings = manifest.get("bindings", {})
    for name, kind in REQUIRED_BINDINGS[workflow].items():
        if name not in bindings:
            raise BindingError(f"{workflow}: missing binding {name}")
        if bindings[name].get("type") != kind:
            raise BindingError(f"{workflow}: binding {name} must be {kind}")
    for name, b in bindings.items():
        if b.get("type") not in TYPES:
            raise BindingError(f"binding {name}: unknown type {b.get('type')!r}")
        node = graph.get(str(b.get("node_id")))
        if node is None:
            raise BindingError(f"binding {name}: node {b.get('node_id')} not in graph")
        if b.get("input") not in node.get("inputs", {}):
            raise BindingError(f"binding {name}: node {b['node_id']} has no field {b.get('input')}")
    outputs = manifest.get("outputs", {})
    for name in REQUIRED_OUTPUTS[workflow]:
        if name not in outputs:
            raise BindingError(f"{workflow}: missing output {name}")
    for name, o in outputs.items():
        if str(o.get("node_id")) not in graph:
            raise BindingError(f"output {name}: node {o.get('node_id')} not in graph")


def _check_value(name: str, spec: dict, value: Any) -> None:
    kind = spec["type"]
    if kind == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "float":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif kind in ("string", "image", "video"):
        ok = isinstance(value, str)
    else:  # json
        ok = isinstance(value, (dict, list))
    if not ok:
        raise BindingError(f"binding {name}: expected {kind}")
    if kind in ("int", "float"):
        if "min" in spec and value < spec["min"]:
            raise BindingError(f"binding {name}: below minimum {spec['min']}")
        if "max" in spec and value > spec["max"]:
            raise BindingError(f"binding {name}: above maximum {spec['max']}")
    if kind == "string" and "max_length" in spec and len(value) > spec["max_length"]:
        raise BindingError(f"binding {name}: longer than {spec['max_length']}")


def validate_values(manifest: dict, values: dict) -> None:
    bindings = manifest["bindings"]
    unknown = set(values) - set(bindings)
    if unknown:
        raise BindingError(f"unknown bindings: {sorted(unknown)}")
    for name, spec in bindings.items():
        if name not in values:
            if spec.get("optional"):
                continue
            raise BindingError(f"missing value for binding {name}")
        _check_value(name, spec, values[name])


def apply_bindings(graph: dict, manifest: dict, values: dict) -> dict:
    """Return a copy of the graph with bound inputs replaced (values must already be final)."""
    validate_values(manifest, values)
    out = copy.deepcopy(graph)
    for name, value in values.items():
        b = manifest["bindings"][name]
        out[str(b["node_id"])]["inputs"][b["input"]] = value
    return out
