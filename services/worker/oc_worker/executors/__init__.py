from collections.abc import Callable

from ..stages import JobRun
from .background import background_image
from .background_video import background_video
from .inspect_asset import inspect_asset
from .inspect_render import inspect_render
from .render import render
from .segment import segment

Executor = Callable[[JobRun], tuple[list[str], dict | None]]

EXECUTORS: dict[str, Executor] = {
    "asset.inspect": inspect_asset,
    "product.segment": segment,
    "product.background": background_image,
    "product.background_video": background_video,
    "product.render": render,
    "product.inspect": inspect_render,
}
