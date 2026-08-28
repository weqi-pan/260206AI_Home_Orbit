"""Phase 5 end-to-end household agent chains.

All chains use the production AgentCompiler/DeploymentAgentRunner path with
deterministic models and no external network.
"""
import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import select

from backend.tests.test_official_device_agent import DeterministicToolModel  # noqa: F401


class HouseholdChainModel(BaseChatModel):
    tool_name: str
    arguments: dict[str, Any]

    @property
    def _llm_type(self):
        return "deterministic-household-chain-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self._visible_tools = tuple(tool.name for tool in tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
        if not tool_messages:
            assert self.tool_name in self._visible_tools, (
                f"{self.tool_name} not granted; visible={self._visible_tools}")
            call = {"name": self.tool_name, "args": self.arguments, "id": "household-call-1", "type": "tool_call"}
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=str(tool_messages[-1].content)))])


class StewardChainModel(BaseChatModel):
    """家庭总管：先委派子代理，再复述子代理结果；子代理身份下调用家庭工具。"""

    child_name: str
    child_tool: str
    child_arguments: dict[str, Any]

    @property
    def _llm_type(self):
        return "deterministic-steward-chain-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        _STEWARD_BOUND_TOOLS_LOG.append(tuple(tool.name for tool in tools))
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
        if not tool_messages:
            if any(self.child_tool in bound for bound in _STEWARD_BOUND_TOOLS_LOG[-1:]):
                call = {"name": self.child_tool, "args": self.child_arguments, "id": "child-tool-1",
                        "type": "tool_call"}
            else:
                call = {"name": "task", "args": {"description": "请处理家庭事务", "subagent_type": self.child_name},
                        "id": "steward-delegate-1", "type": "tool_call"}
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=str(tool_messages[-1].content)))])


_STEWARD_BOUND_TOOLS_LOG: list = []


def _household_context(app, account_id, agent_name, model, grant_tools=()):
    from backend.agent.platform.context import RunBudget, RunContext
    from backend.agent.platform.snapshots import CompiledSnapshotService
    from backend.agent.tools.household_gateways import HouseholdToolRepositories
    from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentInstallation, AgentRun, AgentVersion,
                                       ChatSession, HouseholdMember, InstallationPermission, ToolDefinition)

    with app.state.database.session_factory.begin() as db:
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == account_id))
        agent = db.scalar(select(AgentDefinition).where(
            AgentDefinition.household_id == member.household_id, AgentDefinition.name == agent_name))
        version = db.scalar(select(AgentVersion).where(AgentVersion.agent_id == agent.id))
        deployment = db.scalar(select(AgentDeployment).where(
            AgentDeployment.household_id == member.household_id, AgentDeployment.agent_version_id == version.id))
        installation = db.scalar(select(AgentInstallation).where(
            AgentInstallation.household_id == member.household_id, AgentInstallation.deployment_id == deployment.id,
            AgentInstallation.authority_slot == "active"))
        for tool_key in grant_tools:
            tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == tool_key))
            if not db.scalar(select(InstallationPermission).where(
                    InstallationPermission.installation_id == installation.id,
                    InstallationPermission.tool_id == tool.id)):
                db.add(InstallationPermission(household_id=member.household_id, installation_id=installation.id,
                    tool_id=tool.id, tool_version=tool.version, granted_by_account_id=account_id))
        chat = ChatSession(account_id=account_id, household_id=member.household_id,
                           deployment_id=deployment.id, title=f"{agent_name}链路测试")
        db.add(chat)
        db.flush()
        provisional = RunContext(principal_id=account_id, household_id=member.household_id, member_id=member.id,
            role=member.role, session_id=chat.id, deployment_id=deployment.id, agent_version_id=version.id,
            run_id="pending", timezone="Asia/Shanghai", grants={}, consent={}, deployment_revision=deployment.revision,
            model_id="deterministic", model_snapshot={"id": "deterministic", "revision": 1})
        snapshot = CompiledSnapshotService(db).capture(deployment.id, installation.id, provisional)
        run = AgentRun(household_id=member.household_id, deployment_id=deployment.id, principal_id=account_id,
            member_id=member.id, session_id=chat.id, agent_version_id=version.id, deployment_revision=deployment.revision,
            model_id="deterministic", model_revision=1, compiled_snapshot_json=json.dumps(snapshot),
            model_snapshot_json='{"id":"deterministic","revision":1}',
            permission_snapshot_json=json.dumps(snapshot["grants"]), consent_snapshot_json="{}",
            budget_snapshot_json=json.dumps(snapshot["budget"]))
        db.add(run)
        db.flush()
        context = RunContext(principal_id=account_id, household_id=member.household_id, member_id=member.id,
            role=member.role, session_id=chat.id, deployment_id=deployment.id, agent_version_id=version.id,
            run_id=run.id, timezone="Asia/Shanghai",
            grants={item["key"]: item for item in snapshot["grants"]}, consent={},
            deployment_revision=deployment.revision, model_id="deterministic",
            model_snapshot={"id": "deterministic", "revision": 1}, compiled_snapshot=snapshot,
            skill_version_ids=tuple(item["id"] for item in snapshot["skills"]),
            repositories=HouseholdToolRepositories(app.state.database.session_factory),
            vector_store_service=getattr(app.state, "vector_store_service", None),
            user_id=db.scalar(select(__import__("backend.app.db.models", fromlist=["Account"]).Account.user_id).where(
                __import__("backend.app.db.models", fromlist=["Account"]).Account.id == account_id)),
            budget=RunBudget(max_tool_calls=8, max_tokens=2000, wall_time_seconds=30,
                             max_delegation_depth=2))
    app.state.deployment_agent_runner.bind_model(context.run_id, model)
    return context


