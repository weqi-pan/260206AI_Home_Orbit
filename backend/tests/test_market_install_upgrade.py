# -*- coding: utf-8 -*-
"""Phase 7 marketplace install/upgrade governance: installs pin the reviewed
public version into the household through the Phase 3 installation services
with default-deny per-tool authorization; install count is idempotent per
household; upgrades are explicit with reauthorization of new permissions;
declined upgrades keep the old pinned version running; uninstall optionally
revokes permissions; takedown blocks new installs but existing installations
keep running; public knowledge assets are re-vectorized into the installer
household (cross-household isolation)."""
import json

from sqlalchemy import select

from backend.tests.test_creator_publish import fake_vectors  # noqa: F401 (fixture)
from backend.tests.test_celery_tasks import _home
from backend.tests.test_creator_sandbox import _grant_ceiling
from backend.tests.test_review_governance import (MARKET_CARD, _admin_headers, _grant_market_ceiling,
                                                  _login, _submit, _validated_draft)


def _register(client, username):
    response = client.post("/api/auth/register", json={"username": username, "password": "Market@123",
                                                       "city": "上海"})
    assert response.status_code == 201, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["account"]["id"]


def _household_of(app, account_id):
    from backend.app.db.models import HouseholdMember

    with app.state.database.session_factory() as db:
        return db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account_id))


def _publish(client, app, household_id, card=None, category="生活"):
    """Returns (entry_id, draft_id) after a full validated -> trial -> submit -> approve cycle."""
    draft_id = _validated_draft(client, app, household_id, card=card)
    body = _submit(client, draft_id, category=category).json()
    decision = client.post(f"/api/reviews/submissions/{body['submission_id']}/decision",
                           headers=_admin_headers(client), json={"action": "approve", "reason": "通过"})
    assert decision.status_code == 200, decision.text
    from backend.app.db.models import MarketEntry

    with app.state.database.session_factory() as db:
        entry = db.scalar(select(MarketEntry).where(MarketEntry.name == (card or MARKET_CARD)["name"]))
        assert entry is not None
        return entry.id, draft_id


def _install(client, headers, entry_id, granted=()):
    return client.post(f"/api/market/entries/{entry_id}/install", headers=headers,
                       json={"granted_tool_keys": list(granted)})


def _configure_runtime(app, household_id, monkeypatch):
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    with app.state.database.session_factory() as db:
        configured_runtime(db, household_id)
        configured_runtime(db, household_id, purpose="embedding")


def _chat_and_read(client, headers, deployment_id, message):
    session = client.post("/api/chat/sessions", headers=headers,
                          json={"title": "市场会话", "deployment_id": deployment_id})
    assert session.status_code == 201, session.text
    session_id = session.json()["id"]
    with client.stream("POST", f"/api/chat/sessions/{session_id}/chat", headers=headers,
                       json={"message": message}) as response:
        assert response.status_code == 200, response.text
        for _line in response.iter_lines():
            pass
    messages = client.get(f"/api/chat/sessions/{session_id}/messages", headers=headers).json()
    return messages[-1]["content"] if messages else ""


