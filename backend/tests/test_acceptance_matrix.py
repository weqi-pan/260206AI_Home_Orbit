# -*- coding: utf-8 -*-
"""Phase 9 consolidated cross-household acceptance matrix.

One module proving cross-household 403/404 with no existence leakage across
ALL resource families plus the household role matrix. Cells already covered by
per-phase suites are marked ``[ref]`` and only re-asserted where the
consolidated sweep adds a distinct guarantee (e.g. error-body hygiene);
missing cells are added here.

Referenced suites:
- households/members:   test_platform_foundation.py::test_household_role_rules_and_cross_household_isolation
- device/inventory/usage: test_official_device_agent.py::test_device_repository_isolates_every_resource_by_household
                          test_official_device_fix_round1.py (inventory totals, admin ticket 404)
- knowledge vectors:    test_official_device_fix_round1.py::test_scoped_vector_rebuild_preserves_other_household...
                        test_household_agent_chains.py::test_encyclopedia_answers_from_household_isolated_vectors_only
- creator drafts:       test_creator_api.py::test_draft_crud_and_cross_household_isolation
- review submissions:   test_review_governance.py::test_cross_household_isolation_of_drafts_reviews_and_queue
- market installs:      test_market_install_upgrade.py / test_market_review_e2e.py
- audit/ops 403:        test_audit_query_api.py / test_ops_overview_api.py
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register(client, username: str) -> dict:
    response = client.post("/api/auth/register", json={"username": username, "password": "Password@123",
                                                       "city": "杭州"})
    assert response.status_code == 201, response.text
    return response.json()


def login(client, username: str, password: str = "Password@123") -> dict[str, str]:
    token = client.post("/api/auth/login", json={"username": username, "password": password}).json()["access_token"]
    return bearer(token)


def household_id_of(app, account_id: str) -> str:
    from backend.app.db.models import HouseholdMember

    with app.state.database.session_factory() as db:
        return db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account_id))


def _no_leak(response, *secrets: str) -> None:
    """Error bodies must stay code-only: they never echo foreign identifiers."""
    if response.headers.get("content-type", "").startswith("application/json"):
        text = response.text
        for secret in secrets:
            assert secret not in text, f"existence leak: {secret!r} in {text!r}"


# --------------------------------------------------------------------- F1 households
def test_matrix_household_family_isolation_and_invitation_leakage(client, app):
    """Households/members/invitations: outsider reads/invites 404 [ref], and a
    non-invitee cannot accept someone else's invitation (404, no leak) [new]."""
    owner = register(client, "mx-a-owner")
    home_a = client.get("/api/households/current", headers=bearer(owner["access_token"])).json()
    outsider = register(client, "mx-b-owner")
    outsider_headers = bearer(outsider["access_token"])
    home_b_name = client.get("/api/households/current", headers=outsider_headers).json()["name"]

    got = client.get(f"/api/households/{home_a['id']}", headers=outsider_headers)
    assert got.status_code == 404
    invite = client.post(f"/api/households/{home_a['id']}/invitations", headers=outsider_headers,
                         json={"username": "mx-a-owner", "role": "viewer"})
    assert invite.status_code == 404

    # [new] invitation acceptance is restricted to the invited account
    member_candidate = register(client, "mx-c-user")
    invitation = client.post(f"/api/households/{home_a['id']}/invitations", headers=bearer(owner["access_token"]),
                             json={"username": "mx-c-user", "role": "member"}).json()
    wrong = client.post(f"/api/households/invitations/{invitation['id']}/accept", headers=outsider_headers)
    assert wrong.status_code == 404
    _no_leak(wrong, home_a["name"], home_b_name, "mx-c-user")


