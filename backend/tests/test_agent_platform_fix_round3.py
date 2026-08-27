import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.db.base import utcnow
from backend.tests.test_agent_platform_runtime import db_session, platform_seed  # noqa: F401


def _run(db, seed):
    from backend.app.db.models import AgentRun
    return db.get(AgentRun, seed["context"].run_id)


@pytest.mark.parametrize("events,expected,error", [
    ([{"event": "token", "data": {"content": "ok"}}], "completed", None),
    ([{"event": "approval_required", "data": {"approval_id": "a"}}], "waiting_approval", "APPROVAL_REQUIRED"),
])
def test_production_stream_persists_sse_run_outcome(db_session, platform_seed, events, expected, error):
    from backend.agent.platform.runner import DeploymentAgentRunner
    class Compiled:
        def stream(self, messages, context): yield from events
    runner = DeploymentAgentRunner(lambda: db_session)
    runner._compile_agent = lambda context, session: Compiled()
    output = list(runner.stream([], platform_seed["context"]))
    run = _run(db_session, platform_seed)
    assert output[-1]["data"]["status"] == expected
    assert run.status == expected and run.error_code == error
    assert (run.finished_at is not None) == (expected == "completed")


def test_production_stream_failure_and_generator_close_persist_outcomes(db_session, platform_seed):
    from backend.agent.platform.runner import DeploymentAgentRunner
    class Failing:
        def stream(self, messages, context): raise RuntimeError("raw"); yield
    runner = DeploymentAgentRunner(lambda: db_session)
    runner._compile_agent = lambda context, session: Failing()
    failed_events = list(runner.stream([], platform_seed["context"]))
    assert failed_events[-1]["data"]["error_code"] == "AGENT_RUN_FAILED"
    assert _run(db_session, platform_seed).error_code == "AGENT_RUN_FAILED"
    class Streaming:
        def stream(self, messages, context): yield {"event": "token", "data": {"content": "partial"}}; yield {"event": "token", "data": {"content": "later"}}
    runner._compile_agent = lambda context, session: Streaming()
    iterator = runner.stream([], platform_seed["context"]); next(iterator); next(iterator); iterator.close()
    run = _run(db_session, platform_seed)
    assert run.status == "cancelled" and run.error_code == "RUN_CANCELLED"


def test_resume_rehydrates_snapshot_and_rejects_live_version_change(db_session, platform_seed):
    from backend.agent.platform.snapshots import SnapshotCompiler
    from backend.app.db.models import AgentDeployment, AgentVersion
    snapshot = json.loads(json.dumps(platform_seed["context"].compiled_snapshot or {}))
    if not snapshot:
        from backend.agent.platform.snapshots import CompiledSnapshotService
        snapshot = json.loads(json.dumps(CompiledSnapshotService(db_session).capture(platform_seed["deployment_id"],
            platform_seed["installation_id"], platform_seed["context"])))
    compiler = SnapshotCompiler(db_session)
    hydrated = compiler.validate_and_load(snapshot, platform_seed["context"])
    version = db_session.get(AgentVersion, platform_seed["context"].agent_version_id)
    version.definition_json = '{"prompt":"changed","tools":[]}'
    deployment = db_session.get(AgentDeployment, platform_seed["deployment_id"])
    deployment.revision += 1; db_session.commit()
    with pytest.raises(Exception) as error: compiler.validate_safety(hydrated, platform_seed["context"])
    assert error.value.code == "VERSION_CHANGED"
    assert hydrated["agent_version"]["definition"].get("prompt") != "changed"


@pytest.mark.parametrize("node", [
    {"id": "d", "type": "delegate", "message": "x"},
    {"id": "t", "type": "call_tool", "arguments": {}},
    {"id": "c", "type": "confirm", "message": "x"},
    {"id": "p", "type": "parallel", "branches": ["x"], "extra": True},
    {"id": "x", "type": "condition", "field": "x", "equals": {"unsafe": 1}, "then": "a", "else": "b"},
])
def test_discriminated_workflow_nodes_reject_malformed_at_validation(node):
    from backend.agent.platform.workflow import WorkflowRejected, validate_workflow
    with pytest.raises(WorkflowRejected): validate_workflow({"entry": node["id"], "nodes": [node]})


