import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.platform.versions import AgentVersionService, DefinitionRejected
from backend.app.core.security import get_db, require_admin
from backend.app.db.models import AgentDefinition, AgentDeployment, AgentVersion
from backend.app.schemas.agent_platform import DeploymentRequest, VersionPublishRequest


router = APIRouter(prefix="/agents", tags=["agents"], dependencies=[Depends(require_admin)])


def _version_view(row):
    return {"id": row.id, "agent_id": row.agent_id, "household_id": row.household_id, "version": row.version,
            "definition": json.loads(row.definition_json), "published_at": row.published_at}


@router.post("/{agent_id}/versions", status_code=201)
def publish_version(agent_id: str, payload: VersionPublishRequest, db: Session = Depends(get_db)):
    definition = db.scalar(select(AgentDefinition).where(AgentDefinition.id == agent_id,
                                                         AgentDefinition.household_id == payload.household_id))
    if not definition:
        raise HTTPException(404, detail={"code": "AGENT_NOT_FOUND"})
    try:
        view = AgentVersionService(db, payload.household_id).publish(agent_id, payload.definition)
    except DefinitionRejected as exc:
        raise HTTPException(422, detail={"code": exc.code}) from exc
    return _version_view(view.row)


@router.post("/deployments", status_code=201)
def deploy(payload: DeploymentRequest, db: Session = Depends(get_db)):
    version = db.scalar(select(AgentVersion).where(AgentVersion.id == payload.agent_version_id,
                                                   AgentVersion.household_id == payload.household_id))
    if not version:
        raise HTTPException(404, detail={"code": "AGENT_VERSION_NOT_FOUND"})
    row = AgentVersionService(db, payload.household_id).deploy(version.id)
    return {"id": row.id, "household_id": row.household_id, "agent_version_id": row.agent_version_id,
            "revision": row.revision, "visibility": row.visibility}


@router.put("/deployments/{deployment_id}")
def switch_deployment(deployment_id: str, payload: DeploymentRequest, db: Session = Depends(get_db)):
    deployment = db.scalar(select(AgentDeployment).where(AgentDeployment.id == deployment_id,
                                                         AgentDeployment.household_id == payload.household_id))
    if not deployment:
        raise HTTPException(404, detail={"code": "DEPLOYMENT_NOT_FOUND"})
    try:
        row = AgentVersionService(db, payload.household_id).switch(deployment_id, payload.agent_version_id)
    except DefinitionRejected as exc:
        raise HTTPException(409, detail={"code": exc.code}) from exc
    return {"id": row.id, "household_id": row.household_id, "agent_version_id": row.agent_version_id,
            "revision": row.revision, "visibility": row.visibility}