# ------------------------------------------------------------------------- F2 chat
def test_matrix_chat_sessions_messages_feedback_isolation(client, app, settings):
    """Chat family is account-owned: sessions/messages/chat/feedback of another
    account 404 even inside the platform [new; account-scoped design]."""
    from backend.tests.test_official_device_fix_round1 import register as device_register

    demo_headers = login(client, "demo", settings.demo_initial_password)
    other = device_register(client, "mx-chat-other")
    session = client.post("/api/chat/sessions", headers=demo_headers, json={}).json()
    # 写入一条可引用的消息（无需 LLM：直接持久化一条助手消息）
    from backend.app.db.models import ConversationHistory

    with app.state.database.session_factory.begin() as db:
        message_id = db.execute(
            ConversationHistory.__table__.insert().values(session_id=session["id"], role="assistant",
                                                          content="占位回复")).inserted_primary_key[0]
    other_headers = bearer(other["access_token"])
    assert client.get(f"/api/chat/sessions/{session['id']}/messages", headers=other_headers).status_code == 404
    assert client.patch(f"/api/chat/sessions/{session['id']}", headers=other_headers,
                        json={"title": "抢占"}).status_code == 404
    assert client.delete(f"/api/chat/sessions/{session['id']}", headers=other_headers).status_code == 404
    chat = client.post(f"/api/chat/sessions/{session['id']}/chat", headers=other_headers, json={"message": "x"})
    assert chat.status_code == 404
    feedback = client.post(f"/api/chat/messages/{message_id}/feedback", headers=other_headers, json={"feedback": "like"})
    assert feedback.status_code == 404
    _no_leak(feedback, "占位回复")


# ------------------------------------------------------- F3 devices/inventory/tickets
def test_matrix_device_and_ticket_families_isolation(client, app):
    """Devices/restock/tickets: household-scoped catalog [ref repo-level] and
    API-level 404 for foreign model ids / foreign ticket ids [new]."""
    from backend.tests.test_official_device_fix_round1 import register as device_register

    admin_headers = login(client, "admin", "AdminTest@123")
    owner = device_register(client, "mx-dev-owner")
    attacker = device_register(client, "mx-dev-attacker")
    home_a = household_id_of(app, owner["account"]["id"])
    home_b = household_id_of(app, attacker["account"]["id"])

    created = client.post(f"/api/admin/devices?household_id={home_a}", headers=admin_headers, json={
        "model_id": "MX-A-ONLY", "model_name": "A家专属", "category": "扫地机器人",
        "specs": json.dumps({"suit": "大户型"}), "description": "仅A家庭目录", "price": 1,
        "features": json.dumps(["自动集尘"])})
    assert created.status_code in (200, 201), created.text
    owner_headers = bearer(owner["access_token"])
    attacker_headers = bearer(attacker["access_token"])
    assert client.get("/api/devices/MX-A-ONLY", headers=owner_headers).status_code == 200
    detail = client.get("/api/devices/MX-A-ONLY", headers=attacker_headers)
    assert detail.status_code == 404
    restock = client.post("/api/devices/restock-notify/MX-A-ONLY", headers=attacker_headers)
    assert restock.status_code == 404
    _no_leak(restock, "A家专属", "仅A家庭目录")

    # 工单：account+household 双重归属，跨账号详情 404
    ticket = client.post("/api/tickets", headers=owner_headers,
                         json={"title": "A家工单", "content": "内容"}).json()
    assert client.get(f"/api/tickets/my/{ticket['id']}", headers=attacker_headers).status_code == 404
    assert client.post("/api/tickets", headers=attacker_headers,
                       json={"title": "盗用", "content": "x"}).status_code in (200, 201)
    mine = client.get("/api/tickets/my", headers=attacker_headers).json()
    assert all(row["id"] != ticket["id"] for row in mine)