def test_install_pins_reviewed_version_with_default_deny_and_idempotent_count(client, app):
    from backend.app.db.models import (AgentInstallation, InstallationPermission, MarketEntry, MarketInstall,
                                       ToolDefinition)

    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    entry_id, _draft_id = _publish(client, app, household_id)

    installer, installer_account = _register(client, "market-installer")
    response = _install(client, installer, entry_id, granted=["household.shopping.write"])
    assert response.status_code == 201, response.text
    install = response.json()
    listing_current = client.get(f"/api/market/entries/{entry_id}",
                                 headers=_login(client)).json()["current_version"]["number"]
    assert install["pinned_version"] == listing_current  # 固定到当前公共审核版本

    with app.state.database.session_factory() as db:
        entry = db.get(MarketEntry, entry_id)
        assert entry.install_count == 1
        installation = db.get(AgentInstallation, install["installation_id"])
        assert installation.status == "installed" and installation.authority_slot == "active"
        market_install = db.scalar(select(MarketInstall).where(
            MarketInstall.installation_id == installation.id))
        assert market_install.pinned_version_number == listing_current
        write_tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "household.shopping.write"))
        scale_tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "household.recipe.scale"))
        granted_ids = set(db.scalars(select(InstallationPermission.tool_id).where(
            InstallationPermission.installation_id == installation.id)).all())
        assert write_tool.id in granted_ids  # 显式勾选
        assert scale_tool.id in granted_ids  # 低风险按既有策略自动授予

    # 重复安装被拒绝（幂等），计数不涨
    duplicate = _install(client, installer, entry_id, granted=["household.shopping.write"])
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "ALREADY_INSTALLED"
    with app.state.database.session_factory() as db:
        assert db.get(MarketEntry, entry_id).install_count == 1

    # 卸载后重装：同一家庭计数仍为 1；未勾选可逆写工具则不授予（默认拒绝）
    uninstall = client.delete(f"/api/market/installations/{install['installation_id']}",
                              headers=installer, params={"revoke_permissions": "true"})
    assert uninstall.status_code == 204
    reinstalled = _install(client, installer, entry_id, granted=[])
    assert reinstalled.status_code == 201, reinstalled.text
    with app.state.database.session_factory() as db:
        assert db.get(MarketEntry, entry_id).install_count == 1
        installation = db.get(AgentInstallation, reinstalled.json()["installation_id"])
        write_tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "household.shopping.write"))
        granted_ids = set(db.scalars(select(InstallationPermission.tool_id).where(
            InstallationPermission.installation_id == installation.id)).all())
        assert write_tool.id not in granted_ids

    # 第二个家庭安装：计数 +1
    other, _other_account = _register(client, "market-second")
    assert _install(client, other, entry_id).status_code == 201
    with app.state.database.session_factory() as db:
        assert db.get(MarketEntry, entry_id).install_count == 2

    assert _install(client, installer, "no-such-entry").status_code == 404


def test_uninstall_revokes_permissions_and_removes_authority(client, app):
    from backend.app.db.models import AgentInstallation, InstallationPermission

    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    entry_id, _draft_id = _publish(client, app, household_id)
    installer, _installer_account = _register(client, "market-uninstaller")
    install = _install(client, installer, entry_id, granted=["household.shopping.write"]).json()

    response = client.delete(f"/api/market/installations/{install['installation_id']}",
                             headers=installer, params={"revoke_permissions": "true"})
    assert response.status_code == 204
    with app.state.database.session_factory() as db:
        installation = db.get(AgentInstallation, install["installation_id"])
        assert installation.status == "uninstalled" and installation.authority_slot is None
        assert db.scalars(select(InstallationPermission).where(
            InstallationPermission.installation_id == installation.id)).all() == []
    # 他人家庭的安装不可卸载（稳定 404）
    stranger, _stranger_account = _register(client, "market-stranger")
    assert client.delete(f"/api/market/installations/{install['installation_id']}",
                         headers=stranger).status_code == 404


