from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import get_current_account, get_db
from backend.app.db.models import Account, ApprovalRequest, HouseholdMember
from backend.app.schemas.agent_platform import ApprovalDecisionRequest
from backend.app.db.base import utcnow


router = APIRouter(prefix="/approvals", tags=["approvals"])


def _owned(db, approval_id, account_id):
    row = db.scalar(select(ApprovalRequest).join(HouseholdMember, HouseholdMember.household_id == ApprovalRequest.household_id).where(
        ApprovalRequest.id == approval_id, HouseholdMember.account_id == account_id))
    if not row:
        raise HTTPException(404, detail={"code": "APPROVAL_NOT_FOUND"})
    return row


@router.get("")
def pending(account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    rows = db.scalars(select(ApprovalRequest).join(HouseholdMember, HouseholdMember.household_id == ApprovalRequest.household_id).where(
        HouseholdMember.account_id == account.id, ApprovalRequest.status == "pending")).all()
    return [{"id": row.id, "run_id": row.run_id, "tool_id": row.tool_id, "status": row.status,
             "expires_at": row.expires_at, "created_at": row.created_at} for row in rows]


@router.post("/{approval_id}/approve")
def approve(approval_id: str, payload: ApprovalDecisionRequest, account: Account = Depends(get_current_account),
            db: Session = Depends(get_db)):
    row = _owned(db, approval_id, account.id)
    if payload.expires_at <= utcnow():
        raise HTTPException(422, detail={"code": "APPROVAL_EXPIRED"})
    row.status = "approved"; row.approved_by_account_id = account.id; row.expires_at = payload.expires_at
    db.commit()
    return {"id": row.id, "status": row.status, "expires_at": row.expires_at}


@router.post("/{approval_id}/reject")
def reject(approval_id: str, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    row = _owned(db, approval_id, account.id)
    row.status = "rejected"; row.approved_by_account_id = account.id; db.commit()
    return {"id": row.id, "status": row.status}