# ------------------------------------------------------ F4 home daily family (new cells)
@pytest.mark.parametrize("family", ["tasks", "shopping", "memos"])
def test_matrix_home_daily_writes_isolated_by_household(client, app, family):
    """Tasks/shopping/memos: foreign resource ids 404 and never appear in own lists [new]."""
    from backend.tests.test_official_device_fix_round1 import register as device_register

    owner = device_register(client, f"mx-home-{family}-owner")
    attacker = device_register(client, f"mx-home-{family}-atk")

    def create(headers):
        if family == "tasks":
            return client.post("/api/home/tasks", headers=headers,
                               json={"title": "A家任务"}).json()["id"], "/api/home/tasks"
        if family == "shopping":
            return client.post("/api/home/shopping-list", headers=headers,
                               json={"name": "A家物品"}).json()["id"], "/api/home/shopping-list"
        return client.post("/api/home/memos", headers=headers, json={"content": "A家备忘"}).json()["id"], "/api/home/memos"

    resource_id, list_path = create(bearer(owner["access_token"]))
    create(bearer(attacker["access_token"]))
    items = client.get(list_path, headers=bearer(attacker["access_token"])).json()["items"]
    assert all(str(item.get("id")) != str(resource_id) for item in items)
    patch_by = {"tasks": (f"/api/home/tasks/{resource_id}", {"title": "抢占"}),
                "shopping": (f"/api/home/shopping-list/{resource_id}", {"name": "抢占"}),
                "memos": (f"/api/home/memos/{resource_id}", {"content": "抢占"})}[family]
    response = client.patch(patch_by[0], headers=bearer(attacker["access_token"]), json=patch_by[1])
    assert response.status_code == 404
    _no_leak(response, "A家任务", "A家物品", "A家备忘")
    if family == "tasks":
        assert client.post(f"/api/home/tasks/{resource_id}/complete",
                           headers=bearer(attacker["access_token"])).status_code == 404
    else:
        assert client.delete(patch_by[0], headers=bearer(attacker["access_token"])).status_code == 404


def test_matrix_reminders_and_notifications_isolated_by_household(client, app):
    """Schedules/notifications: foreign ids 404, own lists stay clean [new]."""
    from backend.tests.test_official_device_fix_round1 import register as device_register

    owner = device_register(client, "mx-rem-owner")
    attacker = device_register(client, "mx-rem-atk")
    owner_headers = bearer(owner["access_token"])
    task = client.post("/api/home/tasks", headers=owner_headers, json={"title": "提醒载体"}).json()
    reminder = client.post("/api/home/reminders", headers=owner_headers,
                           json={"task_id": task["id"], "interval_minutes": 60}).json()

    attacker_headers = bearer(attacker["access_token"])
    assert client.delete(f"/api/home/reminders/{reminder['id']}", headers=attacker_headers).status_code == 404
    assert client.post(f"/api/home/reminders", headers=attacker_headers,
                       json={"task_id": task["id"], "interval_minutes": 30}).status_code == 404
    own_reminders = client.get("/api/home/reminders", headers=attacker_headers).json()["items"]
    assert all(item["id"] != reminder["id"] for item in own_reminders)

    with app.state.database.session_factory.begin() as db:
        from backend.app.db.base import uuid_str
        from backend.app.db.models import Notification

        home_a = household_id_of(app, owner["account"]["id"])
        notification_id = uuid_str()
        db.add(Notification(id=notification_id, household_id=home_a, title="A家通知", kind="general"))
    notifications = client.get("/api/home/notifications", headers=attacker_headers).json()["items"]
    assert all(item["title"] != "A家通知" for item in notifications)
    assert client.post(f"/api/home/notifications/{notification_id}/read",
                       headers=attacker_headers).status_code == 404


def test_matrix_preferences_and_proposals_isolated_by_household(client, app):
    """Structured preferences: foreign proposal confirm/reject 404 [new]."""
    from backend.tests.test_official_device_fix_round1 import register as device_register

    owner = device_register(client, "mx-pref-owner")
    attacker = device_register(client, "mx-pref-atk")
    owner_headers = bearer(owner["access_token"])
    proposal = client.post("/api/home/preferences/proposals", headers=owner_headers,
                           json={"key": "口味", "value": "清淡", "reason": "用户在对话中确认"}).json()
    attacker_headers = bearer(attacker["access_token"])
    assert client.post(f"/api/home/preferences/proposals/{proposal['id']}/confirm",
                       headers=attacker_headers).status_code == 404
    assert client.post(f"/api/home/preferences/proposals/{proposal['id']}/reject",
                       headers=attacker_headers).status_code == 404
    assert client.get("/api/home/preferences/proposals", headers=attacker_headers).json()["items"] == []


