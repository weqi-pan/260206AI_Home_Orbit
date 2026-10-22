# -*- coding: utf-8 -*-
"""Phase 8 hard prerequisite zero: sandbox trial model usage must flow through
the same QuotaService reserve/complete lifecycle as chat (household grant
checks, RPM/monthly/budget reservation, ModelUsage records) and over-limit must
produce a silent stable failure plus a persisted quota incident."""
import json

from sqlalchemy import select

from backend.tests.test_celery_tasks import _home


def _deep_agent_card(respond_text="换算完成：鸡蛋需要8个"):
    return {
        "name": "用量助手", "description": "按人数换算食材", "prompt": "你是用量换算助手。",
        "examples": [], "model_requirements": {"capabilities": ["chat"]}, "skills": [],
        "tools": [], "budgets": {"tool_calls": 8, "tokens": 8000, "wall_time_seconds": 60, "delegation_depth": 0},
        "tests": [{"name": "换算", "input": "四人份", "expected_contains": "8"}],
        "workflow": None,
    }


def _draft(app, household_id, card):
    from backend.app.db.models import CreatorDraft

    with app.state.database.session_factory.begin() as db:
        draft = CreatorDraft(household_id=household_id, title="配额沙盒", status="validated",
                             intent_card_json=json.dumps(card, ensure_ascii=False))
        db.add(draft)
        db.flush()
        return draft.id


def _echo_model(app, monkeypatch):
    from typing import Any

    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class EchoChatModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "quota-echo"

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="换算完成：鸡蛋需要8个"))])

    from backend.app.llm_runtime.runtime_service import ModelRuntimeService

    monkeypatch.setattr(ModelRuntimeService, "build_chat_model", lambda self, resolved: EchoChatModel())
    return EchoChatModel


def _run_trial(app, household_id, account_id, draft_id):
    from backend.app.creator.sandbox import SandboxTrialService

    service = SandboxTrialService(app.state.database.session_factory, household_id, account_id)
    report = service.create_report(draft_id, [])
    return report.id, service.run(report.id)


def test_sandbox_platform_model_usage_reserves_and_completes_quota(client, app, monkeypatch):
    from backend.app.db.models import ModelUsage
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    account_id, household_id = _home(app)
    with app.state.database.session_factory() as db:
        _endpoint, model = configured_runtime(db, household_id)  # max_tokens=8, monthly=100
        model_id = model.id
    _echo_model(app, monkeypatch)
    draft_id = _draft(app, household_id, _deep_agent_card())

    report_id, summary = _run_trial(app, household_id, account_id, draft_id)

    assert summary["status"] == "completed", summary
    assert summary["cases"][0]["status"] == "passed", summary
    with app.state.database.session_factory() as db:
        rows = db.scalars(select(ModelUsage).where(ModelUsage.household_id == household_id,
                                                   ModelUsage.model_id == model_id)).all()
        assert len(rows) == 1  # 每个用例一次预留：与聊天同生命周期
        assert rows[0].status == "completed"
        assert rows[0].reserved_tokens == 8 and rows[0].tokens == 8


def test_sandbox_over_limit_fails_silently_with_stable_code_and_records_incident(client, app, monkeypatch):
    from backend.app.db.models import ModelUsage, QuotaIncident, UsageQuota
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    account_id, household_id = _home(app)
    with app.state.database.session_factory() as db:
        _endpoint, model = configured_runtime(db, household_id, max_tokens=8, monthly=8)
        model_id = model.id
        quota = db.scalar(select(UsageQuota).where(UsageQuota.household_id == household_id))
        quota.monthly_limit = 0  # 月配额已耗尽
        db.commit()
    _echo_model(app, monkeypatch)
    draft_id = _draft(app, household_id, _deep_agent_card())

    report_id, summary = _run_trial(app, household_id, account_id, draft_id)

    assert summary["status"] == "completed"  # 沙盒静默稳定失败，不抛出
    assert summary["cases"][0]["status"] == "failed"
    assert summary["cases"][0]["error_code"] == "HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED"
    with app.state.database.session_factory() as db:
        assert db.scalars(select(ModelUsage).where(ModelUsage.household_id == household_id)).all() == []
        incidents = db.scalars(select(QuotaIncident).where(QuotaIncident.household_id == household_id)).all()
        assert len(incidents) == 1
        assert incidents[0].code == "HOUSEHOLD_MONTHLY_QUOTA_EXCEEDED"
        assert incidents[0].model_id == model_id


def test_sandbox_without_configured_quota_reports_stable_code(client, app, monkeypatch):
    from backend.app.db.models import QuotaIncident, UsageQuota
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    account_id, household_id = _home(app)
    with app.state.database.session_factory() as db:
        configured_runtime(db, household_id)
        quota = db.scalar(select(UsageQuota).where(UsageQuota.household_id == household_id))
        db.delete(quota)
        db.commit()
    _echo_model(app, monkeypatch)
    draft_id = _draft(app, household_id, _deep_agent_card())

    report_id, summary = _run_trial(app, household_id, account_id, draft_id)

    assert summary["cases"][0]["status"] == "failed"
    assert summary["cases"][0]["error_code"] == "HOUSEHOLD_QUOTA_NOT_CONFIGURED"
    with app.state.database.session_factory() as db:
        incidents = db.scalars(select(QuotaIncident).where(QuotaIncident.household_id == household_id)).all()
        assert [row.code for row in incidents] == ["HOUSEHOLD_QUOTA_NOT_CONFIGURED"]
