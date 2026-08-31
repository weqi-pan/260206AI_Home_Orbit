import json
import time

from sqlalchemy import select


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password}).json()["access_token"]


def register(client, username):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": "Password@123", "city": "杭州"},
    ).json()["access_token"]


class FakeAgentRunner:
    def stream(self, messages, context):
        value = lambda name: context.get(name) if isinstance(context, dict) else getattr(context, name)
        assert value("principal_id") if not isinstance(context, dict) else value("account_id")
        assert value("user_id") == "1001"
        assert value("city") == "上海"
        yield {"event": "token", "data": {"content": "您好"}}
        yield {"event": "tool", "data": {"name": "search_devices", "status": "completed"}}
        yield {"event": "token", "data": {"content": "，已处理"}}


class FakeSentiment:
    def analyze(self, text):
        return {"label": "negative", "score": 0.8}


class FakeFollowup:
    def generate(self, user_text, answer):
        return ["追问1", "追问2", "追问3"]


class FakeTitle:
    def generate(self, user_text, answer):
        return "设备咨询"


def join_title_futures():
    from backend.app.api.chat import pending_title_futures

    for future in list(pending_title_futures()):
        future.result(timeout=10)


class FailingAfterTokenRunner:
    def __init__(self, with_token=True):
        self.with_token = with_token

    def stream(self, messages, context):
        if self.with_token:
            yield {"event": "token", "data": {"content": "已生成部分回答"}}
        raise RuntimeError("agent disconnected")


def test_session_crud_enforces_ownership(client, settings):
    demo = login(client, "demo", settings.demo_initial_password)
    other = register(client, "other-chat-user")
    created = client.post("/api/chat/sessions", headers=bearer(demo), json={}).json()

    assert client.get("/api/chat/sessions", headers=bearer(demo)).json()[0]["id"] == created["id"]
    assert client.patch(
        f"/api/chat/sessions/{created['id']}", headers=bearer(demo), json={"title": "新标题"}
    ).json()["title"] == "新标题"
    assert client.get(f"/api/chat/sessions/{created['id']}/messages", headers=bearer(other)).status_code == 404
    assert client.delete(f"/api/chat/sessions/{created['id']}", headers=bearer(other)).status_code == 404
    assert client.delete(f"/api/chat/sessions/{created['id']}", headers=bearer(demo)).status_code == 204


def test_chat_sse_emits_sequence_persists_messages_feedback_and_first_title(client, app, settings):
    from backend.app.db.models import ChatSession, ConversationHistory

    app.state.agent_runner = FakeAgentRunner()
    app.state.sentiment_service = FakeSentiment()
    app.state.followup_service = FakeFollowup()
    app.state.title_service = FakeTitle()
    token = login(client, "demo", settings.demo_initial_password)
    chat_id = client.post("/api/chat/sessions", headers=bearer(token), json={}).json()["id"]

    with client.stream(
        "POST", f"/api/chat/sessions/{chat_id}/chat", headers=bearer(token), json={"message": "我很不满意"}
    ) as response:
        body = "\n".join(response.iter_lines())

    assert response.status_code == 200
    events = [line.removeprefix("event: ") for line in body.splitlines() if line.startswith("event: ")]
    assert events == ["token", "tool", "token", "sentiment", "followup", "done"]
    with app.state.database.session_factory() as session:
        messages = session.scalars(
            select(ConversationHistory).where(ConversationHistory.session_id == chat_id).order_by(ConversationHistory.id)
        ).all()
        assert [(item.role, item.content) for item in messages] == [("user", "我很不满意"), ("assistant", "您好，已处理")]
        assert messages[0].sentiment == "negative"
        assistant_id = messages[1].id
        join_title_futures()
        assert session.get(ChatSession, chat_id).title == "设备咨询"

    feedback = client.post(
        f"/api/chat/messages/{assistant_id}/feedback", headers=bearer(token), json={"feedback": "like"}
    )
    assert feedback.status_code == 200
    assert feedback.json()["feedback"] == "like"


