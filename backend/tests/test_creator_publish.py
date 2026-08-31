# -*- coding: utf-8 -*-
"""Phase 6 private publishing: immutable versions, private deployment,
household installation with explicit per-tool grants (reversible opt-in),
publish-time knowledge vectorization through the knowledge.vectorize task path,
and the Cantonese-chef E2E through the production deployment-bound chat path."""
import json

import pytest
from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import select

from backend.tests.test_celery_tasks import _home
from backend.tests.test_creator_sandbox import _grant_ceiling

CHEF_CARD = {
    "name": "广东菜大厨",
    "description": "提供几道广东菜做法",
    "prompt": "你是家庭粤菜大厨。回答菜谱问题前先检索家庭菜谱知识。",
    "examples": [{"user": "推荐一道粤菜", "assistant": "白切鸡是经典粤菜。"}],
    "model_requirements": {"capabilities": ["chat"]},
    "skills": [{"name": "粤菜菜谱", "instructions": "根据上传的菜谱知识回答家常粤菜做法。",
                "knowledge_asset_ids": []}],
    "tools": ["household.recipe.scale", "household.knowledge.search"],
    "budgets": {"tool_calls": 8, "tokens": 8000, "wall_time_seconds": 60, "delegation_depth": 0},
    "tests": [{"name": "菜谱问答", "input": "白切鸡怎么做", "expected_contains": "白切鸡"}],
    "workflow": None,
}


class InMemoryVectorStore:
    """家庭+技能版本隔离的内存向量库，模拟 Chroma 元数据过滤。"""

    def __init__(self):
        self.documents = []

    def add_file(self, file_id, path, filename, household_id=None, skill_version_id=None):
        # 与生产 VectorStoreService 一致：按 (source_file_id, skill_version_id) 作用域去重
        self.documents = [item for item in self.documents
                          if not (item.metadata.get("source_file_id") == str(file_id)
                                  and item.metadata.get("skill_version_id") == str(skill_version_id))]
        text = path.read_text(encoding="utf-8")
        self.documents.append(Document(page_content=text, metadata={
            "source_file_id": str(file_id), "source_type": "kb", "filename": filename,
            "household_id": str(household_id), "skill_version_id": str(skill_version_id)}))

    def delete_file(self, file_id):
        self.documents = [item for item in self.documents
                          if item.metadata.get("source_file_id") != str(file_id)]

    def has_file(self, file_id, skill_version_id=None):
        return any(item.metadata.get("source_file_id") == str(file_id)
                   and (skill_version_id is None
                        or item.metadata.get("skill_version_id") == str(skill_version_id))
                   for item in self.documents)

    def search(self, query, k=4, household_id=None, skill_version_ids=()):
        hits = [item for item in self.documents
                if item.metadata.get("household_id") == str(household_id)
                and (not skill_version_ids
                     or item.metadata.get("skill_version_id") in {str(value) for value in skill_version_ids})]
        return hits[:k]


@pytest.fixture
def fake_vectors(app, monkeypatch):
    store = InMemoryVectorStore()
    app.state.vector_store_service = store

    import backend.rag.vector_store as vector_store_module

    monkeypatch.setattr(vector_store_module, "configured_vector_store",
                        lambda session_factory, persist_directory: store)

    import backend.app.core.config as config_module

    monkeypatch.setattr(config_module, "get_settings", lambda: app.state.settings)
    yield store
    from backend.rag.vector_store import configured_vector_store

    app.state.vector_store_service = configured_vector_store(
        app.state.database.session_factory, app.state.settings.chroma_path)


def _chef_draft(app, household_id, account_id, card=CHEF_CARD, assets=(), settings=None, title=None):
    from backend.app.creator.knowledge import CreatorKnowledgeService
    from backend.app.db.models import CreatorDraft

    card = json.loads(json.dumps(card, ensure_ascii=False))
    with app.state.database.session_factory.begin() as db:
        draft = CreatorDraft(household_id=household_id, title=title or card["name"], status="validated",
                             intent_card_json=json.dumps(card, ensure_ascii=False),
                             created_by_account_id=account_id)
        db.add(draft)
        db.flush()
        draft_id = draft.id
    if assets:
        service = CreatorKnowledgeService(
            db_session(app), household_id, upload_root=settings.knowledge_upload_path)
        for filename, suffix, content in assets:
            card["skills"][0]["knowledge_asset_ids"].append(
                service.register_asset(draft_id, filename, suffix, content).id)
        with app.state.database.session_factory.begin() as db:
            draft = db.get(CreatorDraft, draft_id)
            draft.intent_card_json = json.dumps(card, ensure_ascii=False)
    return draft_id


