# -*- coding: utf-8 -*-
"""Phase 7 full validation-chain E2E through production APIs with deterministic
models and no external network:

submit -> auto pre-check -> admin return -> fix -> resubmit -> approve ->
cross-household marketplace install -> per-tool authorization -> pinned-version
run through deployment-bound chat -> publisher releases v2 -> user confirms
upgrade -> new-permission reauthorization -> old pin released;
plus the reject path, takedown hiding/blocking with existing installs running,
report -> admin handling, install count idempotency and cross-household
isolation of drafts/reviews."""
import json

from sqlalchemy import select

from backend.tests.test_celery_tasks import _home
from backend.tests.test_creator_sandbox import _grant_ceiling
from backend.tests.test_review_governance import MARKET_CARD, _login


def _register(client, username):
    response = client.post("/api/auth/register", json={"username": username, "password": "Chain@123",
                                                       "city": "上海"})
    assert response.status_code == 201, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["account"]["id"]


def _household_of(app, account_id):
    from backend.app.db.models import HouseholdMember

    with app.state.database.session_factory() as db:
        return db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account_id))


def _admin(client):
    token = client.post("/api/auth/login", json={"username": "admin",
                                                 "password": "AdminTest@123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _chat(client, headers, deployment_id, message):
    session = client.post("/api/chat/sessions", headers=headers,
                          json={"title": "链路会话", "deployment_id": deployment_id})
    assert session.status_code == 201, session.text
    session_id = session.json()["id"]
    with client.stream("POST", f"/api/chat/sessions/{session_id}/chat", headers=headers,
                       json={"message": message}) as response:
        assert response.status_code == 200, response.text
        events = [line.split(":", 1)[1].strip() for line in response.iter_lines() if line.startswith("event:")]
    messages = client.get(f"/api/chat/sessions/{session_id}/messages", headers=headers).json()
    return events, (messages[-1]["content"] if messages else "")


def test_full_validation_chain_e2e(client, app, settings, monkeypatch):
    from backend.app.db.models import (AgentRun, AgentVersion, CreatorDraft, MarketEntry, MarketInstall,
                                       ReviewSubmission)
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    creator_id, creator_household = _home(app)
    _grant_ceiling(client, creator_household, "household.shopping.write")
    _grant_ceiling(client, creator_household, "household.memo.write")
    creator = _login(client)
    admin = _admin(client)

    # 1. 创作者草稿 + 校验 + 沙盒试运行（确定性 workflow，无模型调用）
    card = json.loads(json.dumps(MARKET_CARD, ensure_ascii=False))
    draft = client.post("/api/creator/drafts", headers=creator,
                        json={"title": "链路草稿", "description": card["description"]}).json()
    assert client.patch(f"/api/creator/drafts/{draft['id']}", headers=creator,
                        json={"intent_card": card}).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft['id']}/validate", headers=creator).status_code == 200
    trial = client.post(f"/api/creator/drafts/{draft['id']}/trial", headers=creator,
                        json={"granted_tool_keys": ["household.shopping.write"]})
    assert trial.status_code == 200 and trial.json()["status"] in {"succeeded", "completed"}, trial.text

    # 2. 提交公开审核：自动预检完成并持久化为审核证据
    submitted = client.post(f"/api/creator/drafts/{draft['id']}/submit-review", headers=creator,
                            json={"category": "生活"})
    assert submitted.status_code == 201, submitted.text
    submission_v1 = submitted.json()
    assert submission_v1["status"] == "review_pending"
    assert submission_v1["precheck"]["passed"] is True
    assert submission_v1["risk_summary"]["status"] == "unavailable"  # 审核模型未配置：不阻塞

    # 3. 管理员退回：草稿回到 draft 修复
    queue = client.get("/api/reviews/queue?status=review_pending", headers=admin).json()
    assert any(item["submission_id"] == submission_v1["submission_id"] for item in queue["items"])
    detail = client.get(f"/api/reviews/submissions/{submission_v1['submission_id']}", headers=admin).json()
    assert detail["precheck"]["passed"] is True
    assert detail["sandbox"]["cases"] and detail["sandbox"]["cases"][0]["status"] == "passed"
    assert detail["listing_preview"]["name"] == card["name"]
    returned = client.post(f"/api/reviews/submissions/{submission_v1['submission_id']}/decision", headers=admin,
                           json={"action": "return", "reason": "请补充适用场景"})
    assert returned.status_code == 200 and returned.json()["status"] == "returned"
    with app.state.database.session_factory() as db:
        assert db.get(CreatorDraft, draft["id"]).status == "draft"

    # 4. 修复后重新提交（新不可变版本）并通过人工审核
    card["description"] = "把采购项写入家庭购物清单（已补充适用场景）"
    assert client.patch(f"/api/creator/drafts/{draft['id']}", headers=creator,
                        json={"intent_card": card}).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft['id']}/validate", headers=creator).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft['id']}/trial", headers=creator,
                       json={"granted_tool_keys": ["household.shopping.write"]}).json()["status"] in {
        "succeeded", "completed"}
    resubmitted = client.post(f"/api/creator/drafts/{draft['id']}/submit-review", headers=creator,
                              json={"category": "生活"}).json()
    assert resubmitted["status"] == "review_pending"
    assert resubmitted["agent_version_id"] != submission_v1["agent_version_id"]
    approved = client.post(f"/api/reviews/submissions/{resubmitted['submission_id']}/decision", headers=admin,
                           json={"action": "approve", "reason": "修复后通过"})
    assert approved.status_code == 200 and approved.json()["status"] == "approved"

    with app.state.database.session_factory() as db:
        entry = db.scalar(select(MarketEntry).where(MarketEntry.status == "published"))
        assert entry.current_version_id == resubmitted["agent_version_id"]
        assert db.get(CreatorDraft, draft["id"]).status == "public_published"
        entry_id = entry.id

    # 5. 第二个家庭从市场安装：固定审核版本 + 逐项授权（默认拒绝）
    buyer, buyer_account = _register(client, "chain-buyer")
    buyer_household = _household_of(app, buyer_account)
    with app.state.database.session_factory() as db:
        configured_runtime(db, buyer_household)
        configured_runtime(db, buyer_household, purpose="embedding")

    listings = client.get("/api/market/listings", headers=buyer).json()
    assert any(item["id"] == entry_id for item in listings["entries"])
    install = client.post(f"/api/market/entries/{entry_id}/install", headers=buyer,
                          json={"granted_tool_keys": []}).json()
    # 未授权可逆写工具：安装成功但清单写入未授予（默认拒绝）
    assert install["pinned_version"] >= 1
    assert "household.shopping.write" not in install["granted_tool_keys"]
    assert "household.recipe.scale" in install["granted_tool_keys"]

    # 6. deployment 绑定聊天：固定版本运行（未授权工具不可见 → 工作流失败 PERMISSION_REQUIRED）
    class UnusedModel:
        def invoke(self, messages):
            raise AssertionError("workflow run must not call the chat model")

    app.state.chat_model_builder = lambda resolved: UnusedModel()
    events, content = _chat(client, buyer, install["deployment_id"], "加鸡蛋")
    assert "run" in events
    assert "鸡蛋已加入清单" not in content  # shopping.write 未授权
    with app.state.database.session_factory() as db:
        runs = db.scalars(select(AgentRun).where(AgentRun.household_id == buyer_household,
                                                 AgentRun.deployment_id == install["deployment_id"])).all()
        assert runs and runs[-1].status == "failed" and runs[-1].error_code == "PERMISSION_REQUIRED"

    # 逐项授权后重跑：固定审核版本立即可用
    with app.state.database.session_factory() as db:
        from backend.app.db.models import ToolDefinition

        grant_tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "household.shopping.write"))
    granted = client.put(f"/api/installations/{install['installation_id']}/permissions", headers=buyer,
                         json={"tool_id": grant_tool.id, "tool_version": grant_tool.version})
    assert granted.status_code == 201, granted.text
    events, content = _chat(client, buyer, install["deployment_id"], "再加鸡蛋")
    assert "鸡蛋已加入清单" in content
    with app.state.database.session_factory() as db:
        local_version = db.get(AgentVersion, install["agent_version_id"])
        assert local_version.household_id == buyer_household
        assert local_version.definition_json == db.get(AgentVersion,
                                                       resubmitted["agent_version_id"]).definition_json
        pinned = db.scalar(select(MarketInstall).where(
            MarketInstall.installation_id == install["installation_id"]))
        assert pinned.pinned_version_number == install["pinned_version"]

    # 7. 发布者发布 v2（新增权限）：既有安装固定旧版本，出现待更新
    card["tools"] = ["household.recipe.scale", "household.shopping.write", "household.memo.write"]
    card["workflow"]["nodes"][1]["text"] = "鸡蛋已加入清单（第二版）"
    assert client.patch(f"/api/creator/drafts/{draft['id']}", headers=creator,
                        json={"intent_card": card}).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft['id']}/validate", headers=creator).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft['id']}/trial", headers=creator,
                       json={"granted_tool_keys": ["household.shopping.write"]}).json()["status"] in {
        "succeeded", "completed"}
    v2 = client.post(f"/api/creator/drafts/{draft['id']}/submit-review", headers=creator,
                     json={"category": "生活"}).json()
    assert client.post(f"/api/reviews/submissions/{v2['submission_id']}/decision", headers=admin,
                       json={"action": "approve", "reason": "v2 通过"}).status_code == 200

    mine = client.get("/api/market/my", headers=buyer).json()
    assert mine[0]["update_available"] is True
    assert {item["key"] for item in mine[0]["update"]["new_permissions"]} == {"household.memo.write"}
    # 未确认升级：旧版本继续工作
    _events, still_old = _chat(client, buyer, install["deployment_id"], "还没升级")
    assert "鸡蛋已加入清单" in still_old and "第二版" not in still_old

    # 8. 确认升级 + 新权限重新授权 → 旧 pin 释放
    upgraded = client.post(f"/api/market/installations/{mine[0]['installation_id']}/upgrade", headers=buyer,
                           json={"granted_tool_keys": ["household.memo.write"]})
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()["pinned_version"] == mine[0]["update"]["to_version"]
    assert set(upgraded.json()["granted_tool_keys"]) >= {"household.memo.write"}
    _events, new_content = _chat(client, buyer, install["deployment_id"], "升级后加购")
    assert "鸡蛋已加入清单（第二版）" in new_content
    with app.state.database.session_factory() as db:
        pinned = db.scalar(select(MarketInstall).where(
            MarketInstall.installation_id == upgraded.json()["installation_id"]))
        assert pinned.pinned_version_number == mine[0]["update"]["to_version"]

    # 9. 举报 → 管理员处理（审计）
    report = client.post(f"/api/market/entries/{entry_id}/reports", headers=buyer,
                         json={"reason": "升级后行为异常"})
    assert report.status_code == 201
    reports = client.get("/api/reviews/reports?status=open", headers=admin).json()
    assert any(item["id"] == report.json()["id"] for item in reports["items"])
    handled = client.post(f"/api/reviews/reports/{report.json()['id']}/handle", headers=admin,
                          json={"action": "resolve", "note": "已复现并通知创作者"})
    assert handled.status_code == 200 and handled.json()["status"] == "resolved"

    # 10. 下架：隐藏 + 阻止新安装；既有安装继续运行
    assert client.post(f"/api/reviews/entries/{entry_id}/takedown", headers=admin,
                       json={"reason": "举报成立"}).status_code == 200
    hidden = client.get("/api/market/listings", headers=buyer).json()
    assert not any(item["id"] == entry_id for item in hidden["entries"])
    late, _late_account = _register(client, "chain-late-user")
    blocked = client.post(f"/api/market/entries/{entry_id}/install", headers=late,
                          json={"granted_tool_keys": []})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "MARKET_ENTRY_DEPRECATED"
    _events, after_takedown = _chat(client, buyer, install["deployment_id"], "下架后运行")
    assert "鸡蛋已加入清单（第二版）" in after_takedown  # 已安装固定版本继续运行

    # 11. 拒绝路径：创作者 v3 被拒绝，市场指针不动
    card["description"] = "第三版"
    assert client.patch(f"/api/creator/drafts/{draft['id']}", headers=creator,
                        json={"intent_card": card}).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft['id']}/validate", headers=creator).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft['id']}/trial", headers=creator,
                       json={"granted_tool_keys": ["household.shopping.write"]}).json()["status"] in {
        "succeeded", "completed"}
    v3 = client.post(f"/api/creator/drafts/{draft['id']}/submit-review", headers=creator,
                     json={"category": "生活"}).json()
    rejected = client.post(f"/api/reviews/submissions/{v3['submission_id']}/decision", headers=admin,
                           json={"action": "reject", "reason": "变更说明不足"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    with app.state.database.session_factory() as db:
        entry = db.get(MarketEntry, entry_id)
        assert entry.current_version_id == v2["agent_version_id"]  # 公共指针不动
        submission = db.get(ReviewSubmission, v3["submission_id"])
        assert submission.decision_reason == "变更说明不足"

    # 12. 安装计数按家庭幂等：buyer 家庭只计一次
    assert client.get(f"/api/market/entries/{entry_id}", headers=buyer).status_code == 404  # 下架后不可见
    my_after = client.get("/api/market/my", headers=buyer).json()
    assert my_after[0]["deprecated"] is True
    with app.state.database.session_factory() as db:
        entry = db.get(MarketEntry, entry_id)
        assert entry.install_count == 1  # buyer + 后来的 late 被拒

    # 13. 跨家庭隔离：buyer 看不到创作者草稿/提交
    assert client.get(f"/api/creator/drafts/{draft['id']}", headers=buyer).status_code == 404
    assert client.get("/api/creator/submissions", headers=buyer).json() == []
