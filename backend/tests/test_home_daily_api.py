"""Phase 5 /api/home/* household daily-loop API tests."""
from datetime import timedelta

from sqlalchemy import select


def _login(client, username, password):
    token = client.post("/api/auth/login", json={"username": username, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _demo_headers(client):
    return _login(client, "demo", "DemoTest@123")


def test_home_today_reports_real_household_state(client, app):
    from backend.app.db.base import utcnow
    from backend.app.db.models import Account, HouseholdMember
    from backend.app.services.household_daily import HouseholdTaskService, NotificationService, ShoppingListService

    headers = _demo_headers(client)
    with app.state.database.session_factory.begin() as db:
        account = db.scalar(select(Account).where(Account.username == "demo"))
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        ShoppingListService(db).upsert(household_id, name="鸡蛋", quantity=6, unit="个", account_id=account.id)
        ShoppingListService(db).upsert(household_id, name="牛奶", quantity=1, unit="盒", account_id=account.id)
        HouseholdTaskService(db).create(household_id, title="浇花", due_at=utcnow() + timedelta(hours=1),
                                        account_id=account.id)
        NotificationService(db).create(household_id, title="测试通知", kind="general")

    response = client.get("/api/home/today", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["shopping"] == {"total": 2, "unchecked": 2, "checked": 0}
    assert [task["title"] for task in payload["tasks"]] == ["浇花"]
    assert payload["notifications"]["unread"] == 1
    assert "anomalies" in payload


def test_home_today_is_household_scoped(client, app):
    from backend.app.db.models import Account, HouseholdMember
    from backend.app.services.household_daily import HouseholdTaskService

    admin_headers = _login(client, "admin", "AdminTest@123")
    with app.state.database.session_factory.begin() as db:
        account = db.scalar(select(Account).where(Account.username == "demo"))
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        HouseholdTaskService(db).create(household_id, title="仅本家庭可见")
    payload = client.get("/api/home/today", headers=admin_headers).json()
    assert payload["tasks"] == []
    assert payload["notifications"]["unread"] == 0


def test_shopping_list_endpoints_create_check_and_remove(client):
    headers = _demo_headers(client)
    created = client.post("/api/home/shopping-list", headers=headers,
                          json={"name": "鸡蛋", "quantity": 6, "unit": "个", "category": "食材"}).json()
    assert created["checked"] is False and created["name"] == "鸡蛋"
    listed = client.get("/api/home/shopping-list", headers=headers).json()
    assert [item["name"] for item in listed["items"]] == ["鸡蛋"]
    checked = client.patch(f"/api/home/shopping-list/{created['id']}", headers=headers,
                           json={"checked": True}).json()
    assert checked["checked"] is True
    summary = client.get("/api/home/today", headers=headers).json()["shopping"]
    assert summary["checked"] == 1
    assert client.delete(f"/api/home/shopping-list/{created['id']}", headers=headers).status_code == 204
    assert client.get("/api/home/shopping-list", headers=headers).json()["items"] == []


def test_task_and_reminder_endpoints(client):
    headers = _demo_headers(client)
    task = client.post("/api/home/tasks", headers=headers, json={"title": "每日垃圾分类"}).json()
    reminder = client.post("/api/home/reminders", headers=headers,
                           json={"task_id": task["id"], "interval_minutes": 60}).json()
    assert reminder["active"] is True and reminder["interval_minutes"] == 60
    reminders = client.get("/api/home/reminders", headers=headers).json()["items"]
    assert [item["id"] for item in reminders] == [reminder["id"]]
    completed = client.post(f"/api/home/tasks/{task['id']}/complete", headers=headers).json()
    assert completed["status"] == "done"
    reminders = client.get("/api/home/reminders", headers=headers).json()["items"]
    assert reminders[0]["active"] is False  # completion stops future runs
    cancelled = client.delete(f"/api/home/reminders/{reminder['id']}", headers=headers).json()
    assert cancelled["active"] is False


def test_notification_center_lists_and_marks_read_per_account(client, app):
    from backend.app.db.models import Account, HouseholdMember
    from backend.app.services.household_daily import NotificationService

    with app.state.database.session_factory.begin() as db:
        account = db.scalar(select(Account).where(Account.username == "demo"))
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        notification = NotificationService(db).create(household_id, title="待读通知", kind="reminder")
    headers = _demo_headers(client)
    items = client.get("/api/home/notifications", headers=headers).json()["items"]
    assert items[0]["title"] == "待读通知" and items[0]["read_at"] is None
    unread = client.get("/api/home/notifications?unread_only=true", headers=headers).json()["items"]
    assert len(unread) == 1
    assert client.post(f"/api/home/notifications/{notification.id}/read", headers=headers).status_code == 200
    assert client.get("/api/home/notifications?unread_only=true", headers=headers).json()["items"] == []


def test_device_anomaly_hook_creates_notification(client, app):
    from backend.app.db.models import Account, HouseholdMember, Notification

    headers = _demo_headers(client)
    response = client.post("/api/home/device-anomalies", headers=headers,
                           json={"model_id": "R1-Pro", "title": "R1-Pro 库存不足", "detail": "广州仓仅剩 12 台"})
    assert response.status_code == 201
    with app.state.database.session_factory() as db:
        account = db.scalar(select(Account).where(Account.username == "demo"))
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        row = db.scalars(select(Notification).where(Notification.household_id == household_id,
                                                    Notification.kind == "device_anomaly")).all()
        assert len(row) == 1 and row[0].source_type == "device"


def test_preference_endpoints_require_member_confirmation(client, app):
    headers = _demo_headers(client)
    proposed = client.post("/api/home/preferences/proposals", headers=headers,
                           json={"key": "diet.spice_level", "value": "微辣", "value_type": "string",
                                 "category": "饮食", "reason": "建议"}).json()
    assert proposed["status"] == "pending"
    assert client.get("/api/home/preferences", headers=headers).json()["items"] == []
    confirmed = client.post(f"/api/home/preferences/proposals/{proposed['id']}/confirm", headers=headers).json()
    assert confirmed["value"] == "微辣"
    items = client.get("/api/home/preferences", headers=headers).json()["items"]
    assert [item["key"] for item in items] == ["diet.spice_level"]


def test_memo_endpoints(client):
    headers = _demo_headers(client)
    memo = client.post("/api/home/memos", headers=headers, json={"content": "周三物业检修水管"}).json()
    updated = client.patch(f"/api/home/memos/{memo['id']}", headers=headers,
                           json={"content": "周三 14:00 物业检修水管"}).json()
    assert updated["content"] == "周三 14:00 物业检修水管"
    assert client.delete(f"/api/home/memos/{memo['id']}", headers=headers).status_code == 204


def test_orbit_nodes_reflect_household_daily_state(client, app):
    from backend.app.db.models import Account, HouseholdMember
    from backend.app.services.household_daily import HouseholdTaskService, ShoppingListService

    headers = _demo_headers(client)
    with app.state.database.session_factory.begin() as db:
        account = db.scalar(select(Account).where(Account.username == "demo"))
        household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
        ShoppingListService(db).upsert(household_id, name="鸡蛋", quantity=6, unit="个", account_id=account.id)
        HouseholdTaskService(db).create(household_id, title="浇花", account_id=account.id)
    nodes = client.get("/api/home/orbit", headers=headers).json()["nodes"]
    kinds = {node["kind"] for node in nodes}
    assert {"household", "tasks", "shopping", "notifications"} <= kinds
    tasks_node = next(node for node in nodes if node["kind"] == "tasks")
    assert "浇花" in tasks_node["detail"]
    shopping_node = next(node for node in nodes if node["kind"] == "shopping")
    assert "鸡蛋" in shopping_node["detail"]
