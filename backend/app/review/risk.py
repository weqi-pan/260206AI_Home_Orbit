# -*- coding: utf-8 -*-
"""Phase 7 advisory review risk summary.

Uses the ``review_model`` purpose through household-bound BudgetedModel
semantics (quota reservation against the creator household) to produce a risk
summary stored on the review submission. The output is strictly advisory:
this service NEVER writes submission status — human admin decision is the
only approval path. Model-unready degrades to pending-without-summary
(``unavailable`` with a stable code) instead of blocking the queue.
"""
import json

from backend.app.db.base import utcnow


class ReviewRiskService:
    purpose = "review_model"

    def __init__(self, session_factory, household_id, model=None):
        self.session_factory = session_factory
        self.household_id = household_id
        self._model = model  # 注入确定性模型（测试）；生产走 BudgetedModel

    def _budgeted_model(self):
        from backend.app.models.execution import BudgetedModel

        return BudgetedModel(self.session_factory, self.household_id, self.purpose)

    def build_messages(self, payload: dict) -> list[dict]:
        system = ("你是家庭助手平台的公开内容审核助手。仅输出风险摘要文本，供管理员参考；"
                  "你不决定是否通过，平台不会依据你的输出改变审核状态。")
        user = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def platform_risk_level(self, payload: dict) -> str:
        risks = {tool.get("risk_level") for tool in payload.get("tools", [])}
        if risks & {"reversible", "high"}:
            return "elevated"
        return "standard"

    def summarize(self, submission, payload: dict) -> dict:
        """Produces the advisory summary for one submission; NEVER touches submission.status.

        Returns {"status": "done", "summary", "risk_level"} or
        {"status": "unavailable", "code": <stable code>}.
        """
        from backend.app.models.quotas import QuotaExceeded
        from backend.app.models.runtime_service import ModelRuntimeError

        model = self._model or self._budgeted_model()
        try:
            result = model.invoke(self.build_messages(payload))
            summary = str(getattr(result, "content", "") or "")[:2000].strip()
        except (ModelRuntimeError, QuotaExceeded) as exc:
            return self._unavailable(submission, getattr(exc, "code", "MODEL_NOT_CONFIGURED"))
        except Exception as exc:  # 任何模型侧异常都不阻塞队列
            code = getattr(exc, "code", None)
            return self._unavailable(submission, code if code else "MODEL_NOT_CONFIGURED")
        if not summary:
            return self._unavailable(submission, "MODEL_EMPTY_RESPONSE")
        stored = {"summary": summary, "risk_level": self.platform_risk_level(payload),
                  "generated_at": utcnow().isoformat()}
        submission.risk_summary_status = "done"
        submission.risk_summary_json = json.dumps(stored, ensure_ascii=False, sort_keys=True)
        return {"status": "done", **stored}

    def _unavailable(self, submission, code: str) -> dict:
        submission.risk_summary_status = "unavailable"
        submission.risk_summary_json = json.dumps({"code": code}, ensure_ascii=False, sort_keys=True)
        return {"status": "unavailable", "code": code}


def risk_payload(session, submission) -> dict:
    """Compact, deterministic payload describing the submission under review."""
    from sqlalchemy import select

    from backend.app.db.models import (AgentToolBinding, AgentVersion, CreatorDraft, CreatorTrialReport,
                                       ToolDefinition)

    definition = {}
    version = session.get(AgentVersion, submission.agent_version_id)
    if version is not None:
        definition = json.loads(version.definition_json or "{}")
    tools = []
    if version is not None:
        rows = session.scalars(select(ToolDefinition).join(
            AgentToolBinding, AgentToolBinding.tool_id == ToolDefinition.id).where(
            AgentToolBinding.agent_version_id == version.id,
            AgentToolBinding.household_id == submission.household_id).order_by(ToolDefinition.key)).all()
        tools = [{"key": row.key, "risk_level": row.risk_level, "version": row.version} for row in rows]
    trial = session.scalar(select(CreatorTrialReport).where(
        CreatorTrialReport.household_id == submission.household_id,
        CreatorTrialReport.draft_id == submission.draft_id).order_by(
        CreatorTrialReport.created_at.desc(), CreatorTrialReport.id.desc()))
    return {
        "name": submission.name,
        "description": submission.description,
        "category": submission.category,
        "prompt": (definition.get("prompt", "") or "")[:2000],
        "budgets": definition.get("budgets", {}),
        "has_workflow": bool(definition.get("workflow")),
        "tools": tools,
        "sandbox": {"status": trial.status if trial else None,
                    "error_code": trial.error_code if trial else None},
    }