def _demo_account_id(app):
    from backend.app.db.models import Account

    with app.state.database.session_factory() as db:
        return db.scalar(select(Account.id).where(Account.username == "demo"))


def _stream(app, context, message="执行"):
    return list(app.state.deployment_agent_runner.stream([{"role": "user", "content": message}], context))


def _content(events):
    return "".join(event.get("data", {}).get("content", "") for event in events if isinstance(event.get("data"), dict))


def test_chef_chain_scales_recipe_and_writes_idempotent_audited_shopping_list(client, app):
    from backend.app.db.models import ShoppingListItem, ToolExecution

    account_id = _demo_account_id(app)
    arguments = {"items": [{"name": "鸡蛋", "quantity": 10, "unit": "个"},
                           {"name": "番茄", "quantity": 5, "unit": "个"}]}
    context = _household_context(app, account_id, "家庭厨师",
        HouseholdChainModel(tool_name="household.shopping.write", arguments=arguments),
        grant_tools=("household.shopping.write",))
    events = _stream(app, context)
    assert "鸡蛋" in _content(events), events
    with app.state.database.session_factory() as db:
        items = db.scalars(select(ShoppingListItem).where(
            ShoppingListItem.household_id == context.household_id)).all()
        assert {item.name: item.quantity for item in items} == {"鸡蛋": 10, "番茄": 5}
        audit = db.scalars(select(ToolExecution).where(ToolExecution.run_id == context.run_id,
                                                       ToolExecution.status == "success")).all()
        assert any(row.idempotency_key and row.household_id == context.household_id for row in audit)
    second = _household_context(app, account_id, "家庭厨师",
        HouseholdChainModel(tool_name="household.shopping.write", arguments=arguments),
        grant_tools=("household.shopping.write",))
    _stream(app, second)
    with app.state.database.session_factory() as db:
        items = db.scalars(select(ShoppingListItem).where(
            ShoppingListItem.household_id == context.household_id)).all()
        assert {item.name: item.quantity for item in items} == {"鸡蛋": 10, "番茄": 5}  # 幂等


def test_chef_write_requires_explicit_authorization(client, app):
    from backend.agent.platform.policy import PlatformToolExecutor
    from backend.agent.platform.policy_errors import PlatformToolError

    account_id = _demo_account_id(app)
    context = _household_context(app, account_id, "家庭厨师",
        HouseholdChainModel(tool_name="household.shopping.write", arguments={"items": []}))
    with app.state.database.session_factory() as db:
        executor = PlatformToolExecutor(db)
        with pytest.raises(PlatformToolError) as denied:
            executor.execute("household.shopping.write", {"items": [{"name": "x", "quantity": 1}]}, context, "invoke")
        assert denied.value.code == "PERMISSION_REQUIRED"


