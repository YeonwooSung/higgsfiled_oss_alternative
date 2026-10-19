"""Per-app config helpers; app specifics live in `apps/`."""

from __future__ import annotations

from pydantic import BaseModel, ValidationError

from .apps import get_app
from .errors import ApiError


def default_app_config(app_slug: str, profile: str) -> BaseModel:
    return get_app(app_slug).default_config(profile)


def validate_config(app_slug: str, data: dict) -> BaseModel:
    try:
        return get_app(app_slug).config_model.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        raise ApiError(422, "invalid_config", first["msg"],
                       field="config." + ".".join(str(p) for p in first["loc"])) from None
