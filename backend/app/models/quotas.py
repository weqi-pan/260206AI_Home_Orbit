from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from backend.app.db.models import LlmModel, ModelUsage, QuotaIncident, UsageQuota


class QuotaExceeded(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class QuotaService:
    def __init__(self, session):
        self.session = session

    def configure(self, household_id: str, monthly_limit: int, requests_per_minute: int, agent_token_budget: int | None):
        quota = self.session.scalar(select(UsageQuota).where(UsageQuota.household_id == household_id))
        if not quota:
            quota = UsageQuota(household_id=household_id)
            self.session.add(quota)
        quota.monthly_limit = monthly_limit
        quota.requests_per_minute = requests_per_minute
        quota.agent_token_budget = agent_token_budget
        self.session.commit()
        return quota

    def _reject(self, household_id: str, model_id: str, code: str):
        """Persist the over-limit incident (governance history) then raise the stable code."""
        self.session.add(QuotaIncident(household_id=household_id, model_id=model_id or "", code=code))
        self.session.commit()
        raise QuotaExceeded(code)

    def current_usage(self, household_id: str) -> int:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        return self.session.scalar(select(func.sum(ModelUsage.tokens)).where(
            ModelUsage.household_id == household_id, ModelUsage.month == month
        )) or 0

    def reserve(self, household_id: str, model_id: str, tokens: int):
        quota = self.session.scalar(select(UsageQuota).where(UsageQuota.household_id == household_id).with_for_update())
        model = self.session.scalar(select(LlmModel).where(LlmModel.id == model_id).with_for_update())
        if quota is None:
            self._reject(household_id, model_id, "HOUSEHOLD_QUOTA_NOT_CONFIGURED")
        if model is None:
            self._reject(household_id, model_id, "MODEL_NOT_CONFIGURED")
        minute_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        recent_requests = self.session.scalar(select(func.count()).select_from(ModelUsage).where(
            ModelUsage.household_id == household_id, ModelUsage.model_id == model_id, ModelUsage.created_at >= minute_ago
        )) or 0
        if recent_requests >= model.requests_per_minute:
            self._reject(household_id, model_id, "MODEL_RATE_LIMIT_EXCEEDED")
        household_requests = self.session.scalar(select(func.count()).select_from(ModelUsage).where(
            ModelUsage.household_id == household_id, ModelUsage.created_at >= minute_ago
        )) or 0
        if household_requests >= quota.requests_per_minute:
            self._reject(household_id, model_id, "HOUSEHOLD_RATE_LIMIT_EXCEEDED")
        if quota.agent_token_budget is not None and tokens > quota.agent_token_budget:
            self._reject(household_id, model_id, "AGENT_TOKEN_BUDGET_EXCEEDED")
        if self.current_usage(household_id) + tokens > quota.monthly_limit:
            self._reject(household_id, model_id, "HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED")
        usage = ModelUsage(household_id=household_id, model_id=model_id,
                           month=datetime.now(timezone.utc).strftime("%Y-%m"), tokens=tokens,
                           reserved_tokens=tokens, status="reserved")
        self.session.add(usage)
        self.session.commit()
        self.session.refresh(usage)
        return usage

    def complete(self, usage_id: str, actual_tokens: int | None = None, status: str = "completed"):
        usage = self.session.get(ModelUsage, usage_id)
        if usage:
            usage.tokens = min(usage.reserved_tokens, actual_tokens) if actual_tokens is not None else usage.reserved_tokens
            usage.status = status
            self.session.commit()

    def release_expired(self, older_than_minutes: int = 15, now: datetime | None = None) -> int:
        """Reconcile crashed reservations: mark old ``reserved`` rows ``released``.

        Conservative budget charging: released rows keep their tokens counted
        in ``current_usage`` so a crashed reservation never frees budget.
        """
        cutoff = (now or datetime.now(timezone.utc).replace(tzinfo=None)) - timedelta(minutes=older_than_minutes)
        stale = self.session.scalars(select(ModelUsage).where(
            ModelUsage.status == "reserved", ModelUsage.created_at <= cutoff)).all()
        for usage in stale:
            usage.status = "released"
        if stale:
            self.session.commit()
        return len(stale)

    def consume(self, household_id: str, model_id: str, tokens: int):
        usage = self.reserve(household_id, model_id, tokens)
        self.complete(usage.id, tokens)
        return usage
