# -*- coding: utf-8 -*-
"""Phase 8 audit query domain: sanitized admin audit writer plus paginated,
filtered, indexed read paths over the platform's real evidence records
(AgentRun, ToolExecution, ModelUsage, CeleryTaskRecord, AdminActionAudit) and
the reservation-vs-completion accounting reconciliation."""
import json
from datetime import datetime, timedelta

from sqlalchemy import func, select

from backend.app.db.base import utcnow
from backend.app.db.models import (Account, AdminActionAudit, AgentDeployment, AgentRun, AgentVersion,
                                   CeleryTaskRecord, LlmModel, MarketEntry, ModelPurposeBinding, ModelUsage,
                                   Notification, ReviewSubmission, ToolExecution)

SENSITIVE_DETAIL_KEYS = {"api_key", "password", "secret", "token", "credential", "credentials", "ciphertext",
                         "nonce", "authorization", "key_version", "api_key_plain"}
MAX_DETAIL_LENGTH = 200
MAX_PAGE_SIZE = 100
UNRECONCILED_WINDOW_MINUTES = 15


def sanitize_detail(value, key: str = ""):
    """Recursively remove secrets from audit payloads; keep JSON-safe scalars."""
    if str(key).lower() in SENSITIVE_DETAIL_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): sanitize_detail(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_detail(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    return text if len(text) <= MAX_DETAIL_LENGTH else text[:MAX_DETAIL_LENGTH] + "…[truncated]"


def audit_admin_action(db, admin, action: str, resource_type: str, resource_id, detail: dict | None = None,
                       household_id: str | None = None, commit: bool = True):
    """Single sanitized writer for every audited mutation/read on the platform."""
    row = AdminActionAudit(principal_id=admin.id, household_id=household_id, action=action,
                           resource_type=resource_type, resource_id=str(resource_id),
                           detail_json=json.dumps(sanitize_detail(detail or {}), ensure_ascii=False, sort_keys=True))
    db.add(row)
    if commit:
        db.commit()
    return row


def audit_read(db, admin, endpoint: str, filters: dict):
    return audit_admin_action(db, admin, "audit.read", "audit_query", endpoint,
                              {"filters": filters}, commit=False)


def clamp_page(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(MAX_PAGE_SIZE, max(1, page_size))


def _window(days: int) -> datetime:
    return utcnow() - timedelta(days=max(0, days))


class AuditQueryService:
    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------- pagination
    def _page(self, statement, page: int, page_size: int):
        page, page_size = clamp_page(page, page_size)
        total = self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = self.session.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
        return rows, total, page, page_size

    # ------------------------------------------------------------- agent runs
    def agent_runs(self, *, status=None, model_id=None, deployment_id=None, household_id=None,
                   time_from=None, time_to=None, page=1, page_size=20):
        statement = select(AgentRun).order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        if status:
            statement = statement.where(AgentRun.status == status)
        if model_id:
            statement = statement.where(AgentRun.model_id == model_id)
        if deployment_id:
            statement = statement.where(AgentRun.deployment_id == deployment_id)
        if household_id:
            statement = statement.where(AgentRun.household_id == household_id)
        if time_from:
            statement = statement.where(AgentRun.created_at >= time_from)
        if time_to:
            statement = statement.where(AgentRun.created_at <= time_to)
        rows, total, page, page_size = self._page(statement, page, page_size)
        items = [{
            "id": row.id, "household_id": row.household_id, "deployment_id": row.deployment_id,
            "deployment_revision": row.deployment_revision, "agent_version_id": row.agent_version_id,
            "model_id": row.model_id, "model_revision": row.model_revision, "principal_id": row.principal_id,
            "session_id": row.session_id, "status": row.status, "error_code": row.error_code,
            "started_at": row.started_at, "finished_at": row.finished_at, "created_at": row.created_at,
        } for row in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    # -------------------------------------------------------- tool executions
    def tool_executions(self, *, run_id=None, tool_id=None, status=None, household_id=None,
                        time_from=None, time_to=None, page=1, page_size=20):
        statement = select(ToolExecution).order_by(ToolExecution.started_at.desc(), ToolExecution.id.desc())
        if run_id:
            statement = statement.where(ToolExecution.run_id == run_id)
        if tool_id:
            statement = statement.where(ToolExecution.tool_id == tool_id)
        if status:
            statement = statement.where(ToolExecution.status == status)
        if household_id:
            statement = statement.where(ToolExecution.household_id == household_id)
        if time_from:
            statement = statement.where(ToolExecution.started_at >= time_from)
        if time_to:
            statement = statement.where(ToolExecution.started_at <= time_to)
        rows, total, page, page_size = self._page(statement, page, page_size)
        items = [self._tool_execution_view(row) for row in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def _tool_execution_view(self, row):
        return {
            "id": row.id, "household_id": row.household_id, "run_id": row.run_id, "tool_id": row.tool_id,
            "principal_id": row.principal_id, "agent_version_id": row.agent_version_id,
            "tool_version": row.tool_version, "model_id": row.model_id, "deployment_id": row.deployment_id,
            "deployment_revision": row.deployment_revision, "status": row.status, "error_code": row.error_code,
            "duration_ms": row.duration_ms, "started_at": row.started_at, "finished_at": row.finished_at,
            "redaction_schema_version": row.redaction_schema_version,
        }

    def tool_execution_detail(self, execution_id: str):
        row = self.session.get(ToolExecution, execution_id)
        if row is None:
            return None
        view = self._tool_execution_view(row)
        view["arguments"] = json.loads(row.arguments_json or "{}")
        view["result"] = json.loads(row.result_json or "{}")
        return view

    # ------------------------------------------------------------ model usage
    def _purpose_model_ids(self, purpose: str | None):
        if not purpose:
            return None
        return set(self.session.scalars(select(ModelPurposeBinding.model_id).where(
            ModelPurposeBinding.purpose == purpose)).all())

    def model_usage(self, *, household_id=None, model_id=None, purpose=None, status=None,
                    time_from=None, time_to=None, page=1, page_size=20):
        statement = select(ModelUsage).order_by(ModelUsage.created_at.desc(), ModelUsage.id.desc())
        if household_id:
            statement = statement.where(ModelUsage.household_id == household_id)
        if model_id:
            statement = statement.where(ModelUsage.model_id == model_id)
        if purpose:
            model_ids = self._purpose_model_ids(purpose)
            if not model_ids:
                return {"items": [], "total": 0, "page": 1, "page_size": min(MAX_PAGE_SIZE, max(1, page_size))}
            statement = statement.where(ModelUsage.model_id.in_(model_ids))
        if status:
            statement = statement.where(ModelUsage.status == status)
        if time_from:
            statement = statement.where(ModelUsage.created_at >= time_from)
        if time_to:
            statement = statement.where(ModelUsage.created_at <= time_to)
        rows, total, page, page_size = self._page(statement, page, page_size)
        items = [{
            "id": row.id, "household_id": row.household_id, "model_id": row.model_id, "month": row.month,
            "tokens": row.tokens, "reserved_tokens": row.reserved_tokens, "status": row.status,
            "created_at": row.created_at,
        } for row in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def usage_accounting(self, *, days: int = 7, household_id=None):
        """Reservation-vs-completion reconciliation and grouped aggregates from real rows."""
        since = _window(days)
        base = select(ModelUsage).where(ModelUsage.created_at >= since)
        if household_id:
            base = base.where(ModelUsage.household_id == household_id)

        by_status = dict(self.session.execute(
            select(ModelUsage.status, func.count()).where(ModelUsage.created_at >= since,
                                                          *([ModelUsage.household_id == household_id] if household_id else []))
            .group_by(ModelUsage.status)).all())
        totals = self.session.execute(
            select(func.count(), func.coalesce(func.sum(ModelUsage.tokens), 0)).where(
                ModelUsage.created_at >= since,
                *([ModelUsage.household_id == household_id] if household_id else []))).one()

        model_names = dict(self.session.execute(select(LlmModel.id, LlmModel.model_name)).all())
        by_model_rows = self.session.execute(
            select(ModelUsage.model_id, func.count(), func.coalesce(func.sum(ModelUsage.tokens), 0)).where(
                ModelUsage.created_at >= since,
                *([ModelUsage.household_id == household_id] if household_id else []))
            .group_by(ModelUsage.model_id).order_by(func.sum(ModelUsage.tokens).desc())).all()
        by_model = [{"model_id": model_id, "model_name": model_names.get(model_id, model_id),
                     "requests": count, "tokens": tokens} for model_id, count, tokens in by_model_rows]

        purpose_map = {}
        for purpose, model_id in self.session.execute(select(ModelPurposeBinding.purpose,
                                                             ModelPurposeBinding.model_id)).all():
            purpose_map.setdefault(model_id, []).append(purpose)
        by_purpose: dict[str, dict] = {}
        for row in by_model_rows:
            purposes = purpose_map.get(row[0]) or ["unbound"]
            for purpose in purposes:
                bucket = by_purpose.setdefault(purpose, {"purpose": purpose, "requests": 0, "tokens": 0})
                bucket["requests"] += row[1]
                bucket["tokens"] += row[2]

        day = func.date(ModelUsage.created_at)
        by_day_rows = self.session.execute(
            select(day, func.count(), func.coalesce(func.sum(ModelUsage.tokens), 0)).where(
                ModelUsage.created_at >= since,
                *([ModelUsage.household_id == household_id] if household_id else []))
            .group_by(day).order_by(day)).all()
        by_day = [{"day": str(row[0]), "requests": row[1], "tokens": row[2]} for row in by_day_rows]

        cutoff = utcnow() - timedelta(minutes=UNRECONCILED_WINDOW_MINUTES)
        stale_statement = select(ModelUsage).where(ModelUsage.status == "reserved",
                                                   ModelUsage.created_at <= cutoff).order_by(
            ModelUsage.created_at.desc()).limit(50)
        if household_id:
            stale_statement = stale_statement.where(ModelUsage.household_id == household_id)
        stale = self.session.scalars(stale_statement).all()
        unreconciled = {
            "count": len(stale),
            "window_minutes": UNRECONCILED_WINDOW_MINUTES,
            "items": [{"id": row.id, "household_id": row.household_id, "model_id": row.model_id,
                       "reserved_tokens": row.reserved_tokens, "created_at": row.created_at} for row in stale],
        }
        return {
            "window_days": days, "totals": {"requests": totals[0], "tokens": totals[1]},
            "by_status": by_status, "by_model": by_model,
            "by_purpose": sorted(by_purpose.values(), key=lambda item: -item["tokens"]),
            "by_day": by_day, "unreconciled": unreconciled,
        }

    # ----------------------------------------------------------- celery tasks
    def celery_tasks(self, *, task_type=None, status=None, household_id=None, error_code=None,
                     time_from=None, time_to=None, page=1, page_size=20):
        statement = select(CeleryTaskRecord).order_by(CeleryTaskRecord.created_at.desc(),
                                                      CeleryTaskRecord.id.desc())
        if task_type:
            statement = statement.where(CeleryTaskRecord.task_type == task_type)
        if status:
            statement = statement.where(CeleryTaskRecord.status == status)
        if household_id:
            statement = statement.where(CeleryTaskRecord.household_id == household_id)
        if error_code:
            statement = statement.where(CeleryTaskRecord.error_code == error_code)
        if time_from:
            statement = statement.where(CeleryTaskRecord.created_at >= time_from)
        if time_to:
            statement = statement.where(CeleryTaskRecord.created_at <= time_to)
        rows, total, page, page_size = self._page(statement, page, page_size)
        items = [self._celery_task_view(row) for row in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def _celery_task_view(self, row):
        return {
            "id": row.id, "idempotency_key": row.idempotency_key, "task_type": row.task_type,
            "household_id": row.household_id, "status": row.status, "attempt": row.attempt,
            "max_attempts": row.max_attempts, "error_code": row.error_code,
            "next_retry_at": row.next_retry_at, "created_at": row.created_at, "updated_at": row.updated_at,
        }

    def celery_task_detail(self, task_id: str):
        row = self.session.get(CeleryTaskRecord, task_id)
        if row is None:
            return None
        view = self._celery_task_view(row)
        view["error_detail"] = row.error_detail
        view["payload"] = sanitize_detail(json.loads(row.payload_json or "{}"))
        view["result"] = sanitize_detail(json.loads(row.result_json or "{}")) if row.result_json else None
        view["audit"] = json.loads(row.audit_json or "{}")
        notifications = self.session.scalars(select(Notification).where(
            Notification.source_type == "celery_task", Notification.source_id == row.id).order_by(
            Notification.created_at.desc())).all()
        view["notifications"] = [{"id": notice.id, "title": notice.title, "kind": notice.kind,
                                  "created_at": notice.created_at} for notice in notifications]
        return view

    # ----------------------------------------------------------- admin actions
    def admin_actions(self, *, actor_id=None, action=None, resource_type=None, household_id=None,
                      time_from=None, time_to=None, page=1, page_size=20):
        statement = select(AdminActionAudit, Account.username).outerjoin(
            Account, Account.id == AdminActionAudit.principal_id).order_by(
            AdminActionAudit.created_at.desc(), AdminActionAudit.id.desc())
        if actor_id:
            statement = statement.where(AdminActionAudit.principal_id == actor_id)
        if action:
            statement = statement.where(AdminActionAudit.action == action)
        if resource_type:
            statement = statement.where(AdminActionAudit.resource_type == resource_type)
        if household_id:
            statement = statement.where(AdminActionAudit.household_id == household_id)
        if time_from:
            statement = statement.where(AdminActionAudit.created_at >= time_from)
        if time_to:
            statement = statement.where(AdminActionAudit.created_at <= time_to)
        page, page_size = clamp_page(page, page_size)
        total = self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = self.session.execute(statement.offset((page - 1) * page_size).limit(page_size)).all()
        items = [{
            "id": row.id, "principal_id": row.principal_id, "actor_username": username,
            "household_id": row.household_id, "action": row.action, "resource_type": row.resource_type,
            "resource_id": row.resource_id, "detail": json.loads(row.detail_json or "{}"),
            "created_at": row.created_at,
        } for row, username in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    # ------------------------------------------------- deployment pointers
    def deployment_pointer_history(self):
        """Per-market-entry and per-official-Agent pointer history from existing
        records: approved submissions move the market pointer; official
        deployments pin household installs to immutable versions."""
        entries = self.session.scalars(select(MarketEntry).order_by(MarketEntry.created_at.desc())).all()
        market = []
        for entry in entries:
            rows = self.session.execute(select(ReviewSubmission, AgentVersion).join(
                AgentVersion, AgentVersion.id == ReviewSubmission.agent_version_id).where(
                ReviewSubmission.entry_id == entry.id, ReviewSubmission.status == "approved").order_by(
                ReviewSubmission.decided_at.desc(), ReviewSubmission.id.desc())).all()
            market.append({
                "entry_id": entry.id, "name": entry.name, "source_household_id": entry.source_household_id,
                "official": entry.official, "status": entry.status,
                "current_version_number": entry.current_version_number,
                "current_version_id": entry.current_version_id,
                "history": [{"version": version.version, "version_id": version.id,
                             "changed_by_account_id": submission.decided_by_account_id,
                             "changed_at": submission.decided_at, "submission_id": submission.id}
                            for submission, version in rows],
            })

        official_rows = self.session.execute(select(AgentDeployment, AgentVersion, Account.username).join(
            AgentVersion, AgentVersion.id == AgentDeployment.agent_version_id).join(
            Account, Account.id == AgentDeployment.created_by_account_id, isouter=True).where(
            AgentDeployment.visibility == "official").order_by(AgentDeployment.created_at.desc())).all()
        deployment_map: dict[str, dict] = {}
        for deployment, version, creator in official_rows:
            record = deployment_map.setdefault(deployment.id, {
                "deployment_id": deployment.id, "household_id": deployment.household_id,
                "name": None, "revision": deployment.revision,
                "agent_version_id": deployment.agent_version_id, "created_by": creator,
                "created_at": deployment.created_at, "history": []})
            record["history"].append({"version": version.version, "version_id": version.id,
                                      "published_at": version.published_at, "changed_by": creator})
        official = sorted(deployment_map.values(), key=lambda item: item["created_at"] or utcnow(), reverse=True)
        return {"market_entries": market, "official": official}

    # ------------------------------------------------- model config revisions
    def model_config_revisions(self):
        """Model configuration revision evidence from existing records."""
        from backend.app.db.models import ModelHealthCheck

        models = self.session.scalars(select(LlmModel).order_by(LlmModel.model_name)).all()
        checks = self.session.scalars(select(ModelHealthCheck).order_by(
            ModelHealthCheck.created_at.desc(), ModelHealthCheck.id.desc())).all()
        by_model: dict[str, list] = {}
        for check in checks:
            by_model.setdefault(check.model_id, []).append(check)
        return [{
            "model_id": model.id, "model_name": model.model_name, "model_type": model.model_type,
            "enabled": model.enabled, "revision": model.revision,
            "latest_health": ({"status": by_model[model.id][0].status, "error_code": by_model[model.id][0].error_code,
                               "checked_at": by_model[model.id][0].created_at} if model.id in by_model else None),
            "history": [{"status": check.status, "error_code": check.error_code,
                         "endpoint_revision": check.endpoint_revision, "model_revision": check.model_revision,
                         "checked_at": check.created_at} for check in by_model.get(model.id, [])[:20]],
        } for model in models]
