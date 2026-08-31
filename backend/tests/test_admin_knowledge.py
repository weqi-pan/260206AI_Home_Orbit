from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import select


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def admin_token(client, settings):
    return client.post(
        "/api/auth/login", json={"username": "admin", "password": settings.admin_initial_password}
    ).json()["access_token"]


class FakeVectorStore:
    def __init__(self):
        self.calls = []

    def add_file(self, file_id, path, filename, household_id=None, skill_version_id=None):
        assert household_id and skill_version_id
        self.calls.append(("add_file", file_id, filename))

    def delete_file(self, file_id):
        self.calls.append(("delete_file", file_id))

    def rebuild(self, files=(), faqs=(), household_id=None, skill_version_id=None):
        assert household_id and skill_version_id
        self.calls.append(("rebuild", tuple(files), tuple(faqs)))

    def add_faq(self, faq_id, question, answer, source_file_id, household_id=None, skill_version_id=None):
        assert household_id and skill_version_id
        self.calls.append(("add_faq", faq_id, source_file_id))

    def delete_faq(self, faq_id):
        self.calls.append(("delete_faq", faq_id))

    def search(self, query):
        return []


class FakeFaqGenerator:
    def generate(self, path):
        return [{"question": "如何维护？", "answer": "定期清洁。"}]


@pytest.fixture(autouse=True)
def configured_embedding(client, app, monkeypatch):
    from backend.app.db.models import Account, HouseholdMember
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    with app.state.database.session_factory() as session:
        admin = session.scalar(select(Account).where(Account.username == "admin"))
        household_id = session.scalar(select(HouseholdMember.household_id).where(
            HouseholdMember.account_id == admin.id))
        configured_runtime(session, household_id, purpose="embedding")


def test_knowledge_upload_validates_type_size_and_md5(client, app, settings):
    token = admin_token(client, settings)
    app.state.vector_store_service = FakeVectorStore()

    bad = client.post(
        "/api/knowledge/files", headers=bearer(token), files={"file": ("bad.md", b"text", "text/markdown")}
    )
    assert bad.status_code == 422
    too_large = client.post(
        "/api/knowledge/files", headers=bearer(token), files={"file": ("large.txt", b"x" * (20 * 1024 * 1024 + 1), "text/plain")}
    )
    assert too_large.status_code == 413
    first = client.post(
        "/api/knowledge/files", headers=bearer(token), files={"file": ("guide.txt", "知识".encode(), "text/plain")}
    )
    duplicate = client.post(
        "/api/knowledge/files", headers=bearer(token), files={"file": ("copy.txt", "知识".encode(), "text/plain")}
    )
    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert client.get("/api/knowledge/files", headers=bearer(token)).json()[0]["filename"] == "guide.txt"


