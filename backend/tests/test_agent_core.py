from types import SimpleNamespace


def test_agent_tools_use_runtime_context_identity(client, app):
    from backend.agent.tools.agent_tools import create_ticket, fetch_external_data, register_restock_notify
    from backend.app.db.models import Account
    from sqlalchemy import select

    with app.state.database.session_factory() as db:
        account_id = db.scalar(select(Account.id).where(Account.username == "demo"))
    runtime = SimpleNamespace(context={"account_id": account_id, "user_id": "1001", "city": "杭州", "session_factory": app.state.database.session_factory})
    assert "1001" in fetch_external_data.func(month="2025-01", runtime=runtime)
    result = register_restock_notify.func(model_id="R1-Pro", runtime=runtime)
    assert result["account_id"] == account_id
    ticket = create_ticket.func(title="工具工单", content="问题描述", runtime=runtime)
    assert ticket["account_id"] == account_id


def test_create_deep_agent_is_lazy_and_has_three_declarative_subagents(monkeypatch):
    from backend.agent import deep_agent

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(deep_agent, "deepagents_create", fake_create)
    monkeypatch.setattr(deep_agent, "get_chat_model", lambda: "qwen3-max-model")
    deep_agent.create_deep_agent()

    assert captured["model"] == "qwen3-max-model"
    assert [item["name"] for item in captured["subagents"]] == ["presales", "aftersales", "report_ticket"]
    assert len(captured["middleware"]) == 3


def test_subagents_are_defined_in_dedicated_modules():
    from backend.agent.subagents import AFTERSALES_SUBAGENT, PRESALES_SUBAGENT, REPORT_TICKET_SUBAGENT

    assert PRESALES_SUBAGENT["name"] == "presales"
    assert AFTERSALES_SUBAGENT["name"] == "aftersales"
    assert REPORT_TICKET_SUBAGENT["name"] == "report_ticket"
