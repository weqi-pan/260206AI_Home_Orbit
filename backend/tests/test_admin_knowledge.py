from io import BytesIO

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

    def add_file(self, file_id, path, filename):
        self.calls.append(("add_file", file_id, filename))

    def delete_file(self, file_id):
        self.calls.append(("delete_file", file_id))

    def rebuild(self, files=(), faqs=()):
        self.calls.append(("rebuild", tuple(files), tuple(faqs)))

    def add_faq(self, faq_id, question, answer, source_file_id):
        self.calls.append(("add_faq", faq_id, source_file_id))

    def delete_faq(self, faq_id):
        self.calls.append(("delete_faq", faq_id))


class FakeFaqGenerator:
    def generate(self, path):
        return [{"question": "如何维护？", "answer": "定期清洁。"}]


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


def test_admin_account_device_inventory_notification_ticket_crud_and_dashboard(client, app, settings):
    token = admin_token(client, settings)
    headers = bearer(token)
    accounts = client.get("/api/admin/accounts", headers=headers).json()
    demo = next(item for item in accounts if item["username"] == "demo")
    assert client.put(f"/api/admin/accounts/{demo['id']}", headers=headers, json={"status": "disabled"}).json()["status"] == "disabled"
    assert client.put(f"/api/admin/accounts/{demo['id']}", headers=headers, json={"status": "active", "role": "user"}).status_code == 200
    assert client.post(f"/api/admin/accounts/{demo['id']}/reset-password", headers=headers, json={"password": "Reset@123"}).status_code == 204

    created = client.post("/api/admin/devices", headers=headers, json={
        "model_id": "NEW-1", "model_name": "新设备", "category": "扫地机器人", "specs": "s", "description": "d", "price": 999, "features": "f"
    })
    assert created.status_code == 201
    assert client.put("/api/admin/inventory", headers=headers, json={"model_id": "NEW-1", "warehouse": "上海仓", "stock": 0, "min_stock": 3}).status_code == 200

    user_token = client.post("/api/auth/login", json={"username": "demo", "password": "Reset@123"}).json()["access_token"]
    notification = client.post("/api/devices/restock-notify/NEW-1", headers=bearer(user_token)).json()
    assert client.put(f"/api/admin/restock-notifications/{notification['id']}", headers=headers, json={"status": "notified"}).json()["status"] == "notified"
    ticket = client.post("/api/tickets", headers=bearer(user_token), json={"title": "故障", "content": "无法启动"}).json()
    assert client.put(f"/api/admin/tickets/{ticket['id']}", headers=headers, json={"status": "resolved", "admin_reply": "已处理"}).json()["status"] == "resolved"

    dashboard = client.get("/api/admin/dashboard", headers=headers).json()
    assert dashboard["accounts"] >= 2
    assert dashboard["knowledge_files"] >= 0
    assert any(item["model_id"] == "NEW-1" for item in dashboard["low_stock_devices"])
    assert client.delete("/api/admin/devices/NEW-1", headers=headers).status_code == 204
