import hashlib
import json

from backend.agent.platform.audit import ToolAuditService
from backend.agent.platform.audit import redact
from backend.agent.platform.authority import ExecutionAuthority
from backend.agent.platform.catalog import BuiltinToolRegistry
from backend.agent.platform.policy_errors import ApprovalRequired, PlatformToolError
from backend.app.db.base import utcnow
from backend.app.db.models import ApprovalRequest
from backend.app.db.models import ToolDefinition
from sqlalchemy import select


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def arguments_digest(arguments):
    return hashlib.sha256(canonical_json(arguments).encode("utf-8")).hexdigest()


def invocation_digest(context, tool_key, tool_version, arguments, invocation_id):
    payload = {"household_id": context.household_id, "run_id": context.run_id,
               "deployment_id": context.deployment_id, "deployment_revision": context.deployment_revision,
               "agent_version_id": context.agent_version_id, "tool_key": tool_key, "tool_version": tool_version,
               "arguments_digest": arguments_digest(arguments), "invocation_id": invocation_id}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class PlatformToolExecutor:
    def __init__(self, session, registry=None, side_effect=None):
        self.session = session
        self.registry = registry or BuiltinToolRegistry.default()
        self.side_effect = side_effect

    def execute(self, key, arguments, context, invocation_id):
        started_at = utcnow()
        try:
            authority = ExecutionAuthority(self.session, self.registry).recheck(context, key,
                context.grants.get(key, {}).get("version", ""))
        except PlatformToolError as exc:
            definition = self.session.scalar(select(ToolDefinition).where(ToolDefinition.key == key))
            if definition:
                denied_key = invocation_digest(context, key, definition.version, arguments, invocation_id)
                ToolAuditService(self.session).record(context=context, tool_id=definition.id,
                    tool_version=definition.version, status="permission_denied", error_code=exc.code,
                    started_at=started_at, idempotency_key=f"{denied_key}:denied", arguments=arguments, result={})
            raise
        invocation_key = invocation_digest(context, key, authority.version, arguments, invocation_id)
        tool = self.registry.resolve(key)
        if authority.risk_level == "high":
            existing = self.session.scalar(select(ApprovalRequest).where(ApprovalRequest.idempotency_key == invocation_key))
            if existing: raise ApprovalRequired(existing.id)
            approval = ApprovalRequest(household_id=context.household_id, run_id=context.run_id,
                principal_id=context.principal_id, tool_id=authority.tool_id, tool_version=authority.version,
                deployment_id=context.deployment_id, deployment_revision=context.deployment_revision,
                agent_version_id=context.agent_version_id, arguments_digest=arguments_digest(arguments),
                arguments_json=canonical_json(redact(arguments)), idempotency_key=invocation_key,
                checkpoint_ns=f"{context.household_id}/{context.session_id}/{context.run_id}",
                consent_digest=hashlib.sha256(canonical_json(dict(context.consent)).encode("utf-8")).hexdigest())
            self.session.add(approval); self.session.commit(); self.session.refresh(approval)
            ToolAuditService(self.session).record(context=context, tool_id=authority.tool_id,
                tool_version=authority.version, status="approval_requested", started_at=started_at,
                idempotency_key=f"{invocation_key}:requested", arguments=arguments, result={})
            raise ApprovalRequired(approval.id)
        try:
            result = (self.side_effect or tool.handler)(arguments, context)
        except Exception:
            ToolAuditService(self.session).record(context=context, tool_id=authority.tool_id,
                tool_version=authority.version, status="failed", error_code="TOOL_EXECUTION_FAILED",
                started_at=started_at, idempotency_key=f"{invocation_key}:failed", arguments=arguments, result={})
            raise
        ToolAuditService(self.session).record(context=context, tool_id=authority.tool_id, tool_version=authority.version,
            status="success", started_at=started_at, idempotency_key=invocation_key, arguments=arguments, result=result)
        return result

    def approve(self, approval_id, principal_id, expires_at):
        row = self.session.get(ApprovalRequest, approval_id)
        if not row or row.status != "pending": raise PlatformToolError("APPROVAL_STATE_CONFLICT")
        row.status = "approved"; row.approved_by_account_id = principal_id; row.expires_at = expires_at
        self.session.commit(); return row

    def resume(self, approval_id, context):
        raise PlatformToolError("GRAPH_RESUME_REQUIRED")