def test_delegate_depth_is_charged_once_and_child_limit_combines(platform_seed):
    from backend.agent.platform.compiler import BudgetedChildRunnable
    from backend.agent.platform.context import RunBudget
    class Child:
        def invoke(self, payload, context): return context.budget.snapshot()
        def with_config(self, config): return self
    budget = RunBudget(max_delegation_depth=1, max_tool_calls=3)
    result = BudgetedChildRunnable(Child(), budget, child_limits={"delegation_depth": 1, "tool_calls": 1}).invoke(
        {}, context=type(platform_seed["context"])(**{**platform_seed["context"].__dict__, "budget": budget}))
    assert result["delegation_depth"] == 1
    assert budget.snapshot()["delegation_depth"] == 0


def test_wall_clock_deadline_survives_restore():
    from backend.agent.platform.context import RunBudget
    expired = (utcnow() - timedelta(seconds=5)).isoformat()
    budget = RunBudget.restore({"wall_time_seconds": 1}, {"deadline_at": expired})
    with pytest.raises(RuntimeError) as error: budget.check_time()
    assert str(error.value) == "RUN_LIMIT_EXCEEDED"


def test_one_active_installation_per_deployment_and_transactional_upgrade(db_session, platform_seed):
    from backend.agent.platform.installations import InstallationService
    from backend.app.db.models import AgentInstallation, AgentVersion
    current = db_session.get(AgentVersion, platform_seed["context"].agent_version_id)
    newer = AgentVersion(household_id=current.household_id, agent_id=current.agent_id, version=2,
        definition_json=current.definition_json, published_at=utcnow())
    db_session.add(newer); db_session.flush()
    service = InstallationService(db_session, platform_seed["household_id"], platform_seed["context"].principal_id)
    upgraded = service.upgrade(platform_seed["deployment_id"], newer.id)
    old = db_session.get(AgentInstallation, platform_seed["installation_id"])
    assert old.status == "upgraded" and old.authority_slot is None
    assert upgraded.status == "installed" and upgraded.authority_slot == "active"
    duplicate = AgentInstallation(household_id=platform_seed["household_id"], deployment_id=platform_seed["deployment_id"],
        agent_version_id=newer.id, status="installed", authority_slot="active")
    db_session.add(duplicate)
    with pytest.raises(IntegrityError): db_session.commit()


@pytest.mark.parametrize("status,code", [("retryable", "APPROVAL_RETRY_REQUIRED"), ("executing", "APPROVAL_IN_PROGRESS")])
def test_public_resume_contract_exposes_stable_approval_state(client, app, status, code):
    from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentRun, AgentVersion, ApprovalRequest,
                                       HouseholdMember, ToolDefinition)
    login = client.post("/api/auth/login", json={"username": "demo", "password": "DemoTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory.begin() as db:
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == login["account"]["id"]))
        definition = AgentDefinition(household_id=member.household_id, name=f"contract-{status}")
        db.add(definition); db.flush()
        version = AgentVersion(household_id=member.household_id, agent_id=definition.id, version=1,
            definition_json='{"prompt":"x","tools":[]}', published_at=utcnow())
        db.add(version); db.flush()
        deployment = AgentDeployment(household_id=member.household_id, agent_version_id=version.id)
        db.add(deployment); db.flush()
        run = AgentRun(household_id=member.household_id, deployment_id=deployment.id,
            principal_id=login["account"]["id"], member_id=member.id, session_id=f"contract-{status}",
            agent_version_id=version.id, deployment_revision=1, model_id="model", model_revision=1)
        db.add(run); db.flush()
        tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "platform.test_high_risk"))
        approval = ApprovalRequest(household_id=member.household_id, run_id=run.id,
            principal_id=login["account"]["id"], tool_id=tool.id, tool_version="1",
            deployment_id=deployment.id, deployment_revision=1, agent_version_id=version.id,
            arguments_digest="digest", arguments_json="{}", idempotency_key=f"contract-{status}", status=status)
        db.add(approval); db.flush(); approval_id = approval.id
    response = client.post(f"/api/approvals/{approval_id}/resume", headers=headers)
    assert response.status_code == 409 and response.json()["detail"]["code"] == code


