import hashlib
import json
from sqlalchemy import select

from backend.agent.platform.catalog import BuiltinToolRegistry
from backend.agent.platform.audit import ToolAuditService
from backend.app.db.models import ApprovalRequest, ToolDefinition, ToolPermission
from backend.app.db.base import utcnow


class PlatformToolError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ApprovalRequired(PlatformToolError):
    def __init__(self, approval_id):
        self.approval_id = approval_id
        super().__init__("APPROVAL_REQUIRED")


def arguments_digest(arguments):
    body = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class PlatformToolExecutor:
    def __init__(self, session, registry=None, side_effect=None):
        self.session = session
        self.registry = registry or BuiltinToolRegistry.default()
        self.side_effect = side_effect

    def _definition(self, key):
        row = self.session.scalar(select(ToolDefinition).where(ToolDefinition.key == key, ToolDefinition.enabled.is_(True)))
        if not row:
            raise PlatformToolError("PERMISSION_REQUIRED")
        return row

    def _authorize(self, row, context):
        permission = self.session.scalar(select(ToolPermission).where(
            ToolPermission.household_id == context.household_id, ToolPermission.tool_id == row.id,
            ToolPermission.tool_version == row.version,
        ))
        if not permission:
            raise PlatformToolError("PERMISSION_REQUIRED")

    def execute(self, key, arguments, context, idempotency_key):
        started_at = utcnow()
        row = self._definition(key)
        self._authorize(row, context)
        tool = self.registry.resolve(key)
        if row.risk_level == "high":
            existing = self.session.scalar(select(ApprovalRequest).where(ApprovalRequest.idempotency_key == idempotency_key))
            if existing:
                raise ApprovalRequired(existing.id)
            approval = ApprovalRequest(
                household_id=context.household_id, run_id=context.run_id, principal_id=context.principal_id,
                tool_id=row.id, tool_version=row.version, deployment_id=context.deployment_id,
                deployment_revision=context.deployment_revision, agent_version_id=context.agent_version_id,
                arguments_digest=arguments_digest(arguments), arguments_json=json.dumps(arguments, sort_keys=True),
                idempotency_key=idempotency_key,
            )
            self.session.add(approval); self.session.commit(); self.session.refresh(approval)
            raise ApprovalRequired(approval.id)
        result = tool.handler(arguments, context)
        ToolAuditService(self.session).record(context=context, tool_id=row.id, tool_version=row.version,
                                              status="success", started_at=started_at,
                                              idempotency_key=idempotency_key, arguments=arguments, result=result)
        return result

    def approve(self, approval_id, principal_id, expires_at):
        row = self.session.get(ApprovalRequest, approval_id)
        row.status = "approved"; row.approved_by_account_id = principal_id; row.expires_at = expires_at
        self.session.commit()
        return row

    def resume(self, approval_id, context):
        row = self.session.get(ApprovalRequest, approval_id)
        if not row or row.household_id != context.household_id or row.run_id != context.run_id:
            raise PlatformToolError("PERMISSION_REQUIRED")
        if row.result_json is not None:
            return json.loads(row.result_json)
        if row.status != "approved":
            raise PlatformToolError("APPROVAL_REQUIRED")
        if not row.expires_at or row.expires_at <= utcnow():
            raise PlatformToolError("APPROVAL_EXPIRED")
        if row.deployment_id != context.deployment_id or row.deployment_revision != context.deployment_revision or row.agent_version_id != context.agent_version_id:
            raise PlatformToolError("APPROVAL_EXPIRED")
        definition = self.session.get(ToolDefinition, row.tool_id)
        self._authorize(definition, context)
        arguments = json.loads(row.arguments_json)
        handler = self.side_effect or self.registry.resolve(definition.key).handler
        result = handler(arguments, context)
        row.result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
        row.status = "consumed"; row.consumed_at = utcnow(); self.session.commit()
        ToolAuditService(self.session).record(context=context, tool_id=row.tool_id, tool_version=row.tool_version,
                                              status="success", started_at=row.created_at,
                                              idempotency_key=f"{row.idempotency_key}:execution",
                                              arguments=arguments, result=result)
        return result
