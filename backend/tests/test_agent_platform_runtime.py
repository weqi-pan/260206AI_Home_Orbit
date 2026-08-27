from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest
from sqlalchemy import select
from backend.app.db.base import utcnow


@pytest.fixture
def db_session(tmp_path):
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from backend.app.db.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'platform.db'}")
    event.listen(engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def platform_seed(db_session):
    from backend.agent.platform.context import RunContext
    from backend.app.core.security import hash_password
    from backend.app.db.models import (
        Account, AgentDefinition, AgentDeployment, AgentRun, AgentToolBinding, AgentVersion,
        Household, HouseholdMember, ToolDefinition, ToolPermission,
    )

    account = Account(username="platform-owner", password_hash=hash_password("Passw0rd!", rounds=4), role="user")
    db_session.add(account); db_session.flush()
    household = Household(name="Platform Home", created_by_account_id=account.id)
    db_session.add(household); db_session.flush()
    member = HouseholdMember(household_id=household.id, account_id=account.id, role="owner")
    db_session.add(member); db_session.flush()
    tool = ToolDefinition(key="platform.current_month", display_name="当前月份", description="读取当前月份",
                          risk_level="low", version="1", enabled=True)
    high_tool = ToolDefinition(key="platform.test_high_risk", display_name="高风险测试", description="测试",
                               risk_level="high", version="1", enabled=True)
    db_session.add_all([tool, high_tool]); db_session.flush()
    permission = ToolPermission(household_id=household.id, tool_id=tool.id, tool_version="1", granted_by_account_id=account.id)
    high_permission = ToolPermission(household_id=household.id, tool_id=high_tool.id, tool_version="1", granted_by_account_id=account.id)
    db_session.add_all([permission, high_permission])
    agent = AgentDefinition(household_id=household.id, name="家庭总管")
    db_session.add(agent); db_session.flush()
    version = AgentVersion(household_id=household.id, agent_id=agent.id, version=1,
                           definition_json='{"prompt":"route","tools":["platform.current_month"]}', published_at=utcnow())
    db_session.add(version); db_session.flush()
    db_session.add(AgentToolBinding(household_id=household.id, agent_version_id=version.id, tool_id=tool.id))
    deployment = AgentDeployment(household_id=household.id, agent_version_id=version.id, revision=1)
    db_session.add(deployment); db_session.flush()
    run = AgentRun(household_id=household.id, deployment_id=deployment.id, principal_id=account.id,
                   member_id=member.id, session_id="session-1", agent_version_id=version.id,
                   deployment_revision=1, model_id="model-1", model_revision=1)
    db_session.add(run); db_session.commit()
    context = RunContext(principal_id=account.id, household_id=household.id, member_id=member.id, role="owner",
                         session_id="session-1", deployment_id=deployment.id, agent_version_id=version.id,
                         run_id=run.id, timezone="Asia/Shanghai", grants={tool.key: {"version": "1"}}, consent={},
                         model_id="model-1")
    return {"household_id": household.id, "agent_id": agent.id, "deployment_id": deployment.id,
            "tool_id": tool.id, "permission": permission, "context": context}


def test_run_context_is_typed_deeply_immutable_and_has_no_session_factory():
    from backend.agent.platform.context import RunContext

    context = RunContext(
        principal_id="account-1", household_id="household-1", member_id="member-1", role="owner",
        session_id="session-1", deployment_id="deployment-1", agent_version_id="agent-version-1",
        run_id="run-1", timezone="Asia/Shanghai", grants={"tool.read": {"version": "1"}},
        consent={"approval": "approved"},
    )

    with pytest.raises(FrozenInstanceError):
        context.run_id = "changed"
    with pytest.raises(TypeError):
        context.grants["tool.read"] = {}
    assert not hasattr(context, "session_factory")


def test_catalog_resolves_only_hardcoded_builtin_keys():
    from backend.agent.platform.catalog import BuiltinToolRegistry, UnknownToolKey

    registry = BuiltinToolRegistry.default()
    assert registry.resolve("platform.current_month").metadata.risk_level == "low"
    with pytest.raises(UnknownToolKey):
        registry.resolve("python:os.system")


@pytest.mark.parametrize("unsafe", [
    {"instructions": "ok", "url": "https://example.com"},
    {"instructions": "ok", "helper_script": "print('x')"},
    {"instructions": "ok", "attachments": [{"kind": "code", "name": "x.py"}]},
])
def test_skill_versions_reject_executable_or_remote_content(unsafe):
    from backend.agent.platform.versions import DefinitionRejected, validate_skill_definition

    with pytest.raises(DefinitionRejected) as error:
        validate_skill_definition(unsafe)
    assert error.value.code == "SKILL_DEFINITION_UNSAFE"


def test_restricted_workflow_rejects_unknown_nodes_loops_and_excessive_depth():
    from backend.agent.platform.workflow import WorkflowRejected, validate_workflow

    with pytest.raises(WorkflowRejected):
        validate_workflow({"nodes": [{"id": "x", "type": "script", "expression": "1+1"}]})
    with pytest.raises(WorkflowRejected):
        validate_workflow({"nodes": [{"id": "x", "type": "route", "next": "x"}]})
    with pytest.raises(WorkflowRejected) as error:
        validate_workflow({"nodes": [{"id": str(i), "type": "respond"} for i in range(65)]})
    assert error.value.code == "RUN_LIMIT_EXCEEDED"


def test_published_versions_are_immutable_and_deployment_switches_pointer(db_session, platform_seed):
    from backend.agent.platform.versions import AgentVersionService, VersionImmutable

    service = AgentVersionService(db_session, platform_seed["household_id"])
    version_1 = service.publish(platform_seed["agent_id"], {"prompt": "v1", "tools": []})
    deployment = service.deploy(version_1.id)
    with pytest.raises(VersionImmutable):
        service.replace(version_1.id, {"prompt": "changed"})
    version_2 = service.publish(platform_seed["agent_id"], {"prompt": "v2", "tools": []})
    service.switch(deployment.id, version_2.id)
    assert db_session.get(type(deployment), deployment.id).agent_version_id == version_2.id
    assert service.get(version_1.id).definition["prompt"] == "v1"


def test_skill_service_publishes_normalized_immutable_versions(db_session, platform_seed):
    from backend.agent.platform.versions import SkillVersionService, VersionImmutable
    from backend.app.db.models import Skill

    skill = Skill(household_id=platform_seed["household_id"], name="知识技能")
    db_session.add(skill); db_session.commit()
    service = SkillVersionService(db_session, platform_seed["household_id"])
    version = service.publish(skill.id, {"instructions": "# Read only", "attachments": [{"kind": "knowledge", "name": "guide.txt"}]})
    with pytest.raises(VersionImmutable):
        service.replace(version.id, {"instructions": "changed"})
    assert version.definition["instructions"] == "# Read only"


def test_installation_is_fixed_to_version_and_new_tool_requires_explicit_permission(db_session, platform_seed):
    from backend.agent.platform.installations import InstallationService

    service = InstallationService(db_session, platform_seed["household_id"], platform_seed["context"].principal_id)
    installation = service.install(platform_seed["deployment_id"])
    assert installation.agent_version_id == platform_seed["context"].agent_version_id
    assert service.authorized_tool_ids(installation.id) == set()
    service.grant(installation.id, platform_seed["tool_id"], "1")
    assert service.authorized_tool_ids(installation.id) == {platform_seed["tool_id"]}


def test_compiler_hides_ungranted_tools_and_revocation_blocks_stale_call(db_session, platform_seed):
    from backend.agent.platform.compiler import AgentCompiler
    from backend.agent.platform.policy import PlatformToolError

    compiler = AgentCompiler(db_session)
    compiled = compiler.compile(platform_seed["deployment_id"], platform_seed["context"])
    assert [tool.name for tool in compiled.tools] == ["platform.current_month"]

    permission = platform_seed["permission"]
    db_session.delete(permission)
    db_session.commit()
    with pytest.raises(PlatformToolError) as error:
        compiled.tools[0].invoke({})
    assert error.value.code == "PERMISSION_REQUIRED"


def test_high_risk_tool_interrupts_then_resumes_once_with_bound_consent(db_session, platform_seed):
    from backend.agent.platform.policy import ApprovalRequired, PlatformToolExecutor

    effects = []
    executor = PlatformToolExecutor(db_session, side_effect=lambda arguments, context: effects.append(arguments) or {"ok": True})
    with pytest.raises(ApprovalRequired) as interrupted:
        executor.execute("platform.test_high_risk", {"value": "safe"}, platform_seed["context"], "idem-1")
    approval_id = interrupted.value.approval_id
    executor.approve(approval_id, platform_seed["context"].principal_id, utcnow() + timedelta(minutes=5))
    first = executor.resume(approval_id, platform_seed["context"])
    second = executor.resume(approval_id, platform_seed["context"])
    assert first == second == {"ok": True}
    assert effects == [{"value": "safe"}]


def test_audit_redacts_secrets_authorization_and_sensitive_text(db_session, platform_seed):
    from backend.agent.platform.audit import ToolAuditService
    from backend.app.db.models import ToolExecution

    ToolAuditService(db_session).record(
        context=platform_seed["context"], tool_id=platform_seed["tool_id"], tool_version="1",
        status="success", started_at=utcnow(), idempotency_key="audit-1",
        arguments={"Authorization": "Bearer secret", "password": "hidden", "query": "private family text"},
        result={"token": "abc", "count": 1},
    )
    row = db_session.scalar(select(ToolExecution).where(ToolExecution.idempotency_key == "audit-1"))
    assert "secret" not in row.arguments_json
    assert "hidden" not in row.arguments_json
    assert "private family text" not in row.arguments_json
    assert "abc" not in row.result_json
    assert "[REDACTED]" in row.arguments_json


def test_sqlite_uses_memory_checkpoint_and_mysql_adapter_round_trips_namespace(db_session, platform_seed):
    from langgraph.checkpoint.memory import InMemorySaver
    from backend.agent.platform.checkpointer import SqlAlchemyCheckpointSaver, checkpoint_saver_for

    assert isinstance(checkpoint_saver_for(db_session.bind, lambda: db_session), InMemorySaver)
    saver = SqlAlchemyCheckpointSaver(lambda: db_session)
    namespace = f'{platform_seed["context"].household_id}/session-1/{platform_seed["context"].run_id}'
    config = {"configurable": {"thread_id": "session-1", "checkpoint_ns": namespace}}
    checkpoint = {"v": 4, "id": "cp-1", "ts": "2026-08-27T00:00:00Z", "channel_values": {}, "channel_versions": {}, "versions_seen": {}}
    stored = saver.put(config, checkpoint, {"source": "input", "step": 1, "parents": {}}, {})
    saver.put_writes(stored, [("messages", {"content": "hello"})], "task-1")
    loaded = saver.get_tuple(stored)
    assert loaded.checkpoint["id"] == "cp-1"
    assert loaded.config["configurable"]["checkpoint_ns"] == namespace
    assert loaded.pending_writes[0][1:] == ("messages", {"content": "hello"})
