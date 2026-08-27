import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.platform.versions import DefinitionRejected, SkillVersionService
from backend.app.core.security import get_db, require_admin
from backend.app.db.models import Skill
from backend.app.schemas.agent_platform import VersionPublishRequest


router = APIRouter(prefix="/skills", tags=["skills"], dependencies=[Depends(require_admin)])


@router.post("/{skill_id}/versions", status_code=201)
def publish_skill(skill_id: str, payload: VersionPublishRequest, db: Session = Depends(get_db)):
    skill = db.scalar(select(Skill).where(Skill.id == skill_id, Skill.household_id == payload.household_id))
    if not skill:
        raise HTTPException(404, detail={"code": "SKILL_NOT_FOUND"})
    try:
        view = SkillVersionService(db, payload.household_id).publish(skill_id, payload.definition)
    except DefinitionRejected as exc:
        raise HTTPException(422, detail={"code": exc.code}) from exc
    row = view.row
    return {"id": row.id, "skill_id": row.skill_id, "household_id": row.household_id, "version": row.version,
            "definition": json.loads(row.definition_json), "published_at": row.published_at}
