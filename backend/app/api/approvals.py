import json
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import get_current_account, get_db
from backend.app.db.models import Account, AgentRun, ApprovalRequest, HouseholdMember, ToolDefinition
from backend.agent.platform.context import RunBudget, RunContext
from backend.agent.tools.household_gateways import HouseholdToolRepositories
from backend.agent.platform.approvals import audit_approval
from backend.app.llm_runtime.runtime_service import ModelRuntimeError, ModelRuntimeService
from backend.app.schemas.agent_platform import ApprovalDecisionRequest
from backend.app.db.base import utcnow
from backend.app.api.setup import purpose_ready


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


def _effective_window(requested, settings):
    """把客户端申请的审批窗口夹到服务端上限以内。

    客户端传得更短就尊重它（用户主动想快点过期），传得更长或干脆不传就由服务端定。
    不夹的话 `expires_at = now + 10 年` 就能把「高风险逐次确认」变成一次永久授权。
    """
    now = utcnow()
    cap = now + timedelta(minutes=settings.approval_max_minutes)
    if requested is None:
        return now + timedelta(minutes=min(settings.approval_default_minutes, settings.approval_max_minutes))
    if requested.tzinfo is not None:
        requested = requested.astimezone(timezone.utc).replace(tzinfo=None)
    return min(requested, cap)


@router.get("")
def pending(account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    """待处理审批清单。

    列表要么由前端轮询、要么在审批卡里直接渲染，两种用法都需要**工具是什么、风险多高、
    要做什么** —— 缺了这些，前端只能显示一个裸 id，用户没有任何判断依据。
    `arguments_json` 写入时已经过 `audit.redact()`（见 agent/platform/policy.py），
    这里直接解析即可，不会把凭据原文送出去。
    """
    rows = db.scalars(select(ApprovalRequest).join(
        HouseholdMember, HouseholdMember.household_id == ApprovalRequest.household_id).where(
        HouseholdMember.account_id == account.id, ApprovalRequest.status == "pending")).all()
    if not rows:
        return []
    tools = {tool.id: tool for tool in db.scalars(
        select(ToolDefinition).where(ToolDefinition.id.in_({row.tool_id for row in rows})))}
    runs = {run.id: run for run in db.scalars(
        select(AgentRun).where(AgentRun.id.in_({row.run_id for row in rows})))}
    result = []
    for row in rows:
        tool = tools.get(row.tool_id)
        run = runs.get(row.run_id)
        result.append({
            "id": row.id, "run_id": row.run_id, "tool_id": row.tool_id, "status": row.status,
            "expires_at": row.expires_at, "created_at": row.created_at,
            "tool_key": tool.key if tool else None,
            "tool_display_name": (tool.display_name or tool.key) if tool else None,
            "risk_level": tool.risk_level if tool else None,
            "arguments": _redacted_arguments(row),
            # approval_requests 自己没有 session_id，会话要从 run 上取
            "session_id": run.session_id if run else None,
            "deployment_id": row.deployment_id,
        })
    return result


def _redacted_arguments(row) -> dict:
    try:
        parsed = json.loads(row.arguments_json or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


@router.post("/{approval_id}/approve")
def approve(approval_id: str, payload: ApprovalDecisionRequest, request: Request,
            account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    row = _owned(db, approval_id, account.id)
    if row.status != "pending":
        raise HTTPException(409, detail={"code": "APPROVAL_STATE_CONFLICT"})
    expires_at = _effective_window(payload.expires_at, request.app.state.settings)
    if expires_at <= utcnow():
        raise HTTPException(422, detail={"code": "APPROVAL_EXPIRED"})
    row.status = "approved"; row.approved_by_account_id = account.id; row.expires_at = expires_at
    audit_approval(db, row, "approval_approved"); db.commit()
    return {"id": row.id, "status": row.status, "expires_at": row.expires_at}


@router.post("/{approval_id}/reject")
def reject(approval_id: str, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    row = _owned(db, approval_id, account.id)
    if row.status != "pending":
        raise HTTPException(409, detail={"code": "APPROVAL_STATE_CONFLICT"})
    row.status = "rejected"; row.approved_by_account_id = account.id; audit_approval(db, row, "approval_rejected"); db.commit()
    return {"id": row.id, "status": row.status}


@router.post("/{approval_id}/resume")
def resume(approval_id: str, request: Request, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    approval = _owned(db, approval_id, account.id)
    if approval.status == "retryable":
        raise HTTPException(409, detail={"code": "APPROVAL_RETRY_REQUIRED",
                                         "fresh_invocation_required": True})
    if approval.status == "executing":
        raise HTTPException(409, detail={"code": "APPROVAL_IN_PROGRESS"})
    if approval.status not in {"approved", "consumed"}:
        raise HTTPException(409, detail={"code": "APPROVAL_STATE_CONFLICT"})
    if approval.status == "consumed":
        return {"id": approval.id, "status": "consumed", "result": json.loads(approval.result_json or "{}")}
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
    budget_snapshot = json.loads(run.budget_snapshot_json or "{}")
    context = RunContext(principal_id=run.principal_id, household_id=run.household_id, member_id=run.member_id,
        role=member.role, session_id=run.session_id, deployment_id=run.deployment_id,
        agent_version_id=run.agent_version_id, run_id=run.id, timezone="Asia/Shanghai",
        grants=snapshot.get("grants", {}), consent=json.loads(run.consent_snapshot_json),
        deployment_revision=run.deployment_revision, model_id=run.model_id, model_revision=run.model_revision,
        model_snapshot=json.loads(run.model_snapshot_json), compiled_snapshot=snapshot,
        skill_version_ids=tuple(item["id"] for item in snapshot.get("skills", [])),
        repositories=HouseholdToolRepositories(request.app.state.database.session_factory),
        vector_store_service=(getattr(request.app.state, "vector_store_service", None)
                              if purpose_ready(db, "embedding") else None),
        budget=RunBudget.restore(budget_snapshot, budget_snapshot.get("counters", {})))
    request.app.state.deployment_agent_runner.bind_model(run.id, chat_model)
    def events():
        for event in request.app.state.deployment_agent_runner.resume(approval_id, context):
            yield {"event": event["event"], "data": json.dumps(event.get("data", {}), ensure_ascii=False)}
    return EventSourceResponse(events(), ping=15, headers={"X-Accel-Buffering": "no"})