# ------------------------------------------- F5/F9/F10 admin surfaces: 403 for users
def test_matrix_admin_surfaces_reject_household_users_with_403(client, app, settings):
    """Knowledge/models/tools/reviews/audit/ops/admin endpoints: stable 403 for
    non-admin accounts across every admin surface family [consolidated; some
    exist scattered per phase]."""
    user_headers = login(client, "demo", settings.demo_initial_password)
    surfaces = [
        "/api/knowledge/files", "/api/knowledge/faq",
        "/api/models/endpoints", "/api/models/profiles", "/api/models/bindings",
        "/api/models/households/x/grants",
        "/api/tools/catalog",
        "/api/reviews/queue", "/api/reviews/reports",
        "/api/audit/agent-runs", "/api/audit/model-usage", "/api/audit/celery-tasks",
        "/api/audit/admin-actions", "/api/audit/deployments/pointers", "/api/audit/models/revisions",
        "/api/ops/overview", "/api/ops/quota/incidents", "/api/ops/tasks/types",
        "/api/admin/dashboard", "/api/admin/accounts", "/api/admin/households",
        "/api/admin/inventory", "/api/admin/tickets", "/api/admin/tasks",
    ]
    for path in surfaces:
        response = client.get(path, headers=user_headers)
        assert response.status_code == 403, f"{path} -> {response.status_code}"
    mutations = [
        ("post", "/api/knowledge/files", {}),
        ("post", "/api/knowledge/rebuild", {}),
        ("put", "/api/knowledge/faq/x/review", {"status": "approved"}),
        ("post", "/api/models/endpoints", {}),
        ("put", "/api/models/bindings/primary_chat", {"model_id": "x"}),
        ("put", "/api/models/households/x/quota", {"monthly_limit": 1, "requests_per_minute": 1}),
        ("put", "/api/models/households/x/grants/y", {}),
        ("put", "/api/tools/x/grants", {"household_id": "x"}),
        ("post", "/api/reviews/submissions/x/decision", {"action": "approve"}),
        ("post", "/api/reviews/entries/x/takedown", {"reason": "x"}),
        ("post", "/api/admin/devices", {}),
        ("put", "/api/admin/inventory", {}),
        ("post", "/api/ops/model-usage/release-expired", {}),
        ("post", "/api/admin/tasks/reminder_dispatch/run", {}),
    ]
    for method, path, body in mutations:
        call = getattr(client, method)
        response = call(path, headers=user_headers, json=body)
        assert response.status_code == 403, f"{method.upper()} {path} -> {response.status_code}"


# ---------------------------------------------------------------- F6/F7 creator+market
def test_matrix_creator_subresources_isolated(client, app):
    """Draft knowledge/trial/publish subresources of a foreign draft 404 [new;
    draft CRUD isolation exists in test_creator_api]."""
    from backend.tests.test_official_device_fix_round1 import register as device_register

    owner = device_register(client, "mx-cr-owner")
    attacker = device_register(client, "mx-cr-atk")
    owner_headers = bearer(owner["access_token"])
    draft = client.post("/api/creator/drafts", headers=owner_headers,
                        json={"title": "A家草稿", "description": "描述"}).json()
    attacker_headers = bearer(attacker["access_token"])
    assert client.get(f"/api/creator/drafts/{draft['id']}", headers=attacker_headers).status_code == 404
    assert client.patch(f"/api/creator/drafts/{draft['id']}", headers=attacker_headers,
                        json={"title": "抢占"}).status_code == 404
    knowledge = client.get(f"/api/creator/drafts/{draft['id']}/knowledge", headers=attacker_headers)
    assert knowledge.status_code == 404
    trial = client.post(f"/api/creator/drafts/{draft['id']}/trial", headers=attacker_headers, json={})
    assert trial.status_code == 404
    publish = client.post(f"/api/creator/drafts/{draft['id']}/publish", headers=attacker_headers, json={})
    assert publish.status_code == 404
    listed = client.get("/api/creator/drafts", headers=attacker_headers).json()
    assert all(row["id"] != draft["id"] for row in listed)