def test_upgrade_explicit_with_reauthorization_and_declined_keeps_old_running(client, app, monkeypatch):
    from backend.app.db.models import (AgentInstallation, InstallationPermission, MarketInstall,
                                       ToolDefinition)

    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    _grant_ceiling(client, household_id, "household.memo.write")
    entry_id, draft_id = _publish(client, app, household_id)

    installer, installer_account = _register(client, "market-upgrader")
    installer_household = _household_of(app, installer_account)
    _configure_runtime(app, installer_household, monkeypatch)

    class UnusedModel:
        def invoke(self, messages):
            raise AssertionError("workflow run must not call the chat model")

    app.state.chat_model_builder = lambda resolved: UnusedModel()

    install = _install(client, installer, entry_id, granted=["household.shopping.write"]).json()
    content = _chat_and_read(client, installer, install["deployment_id"], "加鸡蛋")
    assert "鸡蛋已加入清单" in content
    assert "第二版" not in content  # v1 固定版本运行

    # v2：新增 household.memo.write 权限
    creator_headers = _login(client)
    v2_card = json.loads(json.dumps(MARKET_CARD, ensure_ascii=False))
    v2_card["tools"] = ["household.recipe.scale", "household.shopping.write", "household.memo.write"]
    v2_card["workflow"]["nodes"][1]["text"] = "鸡蛋已加入清单（第二版）"
    assert client.patch(f"/api/creator/drafts/{draft_id}", headers=creator_headers,
                        json={"intent_card": v2_card}).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft_id}/validate", headers=creator_headers).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft_id}/trial", headers=creator_headers,
                       json={"granted_tool_keys": ["household.shopping.write"]}).json()["status"] in {
        "succeeded", "completed"}
    v2 = _submit(client, draft_id).json()
    assert client.post(f"/api/reviews/submissions/{v2['submission_id']}/decision", headers=_admin_headers(client),
                       json={"action": "approve", "reason": "v2 通过"}).status_code == 200

    my = client.get("/api/market/my", headers=installer).json()
    assert len(my) == 1
    mine = my[0]
    assert mine["update_available"] is True
    assert mine["update"]["to_version"] == mine["latest_version"]
    assert {item["key"] for item in mine["update"]["new_permissions"]} == {"household.memo.write"}

    # 未确认升级：旧 pin 继续工作
    content = _chat_and_read(client, installer, install["deployment_id"], "再加一次")
    assert "鸡蛋已加入清单" in content and "第二版" not in content

    # 明确升级但拒绝新权限：household.memo.write 不授予（绝不静默继承）
    upgraded = client.post(f"/api/market/installations/{mine['installation_id']}/upgrade",
                           headers=installer, json={"granted_tool_keys": []})
    assert upgraded.status_code == 200, upgraded.text
    body = upgraded.json()
    assert body["pinned_version"] == mine["update"]["to_version"]
    with app.state.database.session_factory() as db:
        memo = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "household.memo.write"))
        write_tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "household.shopping.write"))
        installation = db.get(AgentInstallation, body["installation_id"])
        granted_ids = set(db.scalars(select(InstallationPermission.tool_id).where(
            InstallationPermission.installation_id == installation.id)).all())
        assert memo.id not in granted_ids
        assert write_tool.id in granted_ids  # 旧授权延续
        market_install = db.scalar(select(MarketInstall).where(
            MarketInstall.installation_id == installation.id))
        assert market_install.pinned_version_number == mine["update"]["to_version"]  # 旧 pin 释放

    # 升级后新版本生效
    content = _chat_and_read(client, installer, install["deployment_id"], "v2 加购")
    assert "鸡蛋已加入清单（第二版）" in content

    # 已是最新时再升级：稳定 409
    again = client.post(f"/api/market/installations/{body['installation_id']}/upgrade",
                        headers=installer, json={"granted_tool_keys": []})
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "UPDATE_NOT_AVAILABLE"


def test_takedown_blocks_new_installs_and_upgrades_but_existing_keep_running(client, app, monkeypatch):
    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)
    entry_id, _draft_id = _publish(client, app, household_id)

    installer, installer_account = _register(client, "market-takedown-user")
    installer_household = _household_of(app, installer_account)
    _configure_runtime(app, installer_household, monkeypatch)
    app.state.chat_model_builder = lambda resolved: None

    install = _install(client, installer, entry_id, granted=["household.shopping.write"]).json()
    assert client.post(f"/api/reviews/entries/{entry_id}/takedown", headers=_admin_headers(client),
                       json={"reason": "举报成立"}).status_code == 200

    late, _late_account = _register(client, "market-late-user")
    blocked = _install(client, late, entry_id)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "MARKET_ENTRY_DEPRECATED"

    # 已安装家庭继续运行（固定版本不受下架影响）
    content = _chat_and_read(client, installer, install["deployment_id"], "加鸡蛋")
    assert "鸡蛋已加入清单" in content

    # 下架后也不允许升级
    mine = client.get("/api/market/my", headers=installer).json()[0]
    assert mine["deprecated"] is True
    upgrade = client.post(f"/api/market/installations/{mine['installation_id']}/upgrade",
                          headers=installer, json={"granted_tool_keys": []})
    assert upgrade.status_code == 409
    assert upgrade.json()["detail"]["code"] == "MARKET_ENTRY_DEPRECATED"