def test_knowledge_upload_without_embedding_returns_stable_code_before_vector_backend(client, app, settings):
    from backend.app.db.models import KnowledgeFile, ModelPurposeBinding

    class UnexpectedVectorBackend(FakeVectorStore):
        def add_file(self, *args, **kwargs):
            raise AssertionError("embedding 未配置时不得访问 vector backend")

    with app.state.database.session_factory.begin() as session:
        session.delete(session.get(ModelPurposeBinding, "embedding"))
    app.state.vector_store_service = UnexpectedVectorBackend()
    token = admin_token(client, settings)

    response = client.post(
        "/api/knowledge/files", headers=bearer(token), files={"file": ("disabled.txt", b"content", "text/plain")}
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "EMBEDDING_NOT_CONFIGURED"
    assert not list(Path(settings.knowledge_upload_path).glob("*"))
    with app.state.database.session_factory() as session:
        assert session.scalar(select(KnowledgeFile).where(KnowledgeFile.filename == "disabled.txt")) is None


def test_soft_deleted_same_md5_upload_restores_record_and_reindexes(client, app, settings):
    token = admin_token(client, settings)
    vector = FakeVectorStore()
    app.state.vector_store_service = vector
    first = client.post(
        "/api/knowledge/files", headers=bearer(token), files={"file": ("old.txt", b"same", "text/plain")}
    ).json()
    assert client.delete(f"/api/knowledge/files/{first['id']}", headers=bearer(token)).status_code == 204

    restored = client.post(
        "/api/knowledge/files", headers=bearer(token), files={"file": ("new.txt", b"same", "text/plain")}
    )

    assert restored.status_code == 201
    assert restored.json()["id"] == first["id"]
    assert restored.json()["status"] == "active"
    assert restored.json()["filename"] == "new.txt"
    assert vector.calls[-1] == ("add_file", first["id"], "new.txt")


def test_upload_vector_failure_removes_file_and_rolls_back_database(client, app, settings):
    from backend.app.db.models import KnowledgeFile
    from backend.rag.vector_store import VectorStoreUnavailable

    class FailingVector(FakeVectorStore):
        def add_file(self, file_id, path, filename):
            raise VectorStoreUnavailable("embedding failed")

    token = admin_token(client, settings)
    app.state.vector_store_service = FailingVector()
    response = client.post(
        "/api/knowledge/files", headers=bearer(token), files={"file": ("broken.txt", b"broken", "text/plain")}
    )

    assert response.status_code == 503
    assert not list(Path(settings.knowledge_upload_path).glob("*"))
    with app.state.database.session_factory() as session:
        assert session.scalar(select(KnowledgeFile).where(KnowledgeFile.filename == "broken.txt")) is None


def test_upload_unexpected_vector_failure_also_compensates(client, app, settings):
    from backend.app.db.models import KnowledgeFile

    class UnexpectedFailure(FakeVectorStore):
        def add_file(self, file_id, path, filename):
            raise ValueError("unexpected chroma failure")

    token = admin_token(client, settings)
    app.state.vector_store_service = UnexpectedFailure()
    response = client.post(
        "/api/knowledge/files", headers=bearer(token), files={"file": ("unexpected.txt", b"broken", "text/plain")}
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "VECTOR_STORE_WRITE_FAILED"
    assert not list(Path(settings.knowledge_upload_path).glob("*"))
    with app.state.database.session_factory() as session:
        assert session.scalar(select(KnowledgeFile).where(KnowledgeFile.filename == "unexpected.txt")) is None


def test_faq_generation_and_review_updates_vector_store(client, app, settings):
    token = admin_token(client, settings)
    vector = FakeVectorStore()
    app.state.vector_store_service = vector
    app.state.faq_generator = FakeFaqGenerator()
    uploaded = client.post(
        "/api/knowledge/files", headers=bearer(token), files={"file": ("faq.txt", b"faq content", "text/plain")}
    ).json()

    generated = client.post(f"/api/knowledge/files/{uploaded['id']}/generate-faq", headers=bearer(token))
    faq_id = generated.json()[0]["id"]
    approved = client.put(
        f"/api/knowledge/faq/{faq_id}/review", headers=bearer(token), json={"status": "approved"}
    )
    rejected = client.put(
        f"/api/knowledge/faq/{faq_id}/review", headers=bearer(token), json={"status": "rejected"}
    )
    assert approved.status_code == 200
    assert rejected.status_code == 200
    assert ("add_faq", faq_id, uploaded["id"]) in vector.calls
    assert ("delete_faq", faq_id) in vector.calls


def test_faq_generation_falls_back_to_bootstrap_and_approved_review_is_idempotent(client, app, settings):
    from backend.app.db.models import KnowledgeFile

    token = admin_token(client, settings)
    vector = FakeVectorStore()
    app.state.vector_store_service = vector
    app.state.faq_generator = FakeFaqGenerator()
    bootstrap = Path(settings.knowledge_path) / "bootstrap-faq.txt"
    bootstrap.write_text("bootstrap faq", encoding="utf-8")
    try:
        with app.state.database.session_factory.begin() as session:
            from backend.app.db.models import Account, HouseholdMember
            admin = session.scalar(select(Account).where(Account.username == "admin"))
            household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == admin.id))
            item = KnowledgeFile(household_id=household_id, filename=bootstrap.name, file_md5="f" * 32, file_type=".txt")
            session.add(item)
            session.flush()
            file_id = item.id
        faq = client.post(f"/api/knowledge/files/{file_id}/generate-faq", headers=bearer(token)).json()[0]
        assert client.put(f"/api/knowledge/faq/{faq['id']}/review", headers=bearer(token), json={"status": "approved"}).status_code == 200
        assert client.put(f"/api/knowledge/faq/{faq['id']}/review", headers=bearer(token), json={"status": "approved"}).status_code == 200
        assert [call for call in vector.calls if call[0] == "add_faq"] == [("add_faq", faq["id"], file_id)]
    finally:
        bootstrap.unlink(missing_ok=True)


