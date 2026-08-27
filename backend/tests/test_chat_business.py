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
        assert context["account_id"]
        assert context["user_id"] == "1001"
        assert context["city"] == "上海"
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
        assert session.get(ChatSession, chat_id).title == "设备咨询"

    feedback = client.post(
        f"/api/chat/messages/{assistant_id}/feedback", headers=bearer(token), json={"feedback": "like"}
    )
    assert feedback.status_code == 200
    assert feedback.json()["feedback"] == "like"


def test_chat_runtime_injects_lazy_vector_store_and_rag_has_clear_key_fallback(client, app, settings):
    from backend.agent.tools.agent_tools import rag_summarize

    class ContextRunner:
        def stream(self, messages, context):
            assert context["vector_store_service"] is app.state.vector_store_service
            fallback = rag_summarize.func(query="售后", runtime=type("Runtime", (), {"context": context})())
            assert "DASHSCOPE_API_KEY" in fallback
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
    for with_token, expected in [(True, "已生成部分回答\n\n[回答中断]"), (False, "[回答生成失败] agent disconnected")]:
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


def test_missing_api_key_does_not_block_non_ai_api_and_chat_returns_error(client, settings):
    token = login(client, "demo", settings.demo_initial_password)
    chat_id = client.post("/api/chat/sessions", headers=bearer(token), json={}).json()["id"]

    assert client.get("/api/devices", headers=bearer(token)).status_code == 200
    with client.stream(
        "POST", f"/api/chat/sessions/{chat_id}/chat", headers=bearer(token), json={"message": "你好"}
    ) as response:
        body = "\n".join(response.iter_lines())
    assert "event: error" in body
    assert "DASHSCOPE_API_KEY" in body


def test_message_feedback_enforces_session_owner(client, app, settings):
    from backend.app.db.models import Account, ChatSession, ConversationHistory

    demo = login(client, "demo", settings.demo_initial_password)
    other = register(client, "other-feedback-user")
    with app.state.database.session_factory.begin() as session:
        account = session.scalar(select(Account).where(Account.username == "demo"))
        chat = ChatSession(account_id=account.id)
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
