import json
from backend.app.db.models import ToolExecution
from backend.app.db.base import utcnow


SENSITIVE_KEYS = {"authorization", "password", "token", "secret", "credential", "api_key", "query", "content", "text"}


def redact(value, key=""):
    if key.lower() in SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {item_key: redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class ToolAuditService:
    def __init__(self, session):
        self.session = session

    def record(self, *, context, tool_id, tool_version, status, started_at, idempotency_key,
               arguments, result=None, error_code=None):
        finished_at = utcnow()
        row = ToolExecution(
            household_id=context.household_id, run_id=context.run_id, tool_id=tool_id,
            principal_id=context.principal_id, agent_version_id=context.agent_version_id,
            skill_versions_json=json.dumps(context.skill_version_ids), tool_version=tool_version,
            model_id=context.model_id, deployment_id=context.deployment_id,
            deployment_revision=context.deployment_revision, status=status, error_code=error_code,
            idempotency_key=idempotency_key,
            arguments_json=json.dumps(redact(arguments), ensure_ascii=False, sort_keys=True),
            result_json=json.dumps(redact(result or {}), ensure_ascii=False, sort_keys=True),
            started_at=started_at, finished_at=finished_at,
            duration_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
            redaction_schema_version=1,
        )
        self.session.add(row); self.session.commit()
        return row
