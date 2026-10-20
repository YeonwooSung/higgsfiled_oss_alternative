"""Pocket VFX project config (skeleton; the full model arrives with the job kinds)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PocketVfxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    video_asset_id: UUID | None = None
    model_profile: str = "open_weights_v1"


def default_config(model_profile: str = "open_weights_v1") -> PocketVfxConfig:
    return PocketVfxConfig(model_profile=model_profile)