def test_chat_title_generation_is_fire_and_forget_and_updates_session(client, app, settings):
    from backend.app.db.models import ChatSession

    class SlowTitle:
        delay = 1.2

        def generate(self, user_text, answer):
            time.sleep(self.delay)
            return "慢速生成的标题"

    app.state.agent_runner = FakeAgentRunner()
    app.state.sentiment_service = FakeSentiment()
    app.state.followup_service = FakeFollowup()
    app.state.title_service = SlowTitle()
    token = login(client, "demo", settings.demo_initial_password)
    chat_id = client.post("/api/chat/sessions", headers=bearer(token), json={}).json()["id"]

    started = time.monotonic()
    with client.stream(
        "POST", f"/api/chat/sessions/{chat_id}/chat", headers=bearer(token), json={"message": "标题测试"}
    ) as response:
        body = "\n".join(response.iter_lines())
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert "event: done" in body
    assert elapsed < SlowTitle.delay

    join_title_futures()
    with app.state.database.session_factory() as session:
        assert session.get(ChatSession, chat_id).title == "慢速生成的标题"


def test_chat_title_failure_is_swallowed_and_default_title_kept(client, app, settings):
    from backend.app.db.models import ChatSession

    class FailingTitle:
        def generate(self, user_text, answer):
            raise RuntimeError("title service offline")

    app.state.agent_runner = FakeAgentRunner()
    app.state.sentiment_service = FakeSentiment()
    app.state.followup_service = FakeFollowup()
    app.state.title_service = FailingTitle()
    token = login(client, "demo", settings.demo_initial_password)
    chat_id = client.post("/api/chat/sessions", headers=bearer(token), json={}).json()["id"]

    with client.stream(
        "POST", f"/api/chat/sessions/{chat_id}/chat", headers=bearer(token), json={"message": "标题失败"}
    ) as response:
        body = "\n".join(response.iter_lines())

    assert response.status_code == 200
    assert "event: done" in body
    join_title_futures()
    with app.state.database.session_factory() as session:
        assert session.get(ChatSession, chat_id).title == "新对话"


def test_chat_without_embedding_does_not_inject_vector_store_and_rag_degrades_cleanly(client, app, settings):
    from backend.agent.tools.agent_tools import rag_summarize

    class ContextRunner:
        def bind_model(self, run_id, model): pass
        def stream(self, messages, context):
            assert context.vector_store_service is None
            fallback = rag_summarize.func(query="售后", runtime=type("Runtime", (), {"context": context})())
            assert fallback == "知识增强未启用，暂时无法检索知识库。"
            yield {"event": "token", "data": {"content": "完成"}}

    app.state.agent_runner = ContextRunner()
    app.state.sentiment_service = FakeSentiment()
    app.state.followup_service = FakeFollowup()
    token = login(client, "demo", settings.demo_initial_password)
    chat_id = client.post("/api/chat/sessions", headers=bearer(token), json={}).json()["id"]

    response = client.post(
        f"/api/chat/sessions/{chat_id}/chat", headers=bearer(token), json={"message": "售后问题"}
    )

    assert response.status_code == 200
    assert "event: done" in response.text


def test_chat_stream_error_persists_partial_or_explicit_placeholder(client, app, settings):
    from backend.app.db.models import ConversationHistory

    token = login(client, "demo", settings.demo_initial_password)
    for with_token, expected in [(True, "已生成部分回答\n\n[回答中断]"), (False, "[回答生成失败] 聊天服务暂不可用")]:
        app.state.agent_runner = FailingAfterTokenRunner(with_token)
        chat_id = client.post("/api/chat/sessions", headers=bearer(token), json={}).json()["id"]
        response = client.post(
            f"/api/chat/sessions/{chat_id}/chat", headers=bearer(token), json={"message": "测试中断"}
        )
        assert "event: error" in response.text
        with app.state.database.session_factory() as session:
            assistant = session.scalar(select(ConversationHistory).where(
                ConversationHistory.session_id == chat_id, ConversationHistory.role == "assistant"
            ))
            assert assistant.content == expected
        listed = client.get(f"/api/chat/sessions/{chat_id}/messages", headers=bearer(token)).json()
        assert listed[-1]["content"] == expected


def test_chat_auxiliary_services_are_bounded_and_sentiment_deadline_starts_with_task(client, app, settings):
    settings.followup_timeout_seconds = 0.3

    class SlowRunner:
        def stream(self, messages, context):
            time.sleep(2.8)
            yield {"event": "token", "data": {"content": "回答"}}

    class SlowService:
        def analyze(self, text):
            time.sleep(5)
            return {"label": "negative", "score": 1}

        def generate(self, *args):
            time.sleep(5)
            return ["不应返回"]

    app.state.agent_runner = SlowRunner()
    app.state.sentiment_service = SlowService()
    app.state.title_service = SlowService()
    app.state.followup_service = SlowService()
    token = login(client, "demo", settings.demo_initial_password)
    chat_id = client.post("/api/chat/sessions", headers=bearer(token), json={}).json()["id"]

    started = time.monotonic()
    response = client.post(f"/api/chat/sessions/{chat_id}/chat", headers=bearer(token), json={"message": "慢请求"})
    elapsed = time.monotonic() - started

    assert elapsed < 4.2
    assert '"label": "neutral"' in response.text
    assert '"questions": []' in response.text
    join_title_futures()