def test_matrix_market_installs_favorites_and_reports_isolated(client, app, monkeypatch, settings):
    """Market family: foreign installation upgrade/uninstall 404; favorites and
    installs never cross households [new; publish chain isolation exists]."""
    from backend.tests.test_celery_tasks import _home
    from backend.tests.test_market_install_upgrade import _configure_runtime, _publish
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY
    from backend.tests.test_review_governance import _grant_market_ceiling

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    from backend.tests.test_official_device_fix_round1 import register as device_register

    _, home_pub = _home(app)  # demo 家庭即发布方（与既有市场链路一致）
    installer = device_register(client, "mx-mk-inst")
    attacker = device_register(client, "mx-mk-atk")
    home_inst = household_id_of(app, installer["account"]["id"])
    _grant_market_ceiling(client, home_pub)
    entry_id, _ = _publish(client, app, home_pub)
    _configure_runtime(app, home_inst, monkeypatch)
    installer_headers = bearer(installer["access_token"])
    installed = client.post(f"/api/market/entries/{entry_id}/install", headers=installer_headers,
                            json={"granted_tool_keys": ["household.shopping.write"]}).json()

    attacker_headers = bearer(attacker["access_token"])
    upgrade = client.post(f"/api/market/installations/{installed['installation_id']}/upgrade",
                          headers=attacker_headers, json={"granted_tool_keys": []})
    assert upgrade.status_code == 404
    uninstall = client.delete(f"/api/market/installations/{installed['installation_id']}",
                              headers=attacker_headers)
    assert uninstall.status_code == 404
    grants = client.put(f"/api/installations/{installed['installation_id']}/permissions",
                        headers=attacker_headers, json={"tool_id": "t", "tool_version": "1"})
    assert grants.status_code == 404
    _no_leak(uninstall, installed["installation_id"])

    favorite = client.post(f"/api/market/entries/{entry_id}/favorite", headers=installer_headers)
    assert favorite.status_code == 201, favorite.text
    attacker_favorites = client.get("/api/market/favorites", headers=attacker_headers).json()
    assert attacker_favorites == [] or all(row["entry_id"] != entry_id for row in attacker_favorites)
    attacker_installs = client.get("/api/market/my", headers=attacker_headers).json()
    assert all(row.get("entry_id") != entry_id for row in attacker_installs)

    reported = client.post(f"/api/market/entries/{entry_id}/reports", headers=attacker_headers,
                           json={"reason": "测试举报"})
    assert reported.status_code == 201  # 举报是对平台公开条目的合法动作，走管理员处理
    admin_headers = login(client, "admin", "AdminTest@123")
    queue_body = client.get("/api/reviews/reports", headers=admin_headers).json()
    queue = queue_body["items"] if isinstance(queue_body, dict) else queue_body
    assert any(row["id"] == reported.json()["id"] for row in queue)


# ------------------------------------------------------------------- role matrix
def _invite_and_accept(client, owner_token, owner_username, invitee_username, role):
    household = client.get("/api/households/current", headers=bearer(owner_token)).json()
    invitee = register(client, invitee_username)
    invitation = client.post(f"/api/households/{household['id']}/invitations", headers=bearer(owner_token),
                             json={"username": invitee_username, "role": role})
    assert invitation.status_code == 201, invitation.text
    accepted = client.post(f"/api/households/invitations/{invitation.json()['id']}/accept",
                           headers=bearer(invitee["access_token"]))
    assert accepted.status_code == 200, accepted.text
    return household["id"], invitee