def test_public_knowledge_is_revectorized_into_installer_household(client, app, settings, fake_vectors,  # noqa: F811
                                                                   monkeypatch):
    from backend.app.creator.knowledge import CreatorKnowledgeService
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    account_id, household_id = _home(app)
    _grant_market_ceiling(client, household_id)

    knowledge_card = json.loads(json.dumps(MARKET_CARD, ensure_ascii=False))
    knowledge_card["name"] = "市场知识助手"
    knowledge_card["skills"] = [{"name": "采购知识", "instructions": "根据采购知识回答。",
                                 "knowledge_asset_ids": []}]
    knowledge_card["tools"] = ["household.knowledge.search"]
    knowledge_card["tests"] = [{"name": "知识问答", "input": "鸡蛋怎么保存", "expected_contains": "采购知识"}]
    knowledge_card["workflow"] = None

    headers = _login(client)
    draft = client.post("/api/creator/drafts", headers=headers,
                        json={"title": "知识草稿", "description": "知识市场"}).json()
    service = CreatorKnowledgeService(app.state.database.session_factory(), household_id,
                                      upload_root=settings.knowledge_upload_path)
    content = "采购须知：鸡蛋常温保存两周。".encode("utf-8")
    asset = service.register_asset(draft["id"], "采购知识.txt", ".txt", content)
    knowledge_card["skills"][0]["knowledge_asset_ids"] = [asset.id]
    assert client.patch(f"/api/creator/drafts/{draft['id']}", headers=headers,
                        json={"intent_card": knowledge_card}).status_code == 200
    assert client.post(f"/api/creator/drafts/{draft['id']}/validate", headers=headers).status_code == 200

    # 沙盒试运行：deterministic 模型替换（无外部网络）
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class KnowledgeChatModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "deterministic-knowledge-chat-model"

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            self._visible = tuple(tool.name for tool in tools)
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
            if not tool_messages:
                assert "household.knowledge.search" in self._visible, self._visible
                call = {"name": "household.knowledge.search", "args": {"query": "鸡蛋保存"},
                        "id": "knowledge-call-1", "type": "tool_call"}
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content=f"根据采购知识：{tool_messages[-1].content}"))])

    from backend.app.llm_runtime.runtime_service import ModelRuntimeService

    monkeypatch.setattr(ModelRuntimeService, "build_chat_model", lambda self, resolved: KnowledgeChatModel())
    with app.state.database.session_factory() as db:
        configured_runtime(db, household_id)
        configured_runtime(db, household_id, purpose="embedding")
    trial = client.post(f"/api/creator/drafts/{draft['id']}/trial", headers=headers, json={"granted_tool_keys": []})
    assert trial.status_code == 200 and trial.json()["status"] in {"succeeded", "completed"}, trial.text
    assert all(case["status"] == "passed" for case in trial.json()["cases"]), trial.text

    body = _submit(client, draft["id"]).json()
    assert body["status"] == "review_pending", body
    assert client.post(f"/api/reviews/submissions/{body['submission_id']}/decision",
                       headers=_admin_headers(client),
                       json={"action": "approve", "reason": "知识通过"}).status_code == 200

    installer, installer_account = _register(client, "market-knowledge-user")
    installer_household = _household_of(app, installer_account)
    with app.state.database.session_factory() as db:
        # primary_chat 绑定是平台级：复用既有档案并给安装家庭授权与配额
        from backend.app.db.models import ModelGrant, ModelPurposeBinding, UsageQuota

        binding = db.get(ModelPurposeBinding, "primary_chat")
        if db.scalar(select(ModelGrant).where(ModelGrant.household_id == installer_household,
                                             ModelGrant.model_id == binding.model_id)) is None:
            db.add(ModelGrant(household_id=installer_household, model_id=binding.model_id))
        if db.scalar(select(UsageQuota).where(UsageQuota.household_id == installer_household)) is None:
            db.add(UsageQuota(household_id=installer_household, monthly_limit=100,
                              requests_per_minute=10, agent_token_budget=8))
        db.commit()

    class RetrievalModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "deterministic-market-retrieval-model"

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            self._visible = tuple(tool.name for tool in tools)
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
            if not tool_messages:
                assert "household.knowledge.search" in self._visible, self._visible
                call = {"name": "household.knowledge.search", "args": {"query": "鸡蛋保存"},
                        "id": "market-call-1", "type": "tool_call"}
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content=f"根据市场知识：{tool_messages[-1].content}"))])

    app.state.chat_model_builder = lambda resolved: RetrievalModel()
    with app.state.database.session_factory() as db:
        from backend.app.db.models import MarketEntry

        entry_id = db.scalar(select(MarketEntry.id).where(MarketEntry.name == knowledge_card["name"]))
    install = _install(client, installer, entry_id).json()

    content_after = _chat_and_read(client, installer, install["deployment_id"], "鸡蛋怎么保存")
    assert "鸡蛋常温保存两周" in content_after  # 安装家庭检索到复制后的知识
    doc = next(item for item in fake_vectors.documents
               if item.metadata.get("household_id") == installer_household)
    assert "鸡蛋常温保存两周" in doc.page_content
    # 原家庭知识不被跨家庭检索
    assert fake_vectors.search("鸡蛋保存", household_id=household_id) == []