def test_planner_chain_creates_periodicReminder_and_manual_beat_dispatch_is_idempotent(client, app):
    from backend.app.db.base import utcnow
    from backend.app.db.models import Notification, TaskSchedule
    from backend.app.services.household_daily import ReminderScheduleService
    from datetime import timedelta

    account_id = _demo_account_id(app)
    context = _household_context(app, account_id, "家庭计划员",
        HouseholdChainModel(tool_name="household.reminder.create",
                            arguments={"task_title": "每日垃圾分类", "interval_minutes": 60}),
        grant_tools=("household.reminder.create",))
    events = _stream(app, context)
    assert "每日垃圾分类" in _content(events), events
    now = utcnow()
    with app.state.database.session_factory.begin() as db:
        schedule = db.scalar(select(TaskSchedule).where(TaskSchedule.household_id == context.household_id))
        schedule.next_run_at = now - timedelta(minutes=30)
    for _ in range(2):
        with app.state.database.session_factory.begin() as db:
            ReminderScheduleService(db).dispatch_due(context.household_id, now=now)
    with app.state.database.session_factory() as db:
        notifications = db.scalars(select(Notification).where(
            Notification.household_id == context.household_id, Notification.kind == "reminder")).all()
        assert len(notifications) == 1


def test_planner_chain_proposes_preference_and_only_confirmation_persists(client, app):
    from backend.app.db.models import HouseholdPreference, HouseholdPreferenceProposal, InstallationPermission, ToolDefinition

    account_id = _demo_account_id(app)
    context = _household_context(app, account_id, "家庭计划员",
        HouseholdChainModel(tool_name="household.preference.propose",
                            arguments={"key": "diet.spice_level", "value": "微辣", "value_type": "string",
                                       "category": "饮食", "reason": "建议偏好"}),
        grant_tools=("household.preference.propose",))
    events = _stream(app, context)
    assert "diet.spice_level" in _content(events), events
    with app.state.database.session_factory() as db:
        assert db.scalar(select(HouseholdPreference).where(
            HouseholdPreference.household_id == context.household_id)) is None  # 仅建议未落库
        proposal = db.scalar(select(HouseholdPreferenceProposal).where(
            HouseholdPreferenceProposal.household_id == context.household_id,
            HouseholdPreferenceProposal.status == "pending"))
        assert proposal is not None
    token = client.post("/api/auth/login", json={"username": "demo", "password": "DemoTest@123"}).json()["access_token"]
    response = client.post(f"/api/home/preferences/proposals/{proposal.id}/confirm",
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    with app.state.database.session_factory() as db:
        row = db.scalar(select(HouseholdPreference).where(
            HouseholdPreference.household_id == context.household_id))
        assert row.value == "微辣" and row.category == "饮食"
        assert row.confirmed_by_account_id == account_id


def test_encyclopedia_answers_from_household_isolated_vectors_only(client, app):
    from langchain_core.documents import Document

    class ScopedVectorStore:
        def search(self, query, k=4, household_id=None, skill_version_ids=()):
            corpus = {context.household_id: [Document(page_content="家庭隔离知识：地暖首选 60℃ 以下水温")]}
            return corpus.get(household_id, [])

    account_id = _demo_account_id(app)
    app.state.vector_store_service = ScopedVectorStore()
    context = _household_context(app, account_id, "生活百科",
        HouseholdChainModel(tool_name="household.knowledge.search", arguments={"query": "地暖水温"}))
    try:
        events = _stream(app, context)
        content = _content(events)
        assert "60℃" in content, events
    finally:
        from backend.rag.vector_store import configured_vector_store
        app.state.vector_store_service = configured_vector_store(
            app.state.database.session_factory, app.state.settings.chroma_path)


def test_steward_delegates_to_child_with_child_only_grants(client, app):
    from backend.app.db.models import Account

    account_id = _demo_account_id(app)
    model = StewardChainModel(child_name="家庭厨师", child_tool="household.recipe.scale",
                              child_arguments={"servings_base": 2, "servings_target": 4,
                                               "ingredients": [{"name": "鸡蛋", "amount": 4, "unit": "个"}]})
    _STEWARD_BOUND_TOOLS_LOG.clear()
    context = _household_context(app, account_id, "家庭总管", model)
    events = _stream(app, context, message="给四个人做两人份的菜")
    content = _content(events)
    assert any(event.get("event") == "delegate" for event in events), events
    assert "8" in content, events  # 4*4/2
    child_bindings = [bound for bound in _STEWARD_BOUND_TOOLS_LOG if "household.recipe.scale" in bound]
    assert child_bindings, _STEWARD_BOUND_TOOLS_LOG
    child_bound = child_bindings[-1]
    assert "household.shopping.write" not in child_bound  # 子代理不可见未授权写工具
    assert "device.search" not in child_bound  # 子代理不继承其他孩子授权