def test_resumed_side_effect_failure_aligns_approval_audit_and_run(db_session, platform_seed):
    from backend.agent.platform.approvals import ApprovalResumeService
    from backend.agent.platform.policy import PlatformToolExecutor
    from backend.app.db.models import ApprovalRequest, ToolExecution
    executor = PlatformToolExecutor(db_session)
    with pytest.raises(Exception) as interrupted:
        executor.execute("platform.test_high_risk", {"value": "safe"}, platform_seed["context"], "round3-failure")
    approval = db_session.get(ApprovalRequest, interrupted.value.approval_id)
    executor.approve(approval.id, platform_seed["context"].principal_id, utcnow() + timedelta(minutes=5))
    service = ApprovalResumeService(lambda: db_session,
        side_effect=lambda args, context: (_ for _ in ()).throw(RuntimeError("raw secret")))
    with pytest.raises(Exception): service.claim_and_execute(approval.id, platform_seed["context"], {"value": "safe"})
    approval = db_session.get(ApprovalRequest, approval.id); run = _run(db_session, platform_seed)
    audit = db_session.scalar(select(ToolExecution).where(ToolExecution.status == "approval_retryable"))
    assert approval.status == "retryable" and approval.error_code == "SIDE_EFFECT_FAILED"
    assert run.status == "retry_required" and run.error_code == "APPROVAL_RETRY_REQUIRED"
    assert audit.error_code == "SIDE_EFFECT_FAILED" and "raw secret" not in audit.result_json


def test_consumed_public_resume_returns_persisted_result_without_runner(client, app):
    from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentRun, AgentVersion, ApprovalRequest,
                                       HouseholdMember, ToolDefinition)
    login = client.post("/api/auth/login", json={"username": "demo", "password": "DemoTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory.begin() as db:
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == login["account"]["id"]))
        definition = AgentDefinition(household_id=member.household_id, name="consumed")
        db.add(definition); db.flush()
        version = AgentVersion(household_id=member.household_id, agent_id=definition.id, version=1,
            definition_json='{"prompt":"x","tools":[]}', published_at=utcnow())
        db.add(version); db.flush(); deployment = AgentDeployment(household_id=member.household_id, agent_version_id=version.id)
        db.add(deployment); db.flush(); run = AgentRun(household_id=member.household_id, deployment_id=deployment.id,
            principal_id=login["account"]["id"], member_id=member.id, session_id="consumed", agent_version_id=version.id,
            deployment_revision=1, model_id="m", model_revision=1, status="completed")
        db.add(run); db.flush(); tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "platform.test_high_risk"))
        approval = ApprovalRequest(household_id=member.household_id, run_id=run.id, principal_id=login["account"]["id"],
            tool_id=tool.id, tool_version="1", deployment_id=deployment.id, deployment_revision=1,
            agent_version_id=version.id, arguments_digest="x", arguments_json="{}", idempotency_key="consumed-public",
            status="consumed", result_json='{"ok":true}')
        db.add(approval); db.flush(); approval_id = approval.id
    class Never:
        def resume(self, *args): raise AssertionError("consumed result must not resume")
    app.state.deployment_agent_runner = Never()
    response = client.post(f"/api/approvals/{approval_id}/resume", headers=headers)
    assert response.status_code == 200 and response.json()["result"] == {"ok": True}


def test_runner_persists_start_interrupt_resume_timestamps(db_session, platform_seed):
    from backend.agent.platform.runner import DeploymentAgentRunner
    class Waiting:
        def stream(self, messages, context): yield {"event": "approval_required", "data": {"approval_id": "a"}}
        def resume(self, approval_id, context): yield {"event": "token", "data": {"content": "done"}}
    runner = DeploymentAgentRunner(lambda: db_session); runner._compile_agent = lambda context, session: Waiting()
    list(runner.stream([], platform_seed["context"])); run = _run(db_session, platform_seed)
    assert run.started_at and run.interrupted_at and run.finished_at is None
    events = list(runner.resume("a", platform_seed["context"])); run = _run(db_session, platform_seed)
    assert run.resumed_at and run.finished_at and run.status == "completed"
    assert events[-1]["data"]["status"] == "completed"


def test_failed_production_stream_emits_terminal_sse_matching_db(db_session, platform_seed):
    from backend.agent.platform.runner import DeploymentAgentRunner
    class Failing:
        def stream(self, messages, context): raise RuntimeError("raw"); yield
    runner = DeploymentAgentRunner(lambda: db_session); runner._compile_agent = lambda context, session: Failing()
    events = list(runner.stream([], platform_seed["context"]))
    run = _run(db_session, platform_seed)
    assert events[-1]["data"] == {"run_id": run.id, "status": "failed", "error_code": "AGENT_RUN_FAILED"}
    assert run.status == "failed" and run.error_code == "AGENT_RUN_FAILED"


