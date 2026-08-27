import json

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
        session.add(message)
        session.flush()
        message_id = message.id

    assert client.post(
        f"/api/chat/messages/{message_id}/feedback", headers=bearer(other), json={"feedback": "dislike"}
    ).status_code == 404
    assert client.post(
        f"/api/chat/messages/{message_id}/feedback", headers=bearer(demo), json={"feedback": "bad"}
    ).status_code == 422


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
