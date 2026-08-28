from types import SimpleNamespace


def test_agent_tools_use_runtime_context_identity(client, app):
    from backend.agent.tools.agent_tools import create_ticket, fetch_external_data, register_restock_notify
    from backend.agent.tools.gateways import ToolRepositories
    from backend.app.db.models import Account, HouseholdMember
    from sqlalchemy import select

    with app.state.database.session_factory() as db:
        account_id = db.scalar(select(Account.id).where(Account.username == "demo"))
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account_id))
    runtime = SimpleNamespace(context={"account_id": account_id, "user_id": "1001", "city": "杭州",
                                       "household_id": household_id,
                                       "repositories": ToolRepositories(app.state.database.session_factory)})
    assert "1001" in fetch_external_data.func(month="2026-05", runtime=runtime)
    result = register_restock_notify.func(model_id="R1-Pro", runtime=runtime)
    assert result["account_id"] == account_id
    ticket = create_ticket.func(title="工具工单", content="问题描述", runtime=runtime)
    assert ticket["account_id"] == account_id


def test_agent_restock_and_ticket_tools_are_idempotent(client, app):
    from backend.agent.tools.agent_tools import create_ticket, register_restock_notify
    from backend.agent.tools.gateways import ToolRepositories
    from backend.app.db.models import Account, ChatSession, RestockNotification, Ticket
    from sqlalchemy import func, select

    with app.state.database.session_factory.begin() as db:
        account_id = db.scalar(select(Account.id).where(Account.username == "demo"))
        from backend.app.db.models import HouseholdMember
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account_id))
        chat = ChatSession(account_id=account_id, household_id=household_id)
        db.add(chat)
        db.flush()
        session_id = chat.id
    runtime = SimpleNamespace(context={
        "account_id": account_id,
        "household_id": household_id,
        "session_id": session_id,
        "repositories": ToolRepositories(app.state.database.session_factory),
    })

    first_notice = register_restock_notify.func(model_id="R1-Pro", runtime=runtime)
    second_notice = register_restock_notify.func(model_id="R1-Pro", runtime=runtime)
    first_ticket = create_ticket.func(title="相同故障", content="第一次", runtime=runtime)
    second_ticket = create_ticket.func(title="相同故障", content="第二次", runtime=runtime)

    assert first_notice["id"] == second_notice["id"]
    assert first_ticket["id"] == second_ticket["id"]
    with app.state.database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(RestockNotification).where(
            RestockNotification.account_id == account_id, RestockNotification.model_id == "R1-Pro"
        )) == 1
        assert db.scalar(select(func.count()).select_from(Ticket).where(
            Ticket.account_id == account_id, Ticket.session_id == session_id, Ticket.status == "open",
            Ticket.title == "相同故障",
        )) == 1


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
