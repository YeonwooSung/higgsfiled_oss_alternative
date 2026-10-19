from collections.abc import Callable

from ..stages import JobRun
from . import background

Executor = Callable[[JobRun], tuple[list[str], dict | None]]

EXECUTORS: dict[str, Executor] = {
    "product.background": background.background_image,
}