def test_matrix_role_rules_for_governed_action_families(client, app):
    """Role matrix for the families each role governs:
    invitations+publish+install/permission = owner/admin only; creator writes =
    owner/admin/member; chat and household daily writes = every member role
    (documented Phase 5 design), while viewer stays blocked from governed writes."""
    owner = register(client, "rmx-owner")
    owner_headers = bearer(owner["access_token"])
    home, admin_member = _invite_and_accept(client, owner["access_token"], "rmx-owner", "rmx-admin", "admin")
    _, plain_member = _invite_and_accept(client, owner["access_token"], "rmx-owner", "rmx-member", "member")
    _, viewer = _invite_and_accept(client, owner["access_token"], "rmx-owner", "rmx-viewer", "viewer")
    admin_headers, member_headers, viewer_headers = (bearer(admin_member["access_token"]),
                                                     bearer(plain_member["access_token"]),
                                                     bearer(viewer["access_token"]))

    # 邀请：owner/admin 可发起；member/viewer 403 [ref + new admin/viewer cells]
    for index, (role_headers, expected) in enumerate(((owner_headers, 201), (admin_headers, 201),
                                                      (member_headers, 403), (viewer_headers, 403))):
        candidate = f"rmx-candidate-{index}"
        client.post("/api/auth/register", json={"username": candidate, "password": "Password@123", "city": "杭州"})
        response = client.post(f"/api/households/{home}/invitations", headers=role_headers,
                               json={"username": candidate, "role": "member"})
        assert response.status_code == expected, response.text

    # 创作写操作：member 可用，viewer 403 [ref test_creator_api viewer cell + member cell]
    member_draft = client.post("/api/creator/drafts", headers=member_headers,
                               json={"title": "成员草稿", "description": "d"})
    assert member_draft.status_code == 201, member_draft.text
    viewer_draft = client.post("/api/creator/drafts", headers=viewer_headers,
                               json={"title": "只读草稿", "description": "d"})
    assert viewer_draft.status_code == 403
    assert viewer_draft.json()["detail"]["code"] == "CREATOR_ROLE_FORBIDDEN"

    # 发布/提交审核：仅 owner/admin [new]
    publish = client.post(f"/api/creator/drafts/{member_draft.json()['id']}/publish", headers=member_headers,
                          json={})
    assert publish.status_code == 403
    submit = client.post(f"/api/creator/drafts/{member_draft.json()['id']}/submit-review", headers=member_headers,
                         json={"category": "生活"})
    assert submit.status_code == 403
    assert submit.json()["detail"]["code"] == "PUBLISH_ROLE_FORBIDDEN"

    # 市场安装与升级：member/viewer 403 [new]
    install = client.post("/api/market/entries/any-entry/install", headers=member_headers, json={})
    assert install.status_code == 403
    assert install.json()["detail"]["code"] == "INSTALLATION_PERMISSION_FORBIDDEN"
    viewer_install = client.post("/api/market/entries/any-entry/install", headers=viewer_headers, json={})
    assert viewer_install.status_code == 403

    # 安装授权：member/viewer 403（对不存在安装稳定 404） [new]
    grant = client.put("/api/installations/any-installation/permissions", headers=member_headers,
                       json={"tool_id": "t", "tool_version": "1"})
    assert grant.status_code in (403, 404)  # 不存在安装先 404；存在时必须 403
    grant_own = client.put("/api/installations/any-installation/permissions", headers=viewer_headers,
                           json={"tool_id": "t", "tool_version": "1"})
    assert grant_own.status_code in (403, 404)

    # 聊天与家庭日常写操作：全部角色可用（含 viewer，Phase 5 设计）
    session = client.post("/api/chat/sessions", headers=viewer_headers, json={})
    assert session.status_code == 201, session.text
    task = client.post("/api/home/tasks", headers=viewer_headers, json={"title": "viewer任务"})
    assert task.status_code == 201, task.text
    shopping = client.post("/api/home/shopping-list", headers=member_headers, json={"name": "member物品"})
    assert shopping.status_code == 201, shopping.text


