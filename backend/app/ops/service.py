# -*- coding: utf-8 -*-
"""Phase 8 operations overview service: dashboard metrics computed from real
records with explicit time windows using grouped queries only (no in-memory
full scans, no fabricated numbers)."""
from datetime import timedelta

from sqlalchemy import Integer, func, select

from backend.app.db.base import utcnow
from backend.app.db.models import (AgentInstallation, AgentRun, AgentToolBinding, CeleryTaskRecord, Household,
                                   LlmModel, ModelPurposeBinding, ModelUsage, QuotaIncident, ReviewSubmission,
                                   ToolDefinition, ToolExecution, UsageQuota)

MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 365
TREND_DAYS = 14


class OpsOverviewService:
    def __init__(self, session):
        self.session = session

    def overview(self, window_days: int = 7) -> dict:
        since = utcnow() - timedelta(days=window_days)
        return {
            "window_days": window_days,
            "households": self._households(since),
            "installations": self._installations(),
            "agent_runs": self._agent_runs(since),
            "tool_executions": self._tool_executions(since),
            "celery_tasks": self._celery_tasks(since),
            "review": self._review(since),
            "model_usage": self._model_usage(since),
            "quota_incidents": self._quota_incidents(since),
            "risk": self._risk(since),
        }

    def _households(self, since):
        total = self.session.scalar(select(func.count()).select_from(Household)) or 0
        new_in_window = self.session.scalar(select(func.count()).select_from(Household).where(
            Household.created_at >= since)) or 0
        day = func.date(Household.created_at)
        growth = self.session.execute(select(day, func.count()).where(Household.created_at >= since).group_by(
            day).order_by(day)).all()
        return {"total": total, "new_in_window": new_in_window,
                "growth_by_day": [{"day": str(row[0]), "count": row[1]} for row in growth]}

    def _installations(self):
        active = self.session.scalar(select(func.count()).select_from(AgentInstallation).where(
            AgentInstallation.status == "installed", AgentInstallation.authority_slot == "active")) or 0
        households_active = self.session.scalar(
            select(func.count(func.distinct(AgentInstallation.household_id))).where(
                AgentInstallation.status == "installed",
                AgentInstallation.authority_slot == "active")) or 0
        return {"active": active, "households_with_active": households_active}

    def _agent_runs(self, since):
        by_status = dict(self.session.execute(select(AgentRun.status, func.count()).where(
            AgentRun.created_at >= since).group_by(AgentRun.status)).all())
        total = sum(by_status.values())
        day = func.date(AgentRun.created_at)
        by_day = self.session.execute(select(day, func.count()).where(
            AgentRun.created_at >= since).group_by(day).order_by(day)).all()
        return {"total_in_window": total, "by_status": by_status,
                "by_day": [{"day": str(row[0]), "count": row[1]} for row in by_day]}

    def _tool_executions(self, since):
        by_status = dict(self.session.execute(select(ToolExecution.status, func.count()).where(
            ToolExecution.started_at >= since).group_by(ToolExecution.status)).all())
        return {"total_in_window": sum(by_status.values()), "by_status": by_status,
                "permission_denials": by_status.get("permission_denied", 0),
                "failed": by_status.get("failed", 0)}

    def _celery_tasks(self, since):
        by_status = dict(self.session.execute(select(CeleryTaskRecord.status, func.count()).where(
            CeleryTaskRecord.created_at >= since).group_by(CeleryTaskRecord.status)).all())
        total = sum(by_status.values())
        failures = by_status.get("failed", 0) + by_status.get("dead", 0)
        day = func.date(CeleryTaskRecord.created_at)
        by_day = self.session.execute(select(day, func.count()).where(
            CeleryTaskRecord.created_at >= since, CeleryTaskRecord.status == "succeeded").group_by(
            day).order_by(day)).all()
        return {"total_in_window": total, "by_status": by_status,
                "failure_rate": (failures / total) if total else 0.0,
                "throughput_by_day": [{"day": str(row[0]), "count": row[1]} for row in by_day]}

    def _review(self, since):
        queue_depth = self.session.scalar(select(func.count()).select_from(ReviewSubmission).where(
            ReviewSubmission.status == "review_pending")) or 0
        decided_in_window = self.session.scalar(select(func.count()).select_from(ReviewSubmission).where(
            ReviewSubmission.status.in_(["approved", "rejected", "returned"]),
            ReviewSubmission.decided_at >= since)) or 0
        if self.session.get_bind().dialect.name == "mysql":
            duration = func.unix_timestamp(ReviewSubmission.decided_at) - func.unix_timestamp(
                ReviewSubmission.created_at)
        else:
            duration = func.cast(func.strftime("%s", ReviewSubmission.decided_at), Integer) - func.cast(
                func.strftime("%s", ReviewSubmission.created_at), Integer)
        avg_turnaround = self.session.scalar(select(func.avg(duration)).where(
            ReviewSubmission.status.in_(["approved", "rejected", "returned"]),
            ReviewSubmission.decided_at >= since))
        by_action = dict(self.session.execute(select(ReviewSubmission.status, func.count()).where(
            ReviewSubmission.decided_at >= since,
            ReviewSubmission.status.in_(["approved", "rejected", "returned"])).group_by(
            ReviewSubmission.status)).all())
        return {"queue_depth": queue_depth, "decided_in_window": decided_in_window,
                "avg_turnaround_seconds": round(float(avg_turnaround), 1) if avg_turnaround is not None else None,
                "decided_by_action": by_action}

    def _model_usage(self, since):
        totals = self.session.execute(select(func.count(), func.coalesce(func.sum(ModelUsage.tokens), 0)).where(
            ModelUsage.created_at >= since)).one()
        model_names = dict(self.session.execute(select(LlmModel.id, LlmModel.model_name)).all())
        by_model_rows = self.session.execute(select(ModelUsage.model_id, func.count(),
                                                    func.coalesce(func.sum(ModelUsage.tokens), 0)).where(
            ModelUsage.created_at >= since).group_by(ModelUsage.model_id).order_by(
            func.sum(ModelUsage.tokens).desc())).all()
        purpose_map: dict[str, list[str]] = {}
        for purpose, model_id in self.session.execute(select(ModelPurposeBinding.purpose,
                                                             ModelPurposeBinding.model_id)).all():
            purpose_map.setdefault(model_id, []).append(purpose)
        by_purpose: dict[str, dict] = {}
        for model_id, count, tokens in by_model_rows:
            for purpose in purpose_map.get(model_id, ["unbound"]):
                bucket = by_purpose.setdefault(purpose, {"purpose": purpose, "requests": 0, "tokens": 0})
                bucket["requests"] += count
                bucket["tokens"] += tokens
        day = func.date(ModelUsage.created_at)
        by_day_rows = self.session.execute(select(day, func.count(),
                                                  func.coalesce(func.sum(ModelUsage.tokens), 0)).where(
            ModelUsage.created_at >= since).group_by(day).order_by(day)).all()
        return {"requests": totals[0], "tokens": totals[1],
                "by_model": [{"model_id": model_id, "model_name": model_names.get(model_id, model_id),
                              "requests": count, "tokens": tokens} for model_id, count, tokens in by_model_rows],
                "by_purpose": sorted(by_purpose.values(), key=lambda item: -item["tokens"]),
                "by_day": [{"day": str(row[0]), "requests": row[1], "tokens": row[2]} for row in by_day_rows]}

    def _quota_incidents(self, since):
        by_code = dict(self.session.execute(select(QuotaIncident.code, func.count()).where(
            QuotaIncident.created_at >= since).group_by(QuotaIncident.code)).all())
        return {"count": sum(by_code.values()), "by_code": by_code}

    def _risk(self, since):
        high_risk_approvals = self.session.scalar(
            select(func.count(func.distinct(ReviewSubmission.id))).select_from(ReviewSubmission).join(
                AgentToolBinding, AgentToolBinding.agent_version_id == ReviewSubmission.agent_version_id).join(
                ToolDefinition, ToolDefinition.id == AgentToolBinding.tool_id).where(
                ReviewSubmission.status == "approved", ReviewSubmission.decided_at >= since,
                AgentToolBinding.household_id == ReviewSubmission.household_id,
                ToolDefinition.risk_level == "high")) or 0
        permission_denials = self.session.scalar(select(func.count()).select_from(ToolExecution).where(
            ToolExecution.status == "permission_denied", ToolExecution.started_at >= since)) or 0
        return {"high_risk_approvals": high_risk_approvals, "permission_denials": permission_denials}