def test_generate_faq_parse_failure_returns_4xx_with_clear_message(client, app, settings):
    from backend.app.services.faq_gen import FaqGenerationError

    class BrokenFaqGenerator:
        def generate(self, path):
            raise FaqGenerationError("模型未返回有效的FAQ JSON数组")

    token = admin_token(client, settings)
    app.state.vector_store_service = FakeVectorStore()
    app.state.faq_generator = BrokenFaqGenerator()
    uploaded = client.post(
        "/api/knowledge/files", headers=bearer(token), files={"file": ("broken-faq.txt", b"content", "text/plain")}
    ).json()

    response = client.post(f"/api/knowledge/files/{uploaded['id']}/generate-faq", headers=bearer(token))

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "FAQ_RESULT_INVALID"


def test_generate_faq_provider_or_missing_key_failure_returns_503_not_500(client, app, settings):
    class NoKeyFaqGenerator:
        def generate(self, path):
            raise RuntimeError("MODEL_NOT_CONFIGURED")

    token = admin_token(client, settings)
    app.state.vector_store_service = FakeVectorStore()
    app.state.faq_generator = NoKeyFaqGenerator()
    uploaded = client.post(
        "/api/knowledge/files", headers=bearer(token), files={"file": ("nokey-faq.txt", b"content", "text/plain")}
    ).json()

    response = client.post(f"/api/knowledge/files/{uploaded['id']}/generate-faq", headers=bearer(token))

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "FAQ_MODEL_UNAVAILABLE"


def test_rebuild_passes_active_files_and_approved_faqs(client, app, settings):
    token = admin_token(client, settings)
    vector = FakeVectorStore()
    app.state.vector_store_service = vector
    app.state.faq_generator = FakeFaqGenerator()
    uploaded = client.post(
        "/api/knowledge/files", headers=bearer(token), files={"file": ("rebuild.txt", b"content", "text/plain")}
    ).json()
    faq = client.post(f"/api/knowledge/files/{uploaded['id']}/generate-faq", headers=bearer(token)).json()[0]
    client.put(f"/api/knowledge/faq/{faq['id']}/review", headers=bearer(token), json={"status": "approved"})

    response = client.post("/api/knowledge/rebuild", headers=bearer(token))

    assert response.status_code == 200
    rebuild_call = next(call for call in vector.calls if call[0] == "rebuild")
    assert uploaded["id"] in {item[0] for item in rebuild_call[1]}
    assert faq["id"] in {item[0] for item in rebuild_call[2]}


