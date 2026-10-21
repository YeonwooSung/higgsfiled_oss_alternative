from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DbSession

from open_creative.pocket_vfx.presets import load_presets

from ..apps_registry import validate_config
from ..deps import current_session, get_db, owned_project
from ..errors import ApiError
from ..models import ProjectRevision
from ..models import Session as SessionRow
from .jobs import validate

router = APIRouter()

_PUBLIC_FIELDS = ("id", "version", "label_en", "label_ko", "input_requirements", "plate_modes", "controls",
                  "defaults", "invalid_policy")
_CLEAN_PLATE_CONDITION = {
    "condition_en": "Needs a fixed camera and an empty background frame",
    "condition_ko": "고정 카메라와 빈 배경 프레임이 필요합니다",
}


def public_preset(p: dict) -> dict:
    """What the editor shows: controls, requirements and plate choices (no prompts or repo file paths)."""
    out = {k: p[k] for k in _PUBLIC_FIELDS if k in p}
    out["bundled_plates"] = [{"id": b["id"], "frames": b["frames"], "loopable": b["loopable"]}
                             for b in p["bundled_plates"]]
    if p["input_requirements"].get("clean_plate"):
        out["availability"] = {"status": "requires_clean_plate", "requires": ["clean_plate"], **_CLEAN_PLATE_CONDITION}
    else:
        out["availability"] = {"status": "available", "requires": []}
    return out


def public_presets() -> list[dict]:
    return [public_preset(p) for p in load_presets().values()]


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


@router.get("/pocket-vfx/presets")
def presets():
    return {"presets": public_presets()}


@router.post("/projects/{project_id}/vfx/validate")
def validate_revision(project_id: str, body: ValidateRequest, db: DbSession = Depends(get_db),
                      session: SessionRow = Depends(current_session)):
    project = owned_project(project_id, db, session)
    if project.app_slug != "pocket-vfx":
        raise ApiError(422, "invalid_app", "This endpoint is only for Pocket VFX projects.")
    rev = db.get(ProjectRevision, (project.id, body.revision))
    if rev is None:
        raise ApiError(422, "revision_not_found", "Revision does not exist.", field="revision")
    return validate(db, project, validate_config(project.app_slug, rev.config_json))