def db_session(app):
    return app.state.database.session_factory()


def test_publish_creates_immutable_versions_deployment_and_optin_grants(client, app, settings):
    from backend.app.creator.publishing import PrivatePublishService, PublishRejected
    from backend.app.db.models import (AgentDeployment, AgentInstallation, AgentSkillBinding, AgentToolBinding,
                                       AgentVersion, CreatorDraft, InstallationPermission, SkillVersion)

    account_id, household_id = _home(app)
    draft_id = _chef_draft(app, household_id, account_id)
    service = PrivatePublishService(app.state.database.session_factory, household_id, account_id,
                                    settings=settings)
    result = service.publish(draft_id, granted_tool_keys=[])
    assert result["deployment"]["visibility"] == "private"

    with app.state.database.session_factory() as db:
        version = db.get(AgentVersion, result["agent_version_id"])
        assert version.published_at is not None
        assert db.scalar(select(AgentSkillBinding.agent_version_id).where(
            AgentSkillBinding.agent_version_id == version.id)) is not None
        tool_ids = set(db.scalars(select(AgentToolBinding.tool_id).where(
            AgentToolBinding.agent_version_id == version.id)).all())
        installation = db.get(AgentInstallation, result["installation_id"])
        assert installation.status == "installed" and installation.authority_slot == "active"
        granted = set(db.scalars(select(InstallationPermission.tool_id).where(
            InstallationPermission.installation_id == installation.id)).all())
        assert granted <= tool_ids
        assert len(granted) == len([key for key in CHEF_CARD["tools"]])  # 低风险自动授予

        draft = db.get(CreatorDraft, draft_id)
        assert draft.status == "private_published"
        assert draft.deployment_id == result["deployment"]["id"]

        # 重发布：草稿编辑后生成新版本，旧版本不可变
        old_definition = version.definition_json
        draft.status = "draft"
        card = json.loads(draft.intent_card_json)
        card["prompt"] = "你是家庭粤菜大厨，回答菜谱问题。"
        draft.intent_card_json = json.dumps(card, ensure_ascii=False)
        db.commit()
    service2 = PrivatePublishService(app.state.database.session_factory, household_id, account_id,
                                     settings=settings)
    result2 = service2.publish(draft_id, granted_tool_keys=[])
    assert result2["agent_version_id"] != result["agent_version_id"]
    with app.state.database.session_factory() as db:
        versions = db.scalars(select(AgentVersion).where(
            AgentVersion.agent_id == result2["agent_id"])).all()
        assert len(versions) == 2
        assert db.get(AgentVersion, result["agent_version_id"]).definition_json == old_definition
        deployment = db.get(AgentDeployment, result2["deployment"]["id"])
        assert deployment.id == result["deployment"]["id"]  # deployment 指针移动
        assert deployment.agent_version_id == result2["agent_version_id"]
        assert deployment.revision >= 2
        installation = db.get(AgentInstallation, result2["installation_id"])
        assert installation.status == "installed" and installation.authority_slot == "active"

    with pytest.raises(PublishRejected) as exc:
        service2.publish("no-such-draft", granted_tool_keys=[])
    assert exc.value.code == "DRAFT_NOT_FOUND"