def test_rebuild_rejects_bootstrap_filename_outside_root_and_reports_vector_failure(client, app, settings):
    from backend.app.db.models import KnowledgeFile
    from backend.rag.vector_store import VectorStoreUnavailable

    class FailingRebuild(FakeVectorStore):
        def rebuild(self, files=(), faqs=(), household_id=None, skill_version_id=None):
            assert household_id and skill_version_id
            assert all(path.resolve().is_relative_to(Path(settings.knowledge_path).resolve()) for _, path, _ in files)
            assert all(filename != "../outside.txt" for _, _, filename in files)
            raise VectorStoreUnavailable("rebuild incomplete; source data preserved")

    token = admin_token(client, settings)
    outside = Path(settings.knowledge_path).parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        with app.state.database.session_factory.begin() as session:
            from backend.app.db.models import Account, HouseholdMember
            admin = session.scalar(select(Account).where(Account.username == "admin"))
            household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == admin.id))
            session.add(KnowledgeFile(household_id=household_id, filename="../outside.txt", file_md5="e" * 32, file_type=".txt"))
        app.state.vector_store_service = FailingRebuild()
        response = client.post("/api/knowledge/rebuild", headers=bearer(token))
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "VECTOR_STORE_UNAVAILABLE"
    finally:
        outside.unlink(missing_ok=True)


def test_admin_inventory_listing_requires_admin_and_filters_by_model(client, app, settings):
    from backend.app.db.models import Account, HouseholdMember
    token = admin_token(client, settings)
    user_token = client.post(
        "/api/auth/login", json={"username": "demo", "password": settings.demo_initial_password}
    ).json()["access_token"]

    assert client.get("/api/admin/inventory", headers=bearer(user_token)).status_code == 403

    with app.state.database.session_factory() as db:
        admin = db.scalar(select(Account).where(Account.username == "admin"))
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == admin.id))
    rows = client.get("/api/admin/inventory", headers=bearer(token), params={"household_id": household_id}).json()
    assert {(row["model_id"], row["warehouse"]) for row in rows} == {
        ("R1-Lite", "北京仓"), ("R1-Lite", "上海仓"),
        ("R1-Pro", "北京仓"), ("R1-Pro", "上海仓"), ("R1-Pro", "广州仓"),
        ("RT2-Max", "上海仓"), ("RT2-Max", "广州仓"),
    }
    assert all(
        set(row) == {"household_id", "model_id", "warehouse", "stock", "min_stock", "updated_at"}
        and row["updated_at"] and row["household_id"] == household_id
        for row in rows
    )

    filtered = client.get("/api/admin/inventory", headers=bearer(token),
                          params={"household_id": household_id, "model_id": "R1-Pro"}).json()
    assert {row["model_id"] for row in filtered} == {"R1-Pro"}
    assert {row["warehouse"] for row in filtered} == {"北京仓", "上海仓", "广州仓"}
    assert sorted(row["stock"] for row in filtered) == [12, 18, 32]


def test_admin_account_device_inventory_notification_ticket_crud_and_dashboard(client, app, settings):
    from backend.app.db.models import Account, HouseholdMember
    token = admin_token(client, settings)
    headers = bearer(token)
    with app.state.database.session_factory() as db:
        admin_account = db.scalar(select(Account).where(Account.username == "admin"))
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == admin_account.id))
    accounts = client.get("/api/admin/accounts", headers=headers).json()
    demo = next(item for item in accounts if item["username"] == "demo")
    assert client.put(f"/api/admin/accounts/{demo['id']}", headers=headers, json={"status": "disabled"}).json()["status"] == "disabled"
    assert client.put(f"/api/admin/accounts/{demo['id']}", headers=headers, json={"status": "active", "role": "user"}).status_code == 200
    assert client.post(f"/api/admin/accounts/{demo['id']}/reset-password", headers=headers, json={"password": "Reset@123"}).status_code == 204

    created = client.post("/api/admin/devices", headers=headers, params={"household_id": household_id}, json={
        "model_id": "NEW-1", "model_name": "新设备", "category": "扫地机器人", "specs": "s", "description": "d", "price": 999, "features": "f"
    })
    assert created.status_code == 201
    assert client.put("/api/admin/inventory", headers=headers, params={"household_id": household_id},
                      json={"model_id": "NEW-1", "warehouse": "上海仓", "stock": 0, "min_stock": 3}).status_code == 200

    user_token = client.post("/api/auth/login", json={"username": "demo", "password": "Reset@123"}).json()["access_token"]
    notification = client.post("/api/devices/restock-notify/NEW-1", headers=bearer(user_token))
    assert notification.status_code == 404
    existing = client.post("/api/devices/restock-notify/R1-Pro", headers=bearer(user_token)).json()
    assert existing["status"] == "waiting"
    ticket = client.post("/api/tickets", headers=bearer(user_token), json={"title": "故障", "content": "无法启动"}).json()
    assert client.put(f"/api/admin/tickets/{ticket['id']}", headers=headers,
                      json={"status": "resolved", "admin_reply": "已处理"}).status_code == 404

    dashboard = client.get("/api/admin/dashboard", headers=headers).json()
    assert dashboard["accounts"] >= 2
    assert dashboard["knowledge_files"] >= 0
    assert any(item["model_id"] == "NEW-1" for item in dashboard["low_stock_devices"])
    assert client.delete("/api/admin/devices/NEW-1", headers=headers,
                         params={"household_id": household_id}).status_code == 204


