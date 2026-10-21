from collections.abc import Callable

from ..stages import JobRun
from .background import background_image
from .background_video import background_video
from .camera_analyze import camera_analyze
from .camera_cinematic import camera_cinematic
from .camera_guide import camera_guide
from .export import export
from .inspect_asset import inspect_asset
from .inspect_render import inspect_render
from .render import render
from .segment import segment
from .vfx_analyze import vfx_analyze
from .vfx_plate import vfx_generate_plate
from .vfx_render import vfx_render
from .vfx_track import vfx_track

Executor = Callable[[JobRun], tuple[list[str], dict | None]]

EXECUTORS: dict[str, Executor] = {
    "asset.inspect": inspect_asset,
    "product.segment": segment,
    "product.background": background_image,
    "product.background_video": background_video,
    "product.render": render,
    "product.inspect": inspect_render,
    "export": export,
    "camera.analyze": camera_analyze,
    "camera.guide": camera_guide,
    "camera.cinematic": camera_cinematic,
    "camera.export": export,  # dispatches by app
    "vfx.analyze": vfx_analyze,
    "vfx.track": vfx_track,
    "vfx.generate_plate": vfx_generate_plate,
    "vfx.render": vfx_render,
}
