"""Per-app config schemas and job kind allowlists."""

from __future__ import annotations

from pydantic import BaseModel, ValidationError

from .errors import ApiError
from .product_lock.config import ProductLockConfig, default_config

APP_CONFIGS: dict[str, type[BaseModel]] = {"product-lock": ProductLockConfig}
APP_KINDS: dict[str, tuple[str, ...]] = {
    "product-lock": ("product.segment", "product.background", "product.background_video",
                     "product.render", "product.inspect"),
}


def default_app_config(app_slug: str, profile: str) -> BaseModel:
    if app_slug == "product-lock":
        return default_config(profile)
    raise ApiError(422, "unknown_app", f"unknown app {app_slug!r}", field="app_slug")


def validate_config(app_slug: str, data: dict) -> BaseModel:
    try:
        return APP_CONFIGS[app_slug].model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        raise ApiError(422, "invalid_config", first["msg"],
                       field="config." + ".".join(str(p) for p in first["loc"])) from None
