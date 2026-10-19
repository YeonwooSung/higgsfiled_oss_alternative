"""ComfyUI entry point: copy (or symlink) this directory into ComfyUI/custom_nodes/open_creative."""

from .open_creative.comfy_nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
