from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.agent.platform.installations import InstallationService
from backend.app.core.security import get_current_account, get_db
from backend.app.db.models import Account, AgentInstallation, HouseholdMember, InstallationPermission
from backend.app.schemas.agent_platform import InstallationPermissionRequest, InstallationRequest


router = APIRouter(prefix="/installations", tags=["installations"])


def _membership(db, account_id, household_id):
    row = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == account_id,
                                                  HouseholdMember.household_id == household_id))
    if not row:
        raise HTTPException(404, detail={"code": "HOUSEHOLD_NOT_FOUND"})
    return row


def _manager(db, account_id, household_id):
    member = _membership(db, account_id, household_id)
    if member.role not in {"owner", "admin"}:
        raise HTTPException(403, detail={"code": "INSTALLATION_PERMISSION_FORBIDDEN"})
    return member


@router.post("", status_code=201)
def install(payload: InstallationRequest, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    _membership(db, account.id, payload.household_id)
    try:
        row = InstallationService(db, payload.household_id, account.id).install(payload.deployment_id)
    except RuntimeError as exc:
        raise HTTPException(404, detail={"code": str(exc)}) from exc
    return {"id": row.id, "household_id": row.household_id, "deployment_id": row.deployment_id,
            "agent_version_id": row.agent_version_id, "status": row.status}


@router.put("/{installation_id}/permissions", status_code=201)
def grant_permission(installation_id: str, payload: InstallationPermissionRequest,
                     account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    installation = db.get(AgentInstallation, installation_id)
    if not installation:
        raise HTTPException(404, detail={"code": "INSTALLATION_NOT_FOUND"})
    _manager(db, account.id, installation.household_id)
    try:
        row = InstallationService(db, installation.household_id, account.id).grant(
            installation_id, payload.tool_id, payload.tool_version)
    except RuntimeError as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc
    return {"id": row.id, "installation_id": row.installation_id, "tool_id": row.tool_id,
            "tool_version": row.tool_version}


@router.delete("/{installation_id}/permissions/{tool_id}", status_code=204)
def revoke_permission(installation_id: str, tool_id: str, account: Account = Depends(get_current_account),
                      db: Session = Depends(get_db)):
    installation = db.get(AgentInstallation, installation_id)
    if not installation:
        raise HTTPException(404, detail={"code": "INSTALLATION_NOT_FOUND"})
    _manager(db, account.id, installation.household_id)
    db.execute(delete(InstallationPermission).where(InstallationPermission.installation_id == installation_id,
                                                     InstallationPermission.tool_id == tool_id))
    db.commit()