def test_chat_followup_waits_configurable_extended_timeout(client, app, settings):
    class SlowFollowup:
        def generate(self, user_text, answer):
            time.sleep(1.2)
            return ["慢速追问"]

    app.state.agent_runner = FakeAgentRunner()
    app.state.sentiment_service = FakeSentiment()
    app.state.followup_service = SlowFollowup()
    token = login(client, "demo", settings.demo_initial_password)
    chat_id = client.post("/api/chat/sessions", headers=bearer(token), json={}).json()["id"]

    started = time.monotonic()
    with client.stream(
        "POST", f"/api/chat/sessions/{chat_id}/chat", headers=bearer(token), json={"message": "追问测试"}
    ) as response:
        body = "\n".join(response.iter_lines())
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert "慢速追问" in body
    assert "event: followup" in body
    assert "event: done" in body
    assert elapsed < settings.followup_timeout_seconds + 2


def test_chat_stream_close_persists_partial_answer_or_placeholder_with_sentiment(client, app, settings):
    from types import SimpleNamespace

    from backend.app.api.chat import chat_event_stream
    from backend.app.db.models import ConversationHistory

    class ToolOnlyRunner:
        def stream(self, messages, context):
            yield {"event": "tool", "data": {"name": "search_devices", "status": "completed"}}

    app.state.sentiment_service = FakeSentiment()
    token = login(client, "demo", settings.demo_initial_password)
    for runner, expected in [(FakeAgentRunner(), "您好\n\n[回答中断]"), (ToolOnlyRunner(), "[回答生成失败] 客户端提前断开")]:
        app.state.agent_runner = runner
        app.state.deployment_agent_runner = runner
        chat_id = client.post("/api/chat/sessions", headers=bearer(token), json={}).json()["id"]
        with app.state.database.session_factory.begin() as session:
            user_message = ConversationHistory(session_id=chat_id, user_id="1001", role="user", content="中断测试")
            session.add(user_message)
            session.flush()
            message_id = user_message.id

        generator = chat_event_stream(
            app, chat_id, SimpleNamespace(user_id="1001"), "中断测试",
            [{"role": "user", "content": "中断测试"}],
            {"account_id": "acc-1", "user_id": "1001", "city": "上海", "session_id": chat_id}, message_id, False,
        )
        next(generator)
        generator.close()

        with app.state.database.session_factory() as session:
            assistant = session.scalar(select(ConversationHistory).where(
                ConversationHistory.session_id == chat_id, ConversationHistory.role == "assistant"
            ))
            assert assistant is not None
            assert assistant.content == expected
            assert session.get(ConversationHistory, message_id).sentiment == "negative"


def test_production_lifespan_does_not_attach_ungated_global_ticket_model(client, app):
    assert getattr(app.state, "ticket_summary_service", None) is None


def test_chat_stream_close_after_persist_does_not_duplicate_assistant_message(client, app, settings):
    from types import SimpleNamespace

    from backend.app.api.chat import chat_event_stream
    from backend.app.db.models import ConversationHistory

    app.state.agent_runner = FakeAgentRunner()
    app.state.deployment_agent_runner = app.state.agent_runner
    app.state.sentiment_service = FakeSentiment()
    app.state.followup_service = FakeFollowup()
    token = login(client, "demo", settings.demo_initial_password)
    chat_id = client.post("/api/chat/sessions", headers=bearer(token), json={}).json()["id"]
    with app.state.database.session_factory.begin() as session:
        user_message = ConversationHistory(session_id=chat_id, user_id="1001", role="user", content="断开测试")
        session.add(user_message)
        session.flush()
        message_id = user_message.id

    generator = chat_event_stream(
        app, chat_id, SimpleNamespace(user_id="1001"), "断开测试",
        [{"role": "user", "content": "断开测试"}],
        {"account_id": "acc-1", "user_id": "1001", "city": "上海", "session_id": chat_id}, message_id, False,
    )
    for event in generator:
        if event["event"] == "sentiment":
            break
    generator.close()

    with app.state.database.session_factory() as session:
        assistants = session.scalars(select(ConversationHistory).where(
            ConversationHistory.session_id == chat_id, ConversationHistory.role == "assistant"
        )).all()
        assert [item.content for item in assistants] == ["您好，已处理"]
        assert session.get(ConversationHistory, message_id).sentiment == "negative"


