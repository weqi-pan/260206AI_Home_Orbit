import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import func, select


class DeterministicToolModel(BaseChatModel):
    tool_name: str
    arguments: dict[str, Any]

    @property
    def _llm_type(self):
        return "deterministic-official-device-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self._visible_tools = tuple(tool.name for tool in tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
        if not tool_messages:
            assert self.tool_name in self._visible_tools
            call = {"name": self.tool_name, "args": self.arguments, "id": "official-call-1", "type": "tool_call"}
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=str(tool_messages[-1].content)))])


def _official_context(app, account_id, model):
    from backend.agent.platform.context import RunBudget, RunContext
    from backend.agent.platform.snapshots import CompiledSnapshotService
    from backend.agent.tools.gateways import ToolRepositories
    from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentInstallation, AgentRun, ChatSession,
                                       HouseholdMember)

    with app.state.database.session_factory.begin() as db:
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == account_id))
        agent = db.scalar(select(AgentDefinition).where(
            AgentDefinition.household_id == member.household_id, AgentDefinition.name == "设备管家"))
        deployment = db.scalar(select(AgentDeployment).where(
            AgentDeployment.household_id == member.household_id,
            AgentDeployment.agent_version_id.in_(select(__import__("backend.app.db.models", fromlist=["AgentVersion"]).AgentVersion.id).where(
                __import__("backend.app.db.models", fromlist=["AgentVersion"]).AgentVersion.agent_id == agent.id))))
        installation = db.scalar(select(AgentInstallation).where(
            AgentInstallation.household_id == member.household_id,
            AgentInstallation.deployment_id == deployment.id,
            AgentInstallation.authority_slot == "active"))
        if model.tool_name in {"device.restock.register", "device.ticket.create"}:
            from backend.app.db.models import InstallationPermission, ToolDefinition
            tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == model.tool_name))
            db.add(InstallationPermission(household_id=member.household_id, installation_id=installation.id,
                tool_id=tool.id, tool_version=tool.version, granted_by_account_id=account_id))
        chat = ChatSession(account_id=account_id, household_id=member.household_id,
                           deployment_id=deployment.id, title="设备管家测试")
        db.add(chat); db.flush()
        provisional = RunContext(principal_id=account_id, household_id=member.household_id, member_id=member.id,
            role=member.role, session_id=chat.id, deployment_id=deployment.id,
            agent_version_id=installation.agent_version_id, run_id="pending", timezone="Asia/Shanghai",
            grants={}, consent={}, deployment_revision=deployment.revision, model_id="deterministic",
            model_snapshot={"id": "deterministic", "revision": 1})
        snapshot = CompiledSnapshotService(db).capture(deployment.id, installation.id, provisional)
        run = AgentRun(household_id=member.household_id, deployment_id=deployment.id, principal_id=account_id,
            member_id=member.id, session_id=chat.id, agent_version_id=installation.agent_version_id,
            deployment_revision=deployment.revision, model_id="deterministic", model_revision=1,
            compiled_snapshot_json=json.dumps(snapshot), model_snapshot_json='{"id":"deterministic","revision":1}',
            permission_snapshot_json=json.dumps(snapshot["grants"]), consent_snapshot_json="{}",
            budget_snapshot_json=json.dumps(snapshot["budget"]))
        db.add(run); db.flush()
        context = RunContext(principal_id=account_id, household_id=member.household_id, member_id=member.id,
            role=member.role, session_id=chat.id, deployment_id=deployment.id,
            agent_version_id=installation.agent_version_id, run_id=run.id, timezone="Asia/Shanghai",
            grants={item["key"]: item for item in snapshot["grants"]}, consent={},
            deployment_revision=deployment.revision, model_id="deterministic", model_snapshot={"id": "deterministic", "revision": 1},
            compiled_snapshot=snapshot, skill_version_ids=tuple(item["id"] for item in snapshot["skills"]),
            repositories=ToolRepositories(app.state.database.session_factory), vector_store_service=app.state.vector_store_service,
            user_id=db.get(__import__("backend.app.db.models", fromlist=["Account"]).Account, account_id).user_id,
            budget=RunBudget(max_tool_calls=8, max_tokens=1000, wall_time_seconds=30))
    app.state.deployment_agent_runner.bind_model(context.run_id, model)
    return context