def test_admin_cannot_disable_or_demote_self_and_must_leave_active_admin(client, app, settings):
    from backend.app.core.security import hash_password
    from backend.app.db.models import Account

    token = admin_token(client, settings)
    headers = bearer(token)
    accounts = client.get("/api/admin/accounts", headers=headers).json()
    current = next(item for item in accounts if item["username"] == "admin")
    assert client.put(f"/api/admin/accounts/{current['id']}", headers=headers, json={"status": "disabled"}).status_code == 400
    assert client.put(f"/api/admin/accounts/{current['id']}", headers=headers, json={"role": "user"}).status_code == 400

    with app.state.database.session_factory.begin() as session:
        second = Account(username="admin2", password_hash=hash_password("Admin2@123", settings.bcrypt_rounds), role="admin")
        session.add(second)
        session.flush()
        second_id = second.id
    assert client.put(f"/api/admin/accounts/{second_id}", headers=headers, json={"status": "disabled"}).status_code == 200
    assert client.put(f"/api/admin/accounts/{current['id']}", headers=headers, json={"status": "disabled"}).status_code == 400


def test_dashboard_counts_only_assistant_feedback_and_uses_total_stock_threshold(client, app, settings):
    from backend.app.db.models import Account, ChatSession, ConversationHistory, Device, Inventory

    token = admin_token(client, settings)
    with app.state.database.session_factory.begin() as session:
        account = session.scalar(select(Account).where(Account.username == "admin"))
        from backend.app.db.models import HouseholdMember
        household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        chat = ChatSession(account_id=account.id, household_id=household_id)
        session.add(chat)
        session.flush()
        session.add_all([
            ConversationHistory(session_id=chat.id, user_id=account.user_id, role="user", content="q", feedback="like"),
            ConversationHistory(session_id=chat.id, user_id=account.user_id, role="assistant", content="a", feedback="dislike"),
        ])
        device = Device(household_id=household_id, model_id="THRESHOLD", model_name="阈值设备", category="x", specs="x", description="x", price=1, features="x")
        session.add(device)
        session.flush()
        session.add_all([
            Inventory(household_id=household_id, device_id=device.id, model_id="THRESHOLD", warehouse="A", stock=2, min_stock=3),
            Inventory(household_id=household_id, device_id=device.id, model_id="THRESHOLD", warehouse="B", stock=2, min_stock=5),
        ])

    dashboard = client.get("/api/admin/dashboard", headers=bearer(token)).json()
    assert dashboard["satisfaction_rate"] == 0
    low = next(item for item in dashboard["low_stock_devices"] if item["model_id"] == "THRESHOLD")
    assert low == {"household_id": household_id, "model_id": "THRESHOLD", "model_name": "阈值设备",
                   "total_stock": 4, "stock_threshold": 5}
