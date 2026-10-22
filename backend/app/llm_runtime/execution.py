from backend.app.llm_runtime.quotas import QuotaService
from backend.app.llm_runtime.runtime_service import ModelRuntimeService


class BudgetedModel:
    def __init__(self, session_factory, household_id: str, purpose: str):
        self.session_factory = session_factory
        self.household_id = household_id
        self.purpose = purpose

    def invoke(self, messages):
        with self.session_factory() as session:
            service = ModelRuntimeService(session)
            resolved = service.resolve(self.purpose, self.household_id)
            budget = resolved.snapshot.profile["agent_token_budget"] or resolved.snapshot.profile["max_tokens"]
            if budget is None or budget <= 0:
                raise RuntimeError("MODEL_REQUEST_BUDGET_NOT_CONFIGURED")
            usage = QuotaService(session).reserve(self.household_id, resolved.snapshot.model_id, budget)
            try:
                result = service.build_chat_model(resolved).invoke(messages)
            except Exception:
                QuotaService(session).complete(usage.id, status="failed")
                raise
            QuotaService(session).complete(usage.id)
            return result


def purpose_model_resolver(session_factory, household_id: str):
    return lambda purpose: BudgetedModel(session_factory, household_id, purpose)
