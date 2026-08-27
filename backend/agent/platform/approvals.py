import json
import threading
import hashlib

from sqlalchemy import select, update

from backend.agent.platform.authority import ExecutionAuthority
from backend.agent.platform.policy import arguments_digest, canonical_json
from backend.agent.platform.policy_errors import PlatformToolError
from backend.app.db.base import utcnow
from backend.app.db.models import AgentDeployment, ApprovalRequest, HouseholdMember
from backend.app.db.models import AgentRun
from backend.agent.platform.audit import ToolAuditService
from types import SimpleNamespace


def audit_approval(session, row, status, error_code=None):
    run = session.get(AgentRun, row.run_id)
    if not run: return
    context = SimpleNamespace(household_id=run.household_id, run_id=run.id, principal_id=run.principal_id,
        agent_version_id=run.agent_version_id, skill_version_ids=tuple(), model_id=run.model_id,
        deployment_id=run.deployment_id, deployment_revision=run.deployment_revision)
    ToolAuditService(session).record(context=context, tool_id=row.tool_id, tool_version=row.tool_version,
        status=status, error_code=error_code, started_at=utcnow(), idempotency_key=f"{row.idempotency_key}:{status}",
        arguments={}, result={}, commit=False)


class ApprovalResumeService:
    _locks = {}
    _locks_guard = threading.Lock()
    def __init__(self, session_factory, side_effect):
        self.session_factory = session_factory
        self.side_effect = side_effect

    def claim_and_execute(self, approval_id, context, arguments):
        with self._locks_guard:
            lock = self._locks.setdefault(approval_id, threading.Lock())
        with lock:
            return self._claim_and_execute(approval_id, context, arguments)

    def _claim_and_execute(self, approval_id, context, arguments):
        with self.session_factory() as session:
            row = session.scalar(select(ApprovalRequest).where(ApprovalRequest.id == approval_id).with_for_update())
            if not row: raise PlatformToolError("APPROVAL_NOT_FOUND")
            if row.result_json is not None: return json.loads(row.result_json)
            if row.status == "executing":
                if row.claimed_at and row.claimed_at <= utcnow() - __import__("datetime").timedelta(minutes=5):
                    row.status = "retryable"; row.error_code = "EXECUTION_LEASE_EXPIRED"
                    row.error_detail = None
                    audit_approval(session, row, "approval_retryable", row.error_code)
                    session.commit()
                    raise PlatformToolError("APPROVAL_RETRY_REQUIRED")
                raise PlatformToolError("APPROVAL_IN_PROGRESS")
            if row.status == "retryable":
                raise PlatformToolError("APPROVAL_RETRY_REQUIRED")
            if row.status != "approved": raise PlatformToolError("APPROVAL_STATE_CONFLICT")
            if row.principal_id != context.principal_id or row.household_id != context.household_id or row.run_id != context.run_id:
                raise PlatformToolError("PERMISSION_REQUIRED")
            approver = session.scalar(select(HouseholdMember).where(
                HouseholdMember.account_id == row.approved_by_account_id,
                HouseholdMember.household_id == row.household_id)) if row.approved_by_account_id else None
            if row.approved_by_account_id != row.principal_id and (not approver or approver.household_id != row.household_id or
                                                                   approver.role not in {"owner", "admin"}):
                raise PlatformToolError("APPROVAL_DECISION_FORBIDDEN")
            consent_digest = hashlib.sha256(canonical_json(dict(context.consent)).encode("utf-8")).hexdigest()
            if row.consent_digest != consent_digest: raise PlatformToolError("APPROVAL_CONSENT_CHANGED")
            if not row.expires_at or row.expires_at <= utcnow():
                try:
                    row.status = "expired"; row.error_code = "APPROVAL_EXPIRED"; row.error_detail = None
                    audit_approval(session, row, "approval_expired", row.error_code)
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
                raise PlatformToolError("APPROVAL_EXPIRED")
            if row.arguments_digest != arguments_digest(arguments): raise PlatformToolError("APPROVAL_ARGUMENTS_MISMATCH")
            deployment = session.scalar(select(AgentDeployment).where(AgentDeployment.id == row.deployment_id,
                                                                       AgentDeployment.household_id == row.household_id))
            if not deployment or deployment.revision != row.deployment_revision or deployment.agent_version_id != row.agent_version_id:
                try:
                    row.status = "invalidated"; row.error_code = "APPROVAL_VERSION_CHANGED"; row.error_detail = None
                    audit_approval(session, row, "approval_invalidated", row.error_code)
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
                raise PlatformToolError("APPROVAL_VERSION_CHANGED")
            authority = ExecutionAuthority(session).recheck(context,
                session.get(__import__("backend.app.db.models", fromlist=["ToolDefinition"]).ToolDefinition, row.tool_id).key,
                row.tool_version)
            claimed = session.execute(update(ApprovalRequest).where(ApprovalRequest.id == approval_id,
                ApprovalRequest.status == "approved").values(status="executing", claimed_at=utcnow(),
                                                               error_code=None, error_detail=None)).rowcount
            if claimed != 1: raise PlatformToolError("APPROVAL_IN_PROGRESS")
            session.commit()
        try:
            result = self.side_effect(arguments, context)
        except Exception:
            with self.session_factory() as session:
                try:
                    row = session.scalar(select(ApprovalRequest).where(ApprovalRequest.id == approval_id).with_for_update())
                    if row and row.status == "executing" and row.result_json is None:
                        row.status = "retryable"; row.error_code = "SIDE_EFFECT_FAILED"; row.error_detail = None
                        run = session.get(AgentRun, row.run_id)
                        if run:
                            run.status = "retry_required"; run.error_code = "APPROVAL_RETRY_REQUIRED"; run.finished_at = utcnow()
                        audit_approval(session, row, "approval_retryable", row.error_code)
                        session.commit()
                except Exception:
                    session.rollback()
                    raise
            raise PlatformToolError("APPROVAL_RETRY_REQUIRED")
        with self.session_factory() as session:
            row = session.scalar(select(ApprovalRequest).where(ApprovalRequest.id == approval_id).with_for_update())
            if row.result_json is None:
                row.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
                row.status = "consumed"; row.consumed_at = utcnow(); row.error_code = None; row.error_detail = None
                audit_approval(session, row, "approval_consumed")
                session.commit()
            return json.loads(row.result_json)