class QuotaGovernanceService:
    def __init__(self, session):
        self.session = session

    def household_quota_view(self, household_id: str):
        quota = self.session.scalar(select(UsageQuota).where(UsageQuota.household_id == household_id))
        month = utcnow().strftime("%Y-%m")
        usage = self.session.execute(select(func.count(), func.coalesce(func.sum(ModelUsage.tokens), 0)).where(
            ModelUsage.household_id == household_id, ModelUsage.month == month)).one()
        trend_since = utcnow() - timedelta(days=TREND_DAYS)
        day = func.date(ModelUsage.created_at)
        trend_rows = self.session.execute(select(day, func.count(),
                                                 func.coalesce(func.sum(ModelUsage.tokens), 0)).where(
            ModelUsage.household_id == household_id, ModelUsage.created_at >= trend_since).group_by(
            day).order_by(day)).all()
        incidents = self.session.scalars(select(QuotaIncident).where(
            QuotaIncident.household_id == household_id).order_by(
            QuotaIncident.created_at.desc(), QuotaIncident.id.desc()).limit(20)).all()
        model_names = dict(self.session.execute(select(LlmModel.id, LlmModel.model_name)).all())
        return {
            "household_id": household_id,
            "quota": ({"monthly_limit": quota.monthly_limit, "requests_per_minute": quota.requests_per_minute,
                       "agent_token_budget": quota.agent_token_budget} if quota else None),
            "usage": {"month": month, "requests": usage[0], "tokens": usage[1]},
            "trend": [{"day": str(row[0]), "requests": row[1], "tokens": row[2]} for row in trend_rows],
            "incidents": [{"id": row.id, "model_id": row.model_id,
                           "model_name": model_names.get(row.model_id, row.model_id),
                           "code": row.code, "created_at": row.created_at} for row in incidents],
        }

    def quota_incidents(self, *, household_id=None, code=None, time_from=None, time_to=None, page=1, page_size=20):
        statement = select(QuotaIncident).order_by(QuotaIncident.created_at.desc(), QuotaIncident.id.desc())
        if household_id:
            statement = statement.where(QuotaIncident.household_id == household_id)
        if code:
            statement = statement.where(QuotaIncident.code == code)
        if time_from:
            statement = statement.where(QuotaIncident.created_at >= time_from)
        if time_to:
            statement = statement.where(QuotaIncident.created_at <= time_to)
        page, page_size = max(1, page), min(100, max(1, page_size))
        total = self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = self.session.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
        items = [{"id": row.id, "household_id": row.household_id, "model_id": row.model_id, "code": row.code,
                  "created_at": row.created_at} for row in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}
