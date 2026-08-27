import json
from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import get_current_account, get_db
from backend.app.db.models import Account, AgentRun, ApprovalRequest, HouseholdMember
from backend.agent.platform.context import RunContext
from backend.agent.tools.gateways import ToolCapabilities, ToolRepositories
from backend.agent.platform.approvals import audit_approval
from backend.app.models.runtime_service import ModelRuntimeError, ModelRuntimeService
from backend.app.schemas.agent_platform import ApprovalDecisionRequest
from backend.app.db.base import utcnow


router = APIRouter(prefix="/approvals", tags=["approvals"])


def _owned(db, approval_id, account_id):
    row = db.scalar(select(ApprovalRequest).where(ApprovalRequest.id == approval_id))
    if not row:
        raise HTTPException(404, detail={"code": "APPROVAL_NOT_FOUND"})
    member = db.scalar(select(HouseholdMember).where(HouseholdMember.household_id == row.household_id,
                                                     HouseholdMember.account_id == account_id))
    if row.principal_id != account_id and (not member or member.role not in {"owner", "admin"}):
        raise HTTPException(403, detail={"code": "APPROVAL_DECISION_FORBIDDEN"})
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
    if row.status != "pending":
        raise HTTPException(409, detail={"code": "APPROVAL_STATE_CONFLICT"})
    if payload.expires_at <= utcnow():
        raise HTTPException(422, detail={"code": "APPROVAL_EXPIRED"})
    row.status = "approved"; row.approved_by_account_id = account.id; row.expires_at = payload.expires_at
    db.commit(); audit_approval(db, row, "approval_approved")
    return {"id": row.id, "status": row.status, "expires_at": row.expires_at}


@router.post("/{approval_id}/reject")
def reject(approval_id: str, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    row = _owned(db, approval_id, account.id)
    if row.status != "pending":
        raise HTTPException(409, detail={"code": "APPROVAL_STATE_CONFLICT"})
    row.status = "rejected"; row.approved_by_account_id = account.id; db.commit(); audit_approval(db, row, "approval_rejected")
    return {"id": row.id, "status": row.status}


@router.post("/{approval_id}/resume")
def resume(approval_id: str, request: Request, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    approval = _owned(db, approval_id, account.id)
    if approval.status not in {"approved", "consumed"}:
        raise HTTPException(409, detail={"code": "APPROVAL_STATE_CONFLICT"})
    run = db.get(AgentRun, approval.run_id)
    if not run:
        raise HTTPException(404, detail={"code": "RUN_NOT_FOUND"})
    member = db.get(HouseholdMember, run.member_id)
    snapshot = json.loads(run.compiled_snapshot_json)
    try:
        resolved = ModelRuntimeService(db).resolve("primary_chat", household_id=run.household_id)
        if resolved.snapshot.model_id != run.model_id or resolved.snapshot.config_revision != run.model_revision:
            raise HTTPException(409, detail={"code": "MODEL_CONFIGURATION_STALE"})
        chat_model = ModelRuntimeService(db).build_chat_model(resolved)
    except ModelRuntimeError as exc:
        raise HTTPException(503, detail={"code": exc.code}) from exc
    context = RunContext(principal_id=run.principal_id, household_id=run.household_id, member_id=run.member_id,
        role=member.role, session_id=run.session_id, deployment_id=run.deployment_id,
        agent_version_id=run.agent_version_id, run_id=run.id, timezone="Asia/Shanghai",
        grants=snapshot.get("grants", {}), consent=json.loads(run.consent_snapshot_json),
        deployment_revision=run.deployment_revision, model_id=run.model_id, model_revision=run.model_revision,
        model_snapshot=json.loads(run.model_snapshot_json), compiled_snapshot=snapshot,
        skill_version_ids=tuple(item["id"] for item in snapshot.get("skills", [])),
        repositories=ToolCapabilities.bind(ToolRepositories(request.app.state.database.session_factory)),
        vector_store_service=request.app.state.vector_store_service,
        chat_model=chat_model)
    def events():
        for event in request.app.state.deployment_agent_runner.resume(approval_id, context):
            yield {"event": event["event"], "data": json.dumps(event.get("data", {}), ensure_ascii=False)}
    return EventSourceResponse(events(), ping=15, headers={"X-Accel-Buffering": "no"})