def test_missing_api_key_does_not_block_non_ai_api_and_chat_returns_error(client, settings):
    token = login(client, "demo", settings.demo_initial_password)
    chat_id = client.post("/api/chat/sessions", headers=bearer(token), json={}).json()["id"]

    assert client.get("/api/devices", headers=bearer(token)).status_code == 200
    with client.stream(
        "POST", f"/api/chat/sessions/{chat_id}/chat", headers=bearer(token), json={"message": "你好"}
    ) as response:
        body = "\n".join(response.iter_lines())
    assert response.status_code == 503
    assert '"code":"MODEL_NOT_CONFIGURED"' in body


def test_message_feedback_enforces_session_owner(client, app, settings):
    from backend.app.db.models import Account, ChatSession, ConversationHistory

    demo = login(client, "demo", settings.demo_initial_password)
    other = register(client, "other-feedback-user")
    with app.state.database.session_factory.begin() as session:
        account = session.scalar(select(Account).where(Account.username == "demo"))
        from backend.app.db.models import HouseholdMember
        household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        chat = ChatSession(account_id=account.id, household_id=household_id)
        session.add(chat)
        session.flush()
        message = ConversationHistory(session_id=chat.id, user_id=account.user_id, role="assistant", content="answer")
        user_message = ConversationHistory(session_id=chat.id, user_id=account.user_id, role="user", content="question")
        session.add_all([message, user_message])
        session.flush()
        message_id = message.id
        user_message_id = user_message.id

    assert client.post(
        f"/api/chat/messages/{message_id}/feedback", headers=bearer(other), json={"feedback": "dislike"}
    ).status_code == 404
    assert client.post(
        f"/api/chat/messages/{message_id}/feedback", headers=bearer(demo), json={"feedback": "bad"}
    ).status_code == 422

    assert client.post(
        f"/api/chat/messages/{user_message_id}/feedback", headers=bearer(demo), json={"feedback": "like"}
    ).status_code == 400


def test_devices_compare_and_restock_notification_are_structured_and_deduplicated(client, settings):
    token = login(client, "demo", settings.demo_initial_password)
    compared = client.post(
        "/api/devices/compare", headers=bearer(token), json={"model_ids": ["R1-Lite", "R1-Pro"]}
    )
    assert compared.status_code == 200
    assert [item["model_id"] for item in compared.json()] == ["R1-Lite", "R1-Pro"]
    assert compared.json()[0]["total_stock"] == 60

    first = client.post("/api/devices/restock-notify/R1-Pro", headers=bearer(token))
    second = client.post("/api/devices/restock-notify/R1-Pro", headers=bearer(token))
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


class FailingSummary:
    def summarize(self, text):
        raise RuntimeError("offline")


def test_ticket_creation_validates_session_owner_and_summary_falls_back(client, app, settings):
    demo = login(client, "demo", settings.demo_initial_password)
    other = register(client, "other-ticket-user")
    chat_id = client.post("/api/chat/sessions", headers=bearer(demo), json={}).json()["id"]
    app.state.ticket_summary_service = FailingSummary()

    forbidden = client.post(
        "/api/tickets", headers=bearer(other), json={"session_id": chat_id, "title": "问题", "content": "x" * 300}
    )
    assert forbidden.status_code == 404
    created = client.post(
        "/api/tickets", headers=bearer(demo), json={"session_id": chat_id, "title": "问题", "content": "x" * 300}
    )
    assert created.status_code == 201
    assert created.json()["summary"] == "x" * 200
    assert client.get(f"/api/tickets/my/{created.json()['id']}", headers=bearer(other)).status_code == 404
    assert client.get("/api/tickets/my", headers=bearer(demo)).json()[0]["id"] == created.json()["id"]


def test_deleting_session_nulls_ticket_reference(client, app, settings):
    from backend.app.db.models import Ticket

    token = login(client, "demo", settings.demo_initial_password)
    chat_id = client.post("/api/chat/sessions", headers=bearer(token), json={}).json()["id"]
    ticket_id = client.post(
        "/api/tickets", headers=bearer(token), json={"session_id": chat_id, "title": "保留工单", "content": "详情"}
    ).json()["id"]

    assert client.delete(f"/api/chat/sessions/{chat_id}", headers=bearer(token)).status_code == 204
    with app.state.database.session_factory() as session:
        assert session.get(Ticket, ticket_id).session_id is None
