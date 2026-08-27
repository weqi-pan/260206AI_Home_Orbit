from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import select

from backend.app.db.base import utcnow
from backend.tests.test_agent_platform_runtime import db_session, platform_seed  # noqa: F401


class OneToolModel(BaseChatModel):
    tool_name: str

    @property
    def _llm_type(self): return "fix-round-tool-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs): return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any):
        if not any(isinstance(message, ToolMessage) for message in messages):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[
                {"name": self.tool_name, "args": {"value": "safe"}, "id": "stable-call-1", "type": "tool_call"}
            ]))])
        tool_message = next(message for message in messages if isinstance(message, ToolMessage))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=str(tool_message.content)))])


def test_compiled_graph_persists_interrupt_and_resumes_same_checkpoint_once(db_session, platform_seed):
    from langgraph.checkpoint.memory import InMemorySaver
    from backend.agent.platform.compiler import AgentCompiler
    from backend.agent.platform.policy import PlatformToolExecutor
    from backend.app.db.models import AgentToolBinding, ApprovalRequest, ToolDefinition

    high = db_session.scalar(select(ToolDefinition).where(ToolDefinition.key == "platform.test_high_risk"))
    db_session.add(AgentToolBinding(household_id=platform_seed["household_id"],
        agent_version_id=platform_seed["context"].agent_version_id, tool_id=high.id)); db_session.commit()
    context = platform_seed["context"]
    context = type(context)(**{**context.__dict__, "chat_model": OneToolModel(tool_name=high.key)})
    effects = []
    compiler = AgentCompiler(db_session, checkpointer=InMemorySaver(),
        tool_executor_factory=lambda session: PlatformToolExecutor(session,
            side_effect=lambda args, _context: effects.append(args) or {"ok": True}))
    compiled = compiler.compile(platform_seed["deployment_id"], context)

    first = list(compiled.stream([{"role": "user", "content": "run"}], context))
    approval = db_session.scalar(select(ApprovalRequest).where(ApprovalRequest.run_id == context.run_id))
    assert first[-1]["event"] == "approval_required"
    assert approval.checkpoint_ns.endswith(context.run_id)
    PlatformToolExecutor(db_session).approve(approval.id, context.principal_id, utcnow() + timedelta(minutes=5))
    resumed = list(compiled.resume(approval.id, context))
    repeated = list(compiled.resume(approval.id, context))
    assert resumed
    assert any("ok" in str(event.get("data", {}).get("content", "")) for event in resumed)
    assert repeated[-1]["data"]["status"] == "completed"
    assert effects == [{"value": "safe"}]


def test_concurrent_graph_resume_claims_side_effect_once(db_session, platform_seed):
    from backend.agent.platform.approvals import ApprovalResumeService
    from backend.agent.platform.policy import PlatformToolExecutor
    from backend.app.db.models import ApprovalRequest, ToolDefinition

    high = db_session.scalar(select(ToolDefinition).where(ToolDefinition.key == "platform.test_high_risk"))
    executor = PlatformToolExecutor(db_session)
    with pytest.raises(Exception) as interrupted:
        executor.execute(high.key, {"value": "safe"}, platform_seed["context"], "concurrent-call")
    approval = db_session.get(ApprovalRequest, interrupted.value.approval_id)
    executor.approve(approval.id, platform_seed["context"].principal_id, utcnow() + timedelta(minutes=5))
    effects = []
    service = ApprovalResumeService(lambda: db_session,
        side_effect=lambda args, _context: effects.append(args) or {"ok": True})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.claim_and_execute(approval.id, platform_seed["context"], {"value": "safe"}), range(2)))
    assert results == [{"ok": True}, {"ok": True}]
    assert effects == [{"value": "safe"}]


def test_compiler_builds_persisted_child_and_real_task_delegation(db_session, platform_seed):
    from backend.agent.platform.compiler import AgentCompiler
    from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentInstallation, AgentToolBinding,
                                       AgentVersion, InstallationPermission, ToolDefinition)
    from backend.tests.test_agent_platform_delegation import ChildContextModel, DelegatingModel

    child_definition = AgentDefinition(household_id=platform_seed["household_id"], name="child")
    db_session.add(child_definition); db_session.flush()
    child_version = AgentVersion(household_id=platform_seed["household_id"], agent_id=child_definition.id, version=1,
        definition_json='{"prompt":"child","tools":["platform.context_probe"]}', published_at=utcnow())
    db_session.add(child_version); db_session.flush()
    child_deployment = AgentDeployment(household_id=platform_seed["household_id"], agent_version_id=child_version.id)
    db_session.add(child_deployment); db_session.flush()
    child_installation = AgentInstallation(household_id=platform_seed["household_id"], deployment_id=child_deployment.id,
        agent_version_id=child_version.id, status="installed")
    db_session.add(child_installation); db_session.flush()
    probe = ToolDefinition(key="platform.context_probe", display_name="probe", description="probe", risk_level="low",
                           version="1", enabled=True)
    db_session.add(probe); db_session.flush()
    db_session.add(AgentToolBinding(household_id=platform_seed["household_id"],
        agent_version_id=child_version.id, tool_id=probe.id))
    db_session.add(InstallationPermission(household_id=platform_seed["household_id"],
        installation_id=child_installation.id, tool_id=probe.id, tool_version="1",
        granted_by_account_id=platform_seed["context"].principal_id))
    parent = db_session.get(AgentVersion, platform_seed["context"].agent_version_id)
    parent.definition_json = '{"prompt":"delegate","tools":[],"child_deployments":["' + child_deployment.id + '"]}'
    db_session.commit()
    context = platform_seed["context"]
    context = type(context)(**{**context.__dict__, "chat_model": DelegatingModel()})
    compiled = AgentCompiler(db_session, model_factory=lambda deployment_id: ChildContextModel()).compile(
        platform_seed["deployment_id"], context)
    events = list(compiled.stream([{"role": "user", "content": "delegate"}], context))
    assert any(event["event"] == "delegate" for event in events)
    assert any(platform_seed["household_id"] in str(event) for event in events)
    assert compiled.subagent_names == ("child",)


def test_mysql_checkpointer_emits_tenant_scoped_sql_contract():
    from sqlalchemy import delete, select
    from sqlalchemy.dialects.mysql import dialect
    from backend.app.db.models import AgentCheckpoint, AgentCheckpointWrite

    assert AgentCheckpoint.__table__.c.household_id.type.length == 36
    assert AgentCheckpointWrite.__table__.c.checkpoint_ns.type.length == 250
    assert {item.name for item in AgentCheckpoint.__table__.constraints if item.name} >= {"uq_agent_checkpoint_key"}
    read_sql = str(select(AgentCheckpoint).where(AgentCheckpoint.household_id == "h",
        AgentCheckpoint.run_id == "r", AgentCheckpoint.checkpoint_ns == "h/s/r").compile(dialect=dialect()))
    delete_sql = str(delete(AgentCheckpointWrite).where(AgentCheckpointWrite.household_id == "h",
        AgentCheckpointWrite.run_id == "r", AgentCheckpointWrite.checkpoint_ns == "h/s/r").compile(dialect=dialect()))
    assert "agent_checkpoints.household_id" in read_sql and "agent_checkpoints.run_id" in read_sql
    assert "agent_checkpoint_writes.household_id" in delete_sql and "agent_checkpoint_writes.run_id" in delete_sql