def test_official_device_seed_is_idempotent_versioned_and_least_privilege(client, app):
    from backend.app.db.seed import seed_database
    from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentInstallation, AgentSkillBinding,
                                       AgentToolBinding, AgentVersion, InstallationPermission, Skill, ToolDefinition)

    seed_database(app.state.database.session_factory, app.state.settings)
    with app.state.database.session_factory() as db:
        agent = db.scalar(select(AgentDefinition).where(AgentDefinition.name == "设备管家"))
        version = db.scalar(select(AgentVersion).where(AgentVersion.agent_id == agent.id))
        deployment = db.scalar(select(AgentDeployment).where(AgentDeployment.agent_version_id == version.id))
        assert version.version == 1
        assert db.scalar(select(func.count()).select_from(AgentDefinition).where(
            AgentDefinition.household_id == agent.household_id, AgentDefinition.name == "设备管家")) == 1
        assert db.scalar(select(func.count()).select_from(Skill).where(
            Skill.household_id == agent.household_id, Skill.name.in_(
                ("设备诊断", "设备选购", "库存与使用报告", "售后升级")))) == 4
        assert db.scalar(select(func.count()).select_from(AgentSkillBinding).where(
            AgentSkillBinding.agent_version_id == version.id)) == 4
        assert db.scalar(select(func.count()).select_from(AgentToolBinding).where(
            AgentToolBinding.agent_version_id == version.id)) == 9
        installation = db.scalar(select(AgentInstallation).where(
            AgentInstallation.household_id == agent.household_id,
            AgentInstallation.deployment_id == deployment.id))
        granted = set(db.scalars(select(ToolDefinition.key).join(InstallationPermission,
            InstallationPermission.tool_id == ToolDefinition.id).where(
                InstallationPermission.installation_id == installation.id)).all())
        assert "device.ticket.create" not in granted and "device.restock.register" not in granted
        assert all(db.scalar(select(ToolDefinition.risk_level).where(ToolDefinition.key == key)) == "low" for key in granted)


@pytest.mark.parametrize(("tool_name", "arguments", "expected"), [
    ("device.compare", {"model_ids": ["R1-Lite", "R1-Pro"]}, "|型号|名称|价格|规格|特点|"),
    ("device.knowledge.search", {"query": "保修"}, "保修"),
    ("device.usage.report", {"month": "2026-05"}, "用户ID:1001"),
    ("device.restock.register", {"model_id": "R1-Pro"}, "R1-Pro"),
    ("device.ticket.create", {"title": "无法启动", "content": "设备无法启动"}, "无法启动"),
])
def test_official_device_production_chains_use_compiler_permissions_context_audit(
        client, app, tool_name, arguments, expected):
    from backend.agent.platform.runner import DeploymentAgentRunner
    from backend.app.db.models import Account, ToolExecution

    with app.state.database.session_factory() as db:
        account_id = db.scalar(select(Account.id).where(Account.username == "demo"))
    context = _official_context(app, account_id, DeterministicToolModel(tool_name=tool_name, arguments=arguments))
    from backend.agent.platform.snapshots import SnapshotCompiler
    with app.state.database.session_factory() as db:
        SnapshotCompiler(db).validate_and_load(context.compiled_snapshot, context)
    events = list(app.state.deployment_agent_runner.stream([{"role": "user", "content": "执行"}], context))
    assert isinstance(app.state.deployment_agent_runner, DeploymentAgentRunner)
    assert expected in "".join(event.get("data", {}).get("content", "") for event in events), events
    with app.state.database.session_factory() as db:
        audit = db.scalar(select(ToolExecution).where(ToolExecution.run_id == context.run_id,
                                                       ToolExecution.status == "success"))
        assert audit and audit.household_id == context.household_id


def test_device_repository_isolates_every_resource_by_household(client, app):
    from backend.agent.tools.gateways import ToolRepositories
    from backend.app.db.models import Account, Device, HouseholdMember

    with app.state.database.session_factory.begin() as db:
        demo = db.scalar(select(Account).where(Account.username == "demo"))
        admin = db.scalar(select(Account).where(Account.username == "admin"))
        demo_home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        admin_home = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == admin.id))
        db.add(Device(household_id=admin_home, model_id="PRIVATE", model_name="私有设备", category="测试",
                      specs="private", description="private", price=1, features="private"))
    repositories = ToolRepositories(app.state.database.session_factory)
    assert repositories.device(demo_home, "PRIVATE") is None
    assert repositories.search_devices(demo_home, "私有") == []
    assert repositories.inventory(demo_home, "PRIVATE") == []
    assert repositories.usage(demo_home, "1001", "2026-05").household_id == demo_home
    assert all(row.household_id == demo_home for row in repositories.approved_faq(demo_home, "保修"))
