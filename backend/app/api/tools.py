from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.agent.platform.catalog import BuiltinToolRegistry
from backend.app.core.security import get_db, require_admin
from backend.app.db.models import Account, Household, ToolDefinition, ToolPermission
from backend.app.schemas.agent_platform import ToolGrantRequest


router = APIRouter(prefix="/tools", tags=["tools"])


def sync_builtin_catalog(session: Session):
    registry = BuiltinToolRegistry.default()
    for metadata in registry.metadata():
        row = session.scalar(select(ToolDefinition).where(ToolDefinition.key == metadata.key))
        if row is None:
            row = ToolDefinition(key=metadata.key, display_name=metadata.display_name,
                description=metadata.description, risk_level=metadata.risk_level,
                version=metadata.version, enabled=True)
            session.add(row)
        elif (row.display_name, row.description, row.risk_level, row.version, row.enabled) != (
                metadata.display_name, metadata.description, metadata.risk_level, metadata.version, True):
            raise RuntimeError("TOOL_CATALOG_MISMATCH")
    session.commit()


def _view(row):
    return {key: getattr(row, key) for key in ("id", "key", "display_name", "description", "risk_level", "version", "enabled")}


@router.get("/catalog", dependencies=[Depends(require_admin)])
def catalog(db: Session = Depends(get_db)):
    sync_builtin_catalog(db)
    return [_view(row) for row in db.scalars(select(ToolDefinition).order_by(ToolDefinition.key)).all()]


@router.put("/{tool_id}/grants", status_code=201)
def grant_tool(tool_id: str, payload: ToolGrantRequest, account: Account = Depends(require_admin), db: Session = Depends(get_db)):
    from backend.app.audit.service import audit_admin_action

    if not db.get(Household, payload.household_id) or not db.get(ToolDefinition, tool_id):
        raise HTTPException(404, detail={"code": "RESOURCE_NOT_FOUND"})
    row = db.scalar(select(ToolPermission).where(ToolPermission.household_id == payload.household_id,
                                                  ToolPermission.tool_id == tool_id))
    if row is None:
        row = ToolPermission(household_id=payload.household_id, tool_id=tool_id,
                             tool_version=payload.tool_version, granted_by_account_id=account.id)
        db.add(row)
    else:
        row.tool_version = payload.tool_version
        row.granted_by_account_id = account.id
    db.commit(); db.refresh(row)
    audit_admin_action(db, account, "tools.grant.create", "tool_permission", tool_id,
                       {"household_id": payload.household_id, "tool_version": payload.tool_version},
                       household_id=payload.household_id)
    return {"id": row.id, "household_id": row.household_id, "tool_id": row.tool_id, "tool_version": row.tool_version}


@router.delete("/{tool_id}/grants/{household_id}", status_code=204)
def revoke_tool(tool_id: str, household_id: str, account: Account = Depends(require_admin), db: Session = Depends(get_db)):
    from backend.app.audit.service import audit_admin_action

    db.execute(delete(ToolPermission).where(ToolPermission.household_id == household_id, ToolPermission.tool_id == tool_id))
    db.commit()
    audit_admin_action(db, account, "tools.grant.delete", "tool_permission", tool_id,
                       {"household_id": household_id}, household_id=household_id)