def test_publish_reversible_grants_require_explicit_confirmation(client, app, settings):
    from backend.app.creator.publishing import PrivatePublishService, PublishRejected
    from backend.app.db.models import InstallationPermission, ToolDefinition

    account_id, household_id = _home(app)
    _grant_ceiling(client, household_id, "household.shopping.write")
    card = json.loads(json.dumps(CHEF_CARD, ensure_ascii=False))
    card["tools"] = ["household.knowledge.search", "household.shopping.write"]
    draft_id = _chef_draft(app, household_id, account_id, card=card)
    service = PrivatePublishService(app.state.database.session_factory, household_id, account_id,
                                    settings=settings)
    result = service.publish(draft_id, granted_tool_keys=[])
    with app.state.database.session_factory() as db:
        write_tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "household.shopping.write"))
        granted = set(db.scalars(select(InstallationPermission.tool_id).where(
            InstallationPermission.installation_id == result["installation_id"])).all())
        assert write_tool.id not in granted  # 可逆写必须 opt-in

    draft_id2 = _chef_draft(app, household_id, account_id, card=card, title="菜谱2")
    result2 = service.publish(draft_id2, granted_tool_keys=["household.shopping.write"])
    with app.state.database.session_factory() as db:
        granted2 = set(db.scalars(select(InstallationPermission.tool_id).where(
            InstallationPermission.installation_id == result2["installation_id"])).all())
        assert write_tool.id in granted2

    draft_id3 = _chef_draft(app, household_id, account_id, card=card, title="菜谱3")
    with pytest.raises(PublishRejected) as exc:  # 确认清单外的工具拒绝
        service.publish(draft_id3, granted_tool_keys=["no.such.tool"])
    assert exc.value.code in {"TOOL_NOT_IN_CATALOG", "TOOL_NOT_GRANTED"}


def test_publish_vectorizes_knowledge_through_task_path_with_isolation(client, app, settings, fake_vectors, monkeypatch):
    from backend.app.creator.publishing import PrivatePublishService
    from backend.app.db.models import CeleryTaskRecord
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    account_id, household_id = _home(app)
    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    with app.state.database.session_factory() as db:
        configured_runtime(db, household_id, purpose="embedding")
    recipe = "白切鸡：整鸡温水浸煮25分钟，过冰水，配姜葱蘸料。".encode("utf-8")
    draft_id = _chef_draft(app, household_id, account_id, assets=[("粤菜菜谱.txt", ".txt", recipe)],
                           settings=settings)
    service = PrivatePublishService(app.state.database.session_factory, household_id, account_id,
                                    settings=settings)
    result = service.publish(draft_id, granted_tool_keys=[])
    assert result["vectorized_assets"]
    with app.state.database.session_factory() as db:
        records = db.scalars(select(CeleryTaskRecord).where(
            CeleryTaskRecord.task_type == "knowledge.vectorize")).all()
        assert any(row.status == "succeeded" for row in records)
    assert fake_vectors.has_file(result["vectorized_assets"][0]["asset_id"])
    doc = fake_vectors.documents[0]
    assert doc.metadata["household_id"] == household_id
    assert doc.metadata["skill_version_id"] == result["skills"][0]["skill_version_id"]
    # 隔离：其他家庭或官方技能版本检索不到
    assert fake_vectors.search("白切鸡", household_id="other-household") == []
    assert fake_vectors.search("白切鸡", household_id=household_id, skill_version_ids=["official-sv"]) == []


def test_publish_without_embedding_records_stable_degradation_without_vector_backend(client, app, settings, monkeypatch):
    from backend.app.creator.publishing import PrivatePublishService
    import backend.rag.vector_store as vector_store_module

    account_id, household_id = _home(app)
    recipe = "白切鸡：整鸡温水浸煮25分钟。".encode("utf-8")
    draft_id = _chef_draft(app, household_id, account_id, assets=[("粤菜菜谱.txt", ".txt", recipe)],
                           settings=settings)
    monkeypatch.setattr(vector_store_module, "configured_vector_store",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("embedding 未配置时不得创建 vector store")))

    result = PrivatePublishService(app.state.database.session_factory, household_id, account_id,
                                   settings=settings).publish(draft_id, granted_tool_keys=[])

    assert result["vectorized_assets"] == [{
        "asset_id": result["vectorized_assets"][0]["asset_id"],
        "skill_version_id": result["skills"][0]["skill_version_id"],
        "status": "dead",
        "error_code": "EMBEDDING_NOT_CONFIGURED",
    }]