def test_matrix_role_rules_approval_decisions(client, app):
    """High-risk approval decisions: principal or household owner/admin only [ref
    cross-household cell exists in test_agent_platform_api; role cells new]."""
    from datetime import timedelta

    from sqlalchemy import select as sa_select

    from backend.app.core.security import create_token, hash_password
    from backend.app.db.base import utcnow, uuid_str
    from backend.app.db.models import (Account, AgentDefinition, AgentDeployment, AgentRun, AgentVersion,
                                       ApprovalRequest, Household, HouseholdMember, ToolDefinition)

    with app.state.database.session_factory.begin() as db:
        owner = Account(username="apx-owner", password_hash=hash_password("Passw0rd!", rounds=4))
        db.add(owner); db.flush()
        home = Household(name="审批家庭", created_by_account_id=owner.id)
        db.add(home); db.flush()
        member = HouseholdMember(household_id=home.id, account_id=owner.id, role="owner")
        db.add(member); db.flush()
        viewer = Account(username="apx-viewer", password_hash=hash_password("Passw0rd!", rounds=4))
        outsider = Account(username="apx-outsider", password_hash=hash_password("Passw0rd!", rounds=4))
        db.add_all([viewer, outsider]); db.flush()
        db.add(HouseholdMember(household_id=home.id, account_id=viewer.id, role="viewer"))
        tool = db.scalar(sa_select(ToolDefinition).limit(1))
        agent = AgentDefinition(household_id=home.id, name="审批Agent")
        db.add(agent); db.flush()
        version = AgentVersion(household_id=home.id, agent_id=agent.id, version=1,
                               definition_json='{"prompt":"p","tools":[]}', published_at=utcnow())
        db.add(version); db.flush()
        deployment = AgentDeployment(household_id=home.id, agent_version_id=version.id, visibility="private")
        db.add(deployment); db.flush()
        run = AgentRun(household_id=home.id, deployment_id=deployment.id, principal_id=owner.id,
                       member_id=member.id, session_id="sess-apx", agent_version_id=version.id,
                       deployment_revision=1, model_id="m", model_revision=1, status="interrupted")
        db.add(run); db.flush()
        approval = ApprovalRequest(id=uuid_str(), household_id=home.id, run_id=run.id, principal_id=owner.id,
                                   tool_id=tool.id, tool_version="1", deployment_id=deployment.id,
                                   deployment_revision=1, agent_version_id=version.id,
                                   arguments_digest="d" * 64, arguments_json="{}", idempotency_key="apx-key-1",
                                   status="pending")
        db.add(approval)
        approval_id = approval.id
        viewer_account, outsider_account = viewer, outsider

    def token_headers(account):
        return {"Authorization": "Bearer " + create_token(account, app.state.settings.jwt_secret,
                                                          "access", timedelta(minutes=5))}

    future = {"expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()}

    decision = client.post(f"/api/approvals/{approval_id}/approve", headers=token_headers(viewer_account),
                           json=future)
    assert decision.status_code == 403
    assert decision.json()["detail"]["code"] == "APPROVAL_DECISION_FORBIDDEN"
    outsider_decision = client.post(f"/api/approvals/{approval_id}/approve",
                                    headers=token_headers(outsider_account), json=future)
    assert outsider_decision.status_code == 403
    owner_decision = client.post(f"/api/approvals/{approval_id}/approve", headers=token_headers(owner),
                                 json=future)
    assert owner_decision.status_code in (200, 409)  # 家长可决定（409=已被消费等稳定状态）