def test_snapshot_rehydrated_high_risk_tool_uses_interrupt_wrapper(db_session, platform_seed):
    from langgraph.checkpoint.memory import InMemorySaver
    from backend.agent.platform.snapshots import CompiledSnapshotService, SnapshotCompiler
    from backend.app.db.models import AgentToolBinding, ToolDefinition
    from backend.tests.test_agent_platform_fix_round1 import OneToolModel
    high = db_session.scalar(select(ToolDefinition).where(ToolDefinition.key == "platform.test_high_risk"))
    db_session.add(AgentToolBinding(household_id=platform_seed["household_id"],
        agent_version_id=platform_seed["context"].agent_version_id, tool_id=high.id)); db_session.commit()
    snapshot = CompiledSnapshotService(db_session).capture(platform_seed["deployment_id"],
        platform_seed["installation_id"], platform_seed["context"])
    compiler = SnapshotCompiler(db_session, InMemorySaver(),
        model_factory=lambda deployment: OneToolModel(tool_name=high.key))
    loaded = compiler.validate_and_load(json.loads(json.dumps(snapshot)), platform_seed["context"])
    compiled = compiler.rehydrate(loaded, platform_seed["context"])
    events = list(compiled.stream([{"role": "user", "content": "run"}], platform_seed["context"]))
    assert events[-1]["event"] == "approval_required"


def test_snapshot_rehydration_ignores_live_child_definition_mutation(db_session, platform_seed):
    from backend.agent.platform.snapshots import CompiledSnapshotService, SnapshotCompiler
    from backend.app.db.models import AgentDefinition, AgentDeployment, AgentInstallation, AgentVersion
    child_def = AgentDefinition(household_id=platform_seed["household_id"], name="snapshot-child")
    db_session.add(child_def); db_session.flush()
    child_version = AgentVersion(household_id=platform_seed["household_id"], agent_id=child_def.id, version=1,
        definition_json='{"prompt":"original child","tools":[]}', published_at=utcnow())
    db_session.add(child_version); db_session.flush()
    child_deployment = AgentDeployment(household_id=platform_seed["household_id"], agent_version_id=child_version.id)
    db_session.add(child_deployment); db_session.flush()
    db_session.add(AgentInstallation(household_id=platform_seed["household_id"], deployment_id=child_deployment.id,
        agent_version_id=child_version.id, status="installed", authority_slot="active"))
    parent = db_session.get(AgentVersion, platform_seed["context"].agent_version_id)
    parent.definition_json = json.dumps({"prompt": "parent", "tools": [], "child_deployments": [child_deployment.id]})
    db_session.commit()
    snapshot = CompiledSnapshotService(db_session).capture(platform_seed["deployment_id"],
        platform_seed["installation_id"], platform_seed["context"])
    child_version.definition_json = '{"prompt":"changed child","tools":[]}'
    db_session.commit()
    from backend.tests.test_agent_platform_delegation import ChildContextModel
    compiler = SnapshotCompiler(db_session, model_factory=lambda deployment: ChildContextModel())
    loaded = compiler.validate_and_load(json.loads(json.dumps(snapshot)), platform_seed["context"])
    compiler.validate_safety(loaded, platform_seed["context"])
    compiled = compiler.rehydrate(loaded, platform_seed["context"])
    assert loaded["children"][0]["agent_version"]["definition"]["prompt"] == "original child"
    assert compiled.subagent_names == ("snapshot-child",)


def test_confirm_parallel_and_child_budgets_are_authoritative(platform_seed):
    from backend.agent.platform.context import RunBudget
    from backend.agent.platform.workflow import WorkflowExecutor
    confirm_budget = RunBudget(max_tool_calls=1)
    confirm_budget.consume_tool()
    with pytest.raises(RuntimeError): list(WorkflowExecutor(lambda *args: None,
        confirm=lambda node: iter(()), budget=confirm_budget).run({"entry": "c", "nodes": [
            {"id": "c", "type": "confirm", "message": "x", "action": "continue",
             "tool": "platform.workflow_confirm", "next": "e"}, {"id": "e", "type": "end"}]}, {}))
    parallel_budget = RunBudget(max_tokens=1)
    with pytest.raises(RuntimeError): list(WorkflowExecutor(lambda *args: None, budget=parallel_budget).run(
        {"entry": "p", "nodes": [{"id": "p", "type": "parallel", "branches": ["a", "b"], "next": "e"},
            {"id": "a", "type": "respond", "text": "long", "next": "ea"}, {"id": "ea", "type": "end"},
            {"id": "b", "type": "respond", "text": "long", "next": "eb"}, {"id": "eb", "type": "end"},
            {"id": "e", "type": "end"}]}, {}))