def test_cantonese_chef_e2e_publish_then_chat_answers_from_isolated_knowledge(client, app, settings,
                                                                              monkeypatch, fake_vectors):
    from backend.app.creator.publishing import PrivatePublishService
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    account_id, household_id = _home(app)
    with app.state.database.session_factory() as db:
        configured_runtime(db, household_id)
        configured_runtime(db, household_id, purpose="embedding")

    class ChefChatModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "deterministic-chef-chat-model"

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            self._visible = tuple(tool.name for tool in tools)
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
            if not tool_messages:
                assert "household.knowledge.search" in self._visible, self._visible
                call = {"name": "household.knowledge.search", "args": {"query": "白切鸡做法"},
                        "id": "chef-call-1", "type": "tool_call"}
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])
            answer = f"根据家庭菜谱知识：{tool_messages[-1].content}"
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=answer))])

    app.state.chat_model_builder = lambda resolved: ChefChatModel()
    recipe = "白切鸡：整鸡温水浸煮25分钟，过冰水，配姜葱蘸料。".encode("utf-8")
    draft_id = _chef_draft(app, household_id, account_id, assets=[("粤菜菜谱.txt", ".txt", recipe)],
                           settings=settings)
    service = PrivatePublishService(app.state.database.session_factory, household_id, account_id,
                                    settings=settings)
    result = service.publish(draft_id, granted_tool_keys=[])

    token = client.post("/api/auth/login", json={"username": "demo", "password": "DemoTest@123"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    session = client.post("/api/chat/sessions", headers=headers,
                          json={"title": "粤菜", "deployment_id": result["deployment"]["id"]})
    assert session.status_code == 201, session.text
    session_id = session.json()["id"]
    assert session.json()["deployment_id"] == result["deployment"]["id"]

    with client.stream("POST", f"/api/chat/sessions/{session_id}/chat", headers=headers,
                       json={"message": "白切鸡怎么做"}) as response:
        assert response.status_code == 200
        events = []
        for line in response.iter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
        content = _read_stream_content(client, session_id, headers)
    assert "token" in events
    assert "白切鸡" in content and "浸煮" in content
    assert "run" in events


def _runtime_configured(app, household_id, monkeypatch):
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    with app.state.database.session_factory() as db:
        configured_runtime(db, household_id)
        configured_runtime(db, household_id, purpose="embedding")


class RetrievalChatModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "deterministic-retrieval-chat-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self._visible = tuple(tool.name for tool in tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
        if not tool_messages:
            assert "household.knowledge.search" in self._visible, self._visible
            call = {"name": "household.knowledge.search", "args": {"query": "白切鸡做法"},
                    "id": "retrieval-call-1", "type": "tool_call"}
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=f"根据家庭菜谱知识：{tool_messages[-1].content}"))])


def test_republish_revectorizes_under_new_skill_version_and_v2_chat_retrieves(client, app, settings,
                                                                              monkeypatch, fake_vectors):
    """Fix Round 1 Finding 1: 同一资产在重发布后必须挂到新技能版本的元数据下。"""
    from backend.app.creator.publishing import PrivatePublishService
    from backend.app.db.models import CreatorDraft

    account_id, household_id = _home(app)
    _runtime_configured(app, household_id, monkeypatch)
    app.state.chat_model_builder = lambda resolved: RetrievalChatModel()

    recipe = "白切鸡：整鸡温水浸煮25分钟，过冰水，配姜葱蘸料。".encode("utf-8")
    draft_id = _chef_draft(app, household_id, account_id, assets=[("粤菜菜谱.txt", ".txt", recipe)],
                           settings=settings)
    service = PrivatePublishService(app.state.database.session_factory, household_id, account_id,
                                    settings=settings)
    v1 = service.publish(draft_id, granted_tool_keys=[])
    sv1 = v1["skills"][0]["skill_version_id"]
    assert fake_vectors.search("白切鸡", household_id=household_id, skill_version_ids=[sv1])

    # 编辑草稿后重发布：新版本、部署指针移动
    with app.state.database.session_factory.begin() as db:
        draft = db.get(CreatorDraft, draft_id)
        draft.status = "draft"
        card = json.loads(draft.intent_card_json)
        card["prompt"] = "你是家庭粤菜大厨，先检索菜谱知识再回答。"
        draft.intent_card_json = json.dumps(card, ensure_ascii=False)
    v2 = service.publish(draft_id, granted_tool_keys=[])
    sv2 = v2["skills"][0]["skill_version_id"]
    assert sv2 != sv1

    # 新技能版本可检索（回归点：has_file 曾只按 source_file_id 跳过）
    assert fake_vectors.has_file(v1["vectorized_assets"][0]["asset_id"], skill_version_id=sv2)
    assert fake_vectors.search("白切鸡", household_id=household_id, skill_version_ids=[sv2])
    # 旧版本副本与隔离语义不受影响
    assert fake_vectors.search("白切鸡", household_id=household_id, skill_version_ids=[sv1])
    assert fake_vectors.search("白切鸡", household_id="other-household", skill_version_ids=[sv2]) == []

    # v2 部署绑定聊天取回知识内容（非空）
    token = client.post("/api/auth/login", json={"username": "demo", "password": "DemoTest@123"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    session = client.post("/api/chat/sessions", headers=headers,
                          json={"title": "粤菜v2", "deployment_id": v2["deployment"]["id"]})
    assert session.status_code == 201, session.text
    session_id = session.json()["id"]
    with client.stream("POST", f"/api/chat/sessions/{session_id}/chat", headers=headers,
                       json={"message": "白切鸡怎么做"}) as response:
        assert response.status_code == 200
    content = _read_stream_content(client, session_id, headers)
    assert "浸煮" in content, content


def test_one_asset_attached_to_two_skills_is_retrievable_under_both_versions(client, app, settings, fake_vectors,
                                                                              monkeypatch):
    """Fix Round 1 Finding 1（单发布多技能）：同一资产挂两个 Skill 时两个版本都可检索。"""
    from backend.app.creator.knowledge import CreatorKnowledgeService
    from backend.app.creator.publishing import PrivatePublishService
    from backend.app.db.models import CreatorDraft

    account_id, household_id = _home(app)
    from backend.tests.test_model_runtime_fix_round1 import MASTER_KEY, configured_runtime

    monkeypatch.setenv("LLM_CREDENTIAL_MASTER_KEY", MASTER_KEY)
    with app.state.database.session_factory() as db:
        configured_runtime(db, household_id, purpose="embedding")
    recipe = "白切鸡：整鸡温水浸煮25分钟，过冰水，配姜葱蘸料。".encode("utf-8")
    with app.state.database.session_factory.begin() as db:
        draft = CreatorDraft(household_id=household_id, title="双技能菜谱", status="validated",
                             created_by_account_id=account_id)
        db.add(draft)
        db.flush()
        draft_id = draft.id
    service = CreatorKnowledgeService(db_session(app), household_id,
                                      upload_root=settings.knowledge_upload_path)
    asset = service.register_asset(draft_id, "粤菜菜谱.txt", ".txt", recipe)
    card = json.loads(json.dumps(CHEF_CARD, ensure_ascii=False))
    card["skills"] = [
        {"name": "粤菜菜谱", "instructions": "根据菜谱知识回答做法。", "knowledge_asset_ids": [asset.id]},
        {"name": "采购提示", "instructions": "根据菜谱给出采购建议。", "knowledge_asset_ids": [asset.id]},
    ]
    with app.state.database.session_factory.begin() as db:
        draft = db.get(CreatorDraft, draft_id)
        draft.intent_card_json = json.dumps(card, ensure_ascii=False)
    publisher = PrivatePublishService(app.state.database.session_factory, household_id, account_id,
                                      settings=settings)
    result = publisher.publish(draft_id, granted_tool_keys=[])
    sv_a, sv_b = (item["skill_version_id"] for item in result["skills"])
    assert sv_a != sv_b
    assert fake_vectors.search("白切鸡", household_id=household_id, skill_version_ids=[sv_a])
    assert fake_vectors.search("白切鸡", household_id=household_id, skill_version_ids=[sv_b])


def _read_stream_content(client, session_id, headers):
    messages = client.get(f"/api/chat/sessions/{session_id}/messages", headers=headers).json()
    return messages[-1]["content"] if messages else ""
