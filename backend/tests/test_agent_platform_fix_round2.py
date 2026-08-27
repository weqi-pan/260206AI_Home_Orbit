import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.app.db.base import utcnow
from backend.tests.test_agent_platform_runtime import db_session, platform_seed  # noqa: F401


def test_production_runner_session_wrapper_exposes_resume(db_session, platform_seed):
    from backend.agent.platform.runner import DeploymentAgentRunner

    calls = []
    class Compiled:
        def stream(self, messages, context): yield {"event": "token", "data": {"content": "x"}}
        def resume(self, approval_id, context):
            calls.append((approval_id, context.run_id)); yield {"event": "run", "data": {"status": "completed"}}
    runner = DeploymentAgentRunner(lambda: db_session)
    runner._compile_agent = lambda context, session: Compiled()
    events = list(runner.resume("approval-1", platform_seed["context"]))
    assert calls == [("approval-1", platform_seed["context"].run_id)]
    assert events[-1]["data"]["status"] == "completed"


def test_claim_failure_transitions_retryable_and_repeated_resume_is_stable(db_session, platform_seed):
    from backend.agent.platform.approvals import ApprovalResumeService
    from backend.agent.platform.policy import PlatformToolExecutor
    from backend.app.db.models import ApprovalRequest

    executor = PlatformToolExecutor(db_session)
    with pytest.raises(Exception) as interrupted:
        executor.execute("platform.test_high_risk", {"value": "safe"}, platform_seed["context"], "fix2-failure")
    approval = db_session.get(ApprovalRequest, interrupted.value.approval_id)
    executor.approve(approval.id, platform_seed["context"].principal_id, utcnow() + timedelta(minutes=5))
    service = ApprovalResumeService(lambda: db_session,
        side_effect=lambda args, context: (_ for _ in ()).throw(RuntimeError("raw provider secret")))
    with pytest.raises(Exception) as failed:
        service.claim_and_execute(approval.id, platform_seed["context"], {"value": "safe"})
    approval = db_session.get(ApprovalRequest, approval.id)
    assert approval.status == "retryable"
    assert approval.error_code == "SIDE_EFFECT_FAILED"
    assert "raw provider secret" not in (approval.error_detail or "")
    with pytest.raises(Exception) as repeated:
        service.claim_and_execute(approval.id, platform_seed["context"], {"value": "safe"})
    assert repeated.value.code == "APPROVAL_RETRY_REQUIRED"


def test_stale_executing_approval_is_reconciled_to_retryable(db_session, platform_seed):
    from backend.agent.platform.approvals import ApprovalResumeService
    from backend.agent.platform.policy import PlatformToolExecutor
    from backend.app.db.models import ApprovalRequest

    executor = PlatformToolExecutor(db_session)
    with pytest.raises(Exception) as interrupted:
        executor.execute("platform.test_high_risk", {"value": "safe"}, platform_seed["context"], "fix2-stale")
    row = db_session.get(ApprovalRequest, interrupted.value.approval_id)
    row.status = "executing"; row.claimed_at = utcnow() - timedelta(minutes=10); db_session.commit()
    service = ApprovalResumeService(lambda: db_session, side_effect=lambda args, context: {"ok": True})
    with pytest.raises(Exception) as error:
        service.claim_and_execute(row.id, platform_seed["context"], {"value": "safe"})
    assert error.value.code == "APPROVAL_RETRY_REQUIRED"
    row = db_session.get(ApprovalRequest, row.id)
    assert row.status == "retryable"


def test_multiple_persisted_children_have_unique_stable_names_and_descriptions(db_session, platform_seed):
    from backend.agent.platform.compiler import AgentCompiler
    from backend.app.db.models import AgentDefinition, AgentDeployment, AgentInstallation, AgentVersion

    child_ids = []
    for name in ("billing", "support"):
        definition = AgentDefinition(household_id=platform_seed["household_id"], name=name)
        db_session.add(definition); db_session.flush()
        version = AgentVersion(household_id=platform_seed["household_id"], agent_id=definition.id, version=1,
            definition_json=json.dumps({"prompt": name, "tools": [], "metadata": {"description": f"{name} child"}}),
            published_at=utcnow())
        db_session.add(version); db_session.flush()
        deployment = AgentDeployment(household_id=platform_seed["household_id"], agent_version_id=version.id)
        db_session.add(deployment); db_session.flush()
        db_session.add(AgentInstallation(household_id=platform_seed["household_id"], deployment_id=deployment.id,
            agent_version_id=version.id)); child_ids.append(deployment.id)
    parent = db_session.get(AgentVersion, platform_seed["context"].agent_version_id)
    parent.definition_json = json.dumps({"prompt": "parent", "tools": [], "child_deployments": child_ids})
    db_session.commit()
    compiled = AgentCompiler(db_session).compile(platform_seed["deployment_id"], platform_seed["context"])
    assert compiled.subagent_names == ("billing", "support")
    assert [item["description"] for item in compiled.subagents] == ["billing child", "support child"]
    from backend.agent.platform.snapshots import CompiledSnapshotService
    snapshot = CompiledSnapshotService(db_session).capture(platform_seed["deployment_id"],
        platform_seed["installation_id"], platform_seed["context"])
    assert [child["agent"]["name"] for child in snapshot["children"]] == ["billing", "support"]


def test_complete_snapshot_contains_children_provenance_and_is_immutable_after_mutation(db_session, platform_seed):
    from backend.agent.platform.snapshots import CompiledSnapshotService
    from backend.app.db.models import AgentVersion, InstallationPermission

    snapshot = CompiledSnapshotService(db_session).capture(platform_seed["deployment_id"],
        platform_seed["installation_id"], platform_seed["context"])
    assert snapshot["tools"][0]["tool_id"] == platform_seed["tool_id"]
    assert snapshot["tools"][0]["permission_id"] == platform_seed["installation_permission"].id
    assert snapshot["model"]["profile"] == platform_seed["context"].model_snapshot.get("profile", {})
    original = json.dumps(snapshot, sort_keys=True)
    version = db_session.get(AgentVersion, platform_seed["context"].agent_version_id)
    version.definition_json = '{"prompt":"mutated","tools":[]}'
    permission = db_session.get(InstallationPermission, platform_seed["installation_permission"].id)
    permission.tool_version = "changed"; db_session.commit()
    assert json.dumps(snapshot, sort_keys=True) == original


def test_budget_exposes_read_only_snapshot_and_no_mutable_state():
    from backend.agent.platform.context import RunBudget

    budget = RunBudget(max_tool_calls=2)
    budget.consume_tool()
    assert budget.snapshot()["tool_calls"] == 1
    assert not hasattr(budget, "state")
    with pytest.raises(TypeError):
        budget.snapshot()["tool_calls"] = 99


@pytest.mark.parametrize("definition", [
    {"instructions": "safe", "knowledge": [{"asset_id": 1}]},
    {"instructions": "safe", "attachments": [{"kind": "knowledge"}]},
    {"instructions": "safe", "attachments": [{"kind": "knowledge", "name": "x", "asset_id": "a", "nested_url": "x"}]},
])
def test_nested_skill_schema_rejects_malformed_structures(definition):
    from backend.agent.platform.versions import DefinitionRejected, validate_skill_definition
    with pytest.raises(DefinitionRejected): validate_skill_definition(definition)


@pytest.mark.parametrize("definition", [
    {"prompt": "safe", "budgets": {"tool_calls": "many"}},
    {"prompt": "safe", "model_requirements": {"capabilities": "reasoning"}},
    {"prompt": "safe", "permissions": [{"tool": 3}]},
    {"prompt": "safe", "tests": [{"input": "x"}]},
    {"prompt": "safe", "child_deployments": [{"deployment_id": "x"}]},
])
def test_nested_agent_schema_rejects_wrong_types_or_missing_fields(definition):
    from backend.agent.platform.versions import DefinitionRejected, validate_agent_definition
    with pytest.raises(DefinitionRejected): validate_agent_definition(definition)


def test_safe_nested_agent_and_skill_schemas_normalize():
    from backend.agent.platform.versions import validate_agent_definition, validate_skill_definition
    skill = validate_skill_definition({"instructions": "普通家庭说明", "knowledge": ["asset-1"],
        "attachments": [{"kind": "knowledge", "name": "guide", "asset_id": "asset-1", "content_hash": "a" * 64}]})
    agent = validate_agent_definition({"prompt": "普通家庭助手", "model_requirements": {"capabilities": ["chat"]},
        "permissions": [{"tool_key": "platform.current_month", "required": True}],
        "budgets": {"tool_calls": 2, "tokens": 100, "wall_time_seconds": 30, "delegation_depth": 1},
        "tests": [{"name": "smoke", "input": "hello", "expected_contains": "hi"}],
        "tools": ["platform.current_month"], "child_deployments": [], "metadata": {"description": "safe"}})
    assert skill["knowledge"] == ["asset-1"]
    assert agent["budgets"]["tool_calls"] == 2


def test_run_context_has_no_model_and_runner_binds_model_outside_context(platform_seed):
    from backend.agent.platform.runner import DeploymentAgentRunner
    context = platform_seed["context"]
    assert not hasattr(context, "chat_model")
    from langgraph.checkpoint.memory import InMemorySaver
    runner = DeploymentAgentRunner(lambda: None, checkpointer=InMemorySaver())
    model = object(); runner.bind_model(context.run_id, model)
    assert runner.model_for(context.run_id) is model


def test_workflow_delegate_parallel_and_confirm_use_handlers_and_shared_budget(platform_seed):
    from backend.agent.platform.workflow import WorkflowExecutor
    from backend.agent.platform.context import RunBudget
    from backend.agent.platform.policy_errors import ApprovalRequired

    budget = RunBudget(max_delegation_depth=1, max_tool_calls=2)
    delegated, confirmed = [], []
    workflow = {"entry": "parallel", "nodes": [
        {"id": "parallel", "type": "parallel", "branches": ["delegate", "confirm"], "next": "end"},
        {"id": "delegate", "type": "delegate", "subagent": "billing", "message": "help", "next": "branch_end_1"},
        {"id": "branch_end_1", "type": "end"},
        {"id": "confirm", "type": "confirm", "message": "approve", "next": "branch_end_2"},
        {"id": "branch_end_2", "type": "end"},
        {"id": "end", "type": "end"},
    ]}
    executor = WorkflowExecutor(lambda *args: None,
        delegate=lambda node: delegated.append(node["subagent"]) or iter([{"event": "delegate", "data": node}]),
        confirm=lambda node: confirmed.append(node["id"]), budget=budget)
    events = list(executor.run(workflow, {}))
    assert delegated == ["billing"] and confirmed == ["confirm"]
    assert budget.snapshot()["delegation_depth"] == 0
    assert any(event["event"] == "delegate" for event in events)


def test_production_workflow_confirm_interrupts_and_resumes_with_shared_budget(db_session, platform_seed):
    from langgraph.checkpoint.memory import InMemorySaver
    from backend.agent.platform.compiler import AgentCompiler
    from backend.agent.platform.context import RunBudget
    from backend.app.db.models import AgentVersion, ApprovalRequest, InstallationPermission, ToolDefinition

    confirm_tool = ToolDefinition(key="platform.workflow_confirm", display_name="confirm", description="confirm",
        risk_level="high", version="1", enabled=True)
    db_session.add(confirm_tool); db_session.flush()
    db_session.add(InstallationPermission(household_id=platform_seed["household_id"],
        installation_id=platform_seed["installation_id"], tool_id=confirm_tool.id, tool_version="1",
        granted_by_account_id=platform_seed["context"].principal_id))

    version = db_session.get(AgentVersion, platform_seed["context"].agent_version_id)
    version.definition_json = json.dumps({"prompt": "workflow", "tools": [], "budgets": {"tool_calls": 1,
        "tokens": 50, "wall_time_seconds": 30, "delegation_depth": 1}, "workflow": {"entry": "confirm", "nodes": [
            {"id": "confirm", "type": "confirm", "message": "continue?", "next": "reply"},
            {"id": "reply", "type": "respond", "text": "continued", "next": "end"},
            {"id": "end", "type": "end"}]}})
    db_session.commit()
    context = type(platform_seed["context"])(**{**platform_seed["context"].__dict__,
        "grants": {**dict(platform_seed["context"].grants), "platform.workflow_confirm": {"version": "1"}},
        "budget": RunBudget(max_tool_calls=1, max_tokens=50, wall_time_seconds=30, max_delegation_depth=1)})
    compiled = AgentCompiler(db_session, checkpointer=InMemorySaver()).compile(platform_seed["deployment_id"], context)
    interrupted = list(compiled.stream([{"role": "user", "content": "run"}], context))
    assert interrupted[-1]["event"] == "approval_required"
    approval = db_session.scalar(select(ApprovalRequest).where(ApprovalRequest.run_id == context.run_id,
                                                               ApprovalRequest.tool_id == confirm_tool.id))
    assert interrupted[-1]["data"]["approval_id"] == approval.id
    from backend.agent.platform.policy import PlatformToolExecutor
    PlatformToolExecutor(db_session).approve(approval.id, context.principal_id, utcnow() + timedelta(minutes=5))
    resumed = list(compiled.resume(approval.id, context))
    assert any(event.get("data", {}).get("content") == "continued" for event in resumed)
    assert context.budget.snapshot()["tool_calls"] == 0


def test_public_approval_resume_uses_production_runner(client, app, monkeypatch):
    from types import SimpleNamespace
    from backend.app.api import approvals as approvals_api
    from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentRun, AgentVersion, ApprovalRequest,
                                       HouseholdMember, ToolDefinition)

    called = []
    class Runner:
        def bind_model(self, run_id, model): called.append(("bind", run_id))
        def resume(self, approval_id, context):
            called.append(("resume", approval_id, context.run_id)); yield {"event": "run", "data": {"status": "completed"}}
    app.state.deployment_agent_runner = Runner()
    login = client.post("/api/auth/login", json={"username": "demo", "password": "DemoTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory.begin() as db:
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == login["account"]["id"]))
        definition = AgentDefinition(household_id=member.household_id, name="resume-api")
        db.add(definition); db.flush()
        version = AgentVersion(household_id=member.household_id, agent_id=definition.id, version=1,
            definition_json='{"prompt":"resume","tools":[]}', published_at=utcnow())
        db.add(version); db.flush()
        deployment = AgentDeployment(household_id=member.household_id, agent_version_id=version.id)
        db.add(deployment); db.flush()
        run = AgentRun(household_id=member.household_id, deployment_id=deployment.id,
            principal_id=login["account"]["id"], member_id=member.id, session_id="resume-api-session",
            agent_version_id=version.id, deployment_revision=1, model_id="model-1", model_revision=1,
            compiled_snapshot_json='{"grants":{},"skills":[]}', model_snapshot_json='{"id":"model-1","revision":1}',
            consent_snapshot_json="{}", budget_snapshot_json='{"tool_calls":2,"tokens":100,"wall_time_seconds":30,"delegation_depth":1}')
        db.add(run); db.flush()
        tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "platform.test_high_risk"))
        approval = ApprovalRequest(household_id=member.household_id, run_id=run.id,
            principal_id=login["account"]["id"], tool_id=tool.id, tool_version="1",
            deployment_id=deployment.id, deployment_revision=1, agent_version_id=version.id,
            arguments_digest="digest", arguments_json="{}", idempotency_key="public-resume", status="approved")
        db.add(approval); db.flush(); approval_id, run_id = approval.id, run.id
    snapshot = SimpleNamespace(model_id="model-1", config_revision=1)
    class Runtime:
        def __init__(self, db): pass
        def resolve(self, purpose, household_id): return SimpleNamespace(snapshot=snapshot)
        def build_chat_model(self, resolved): return object()
    monkeypatch.setattr(approvals_api, "ModelRuntimeService", Runtime)
    response = client.post(f"/api/approvals/{approval_id}/resume", headers=headers)
    assert response.status_code == 200
    assert ("bind", run_id) in called
    assert ("resume", approval_id, run_id) in called


def test_budget_restore_preserves_counters_across_resume():
    from backend.agent.platform.context import RunBudget
    budget = RunBudget.restore({"tool_calls": 2, "tokens": 10, "wall_time_seconds": 30, "delegation_depth": 1},
                               {"tool_calls": 1, "tokens": 4, "delegations": 1})
    budget.consume_tool(); budget.consume_tokens(6)
    with pytest.raises(RuntimeError): budget.consume_tool()
    assert budget.snapshot()["tokens"] == 10


def test_production_compiled_workflow_delegate_and_parallel_share_budget(platform_seed):
    from backend.agent.platform.compiler import CompiledAgent
    from backend.agent.platform.context import RunBudget

    invoked = []
    class Child:
        def invoke(self, payload, context): invoked.append(payload); return {"messages": ["child-ok"]}
    workflow = {"entry": "delegate", "nodes": [
        {"id": "delegate", "type": "delegate", "subagent": "billing", "message": "help", "next": "parallel"},
        {"id": "parallel", "type": "parallel", "branches": ["one", "two"], "next": "end"},
        {"id": "one", "type": "respond", "text": "one", "next": "end_one"},
        {"id": "end_one", "type": "end"},
        {"id": "two", "type": "respond", "text": "two", "next": "end_two"},
        {"id": "end_two", "type": "end"}, {"id": "end", "type": "end"}]}
    budget = RunBudget(max_delegation_depth=1, max_tool_calls=2)
    context = type(platform_seed["context"])(**{**platform_seed["context"].__dict__, "budget": budget})
    compiled = CompiledAgent("d", "v", "p", workflow, (), None, {}, subagents=(
        {"name": "billing", "description": "billing", "runnable": Child()},), subagent_names=("billing",))
    events = list(compiled.stream([{"role": "user", "content": "run"}], context))
    assert invoked and any(event.get("data", {}).get("content") == "one" for event in events)
    assert any(event.get("data", {}).get("content") == "two" for event in events)
    assert budget.snapshot()["delegation_depth"] == 0


def test_child_delegation_depth_limit_cannot_be_bypassed(platform_seed):
    from backend.agent.platform.compiler import BudgetedChildRunnable
    from backend.agent.platform.context import RunBudget
    class Recursive:
        def invoke(self, payload, context): return wrapped.invoke(payload, context=context)
    budget = RunBudget(max_delegation_depth=1)
    wrapped = BudgetedChildRunnable(Recursive(), budget)
    with pytest.raises(RuntimeError) as error: wrapped.invoke({}, context=platform_seed["context"])
    assert str(error.value) == "RUN_LIMIT_EXCEEDED"
    assert budget.snapshot()["delegation_depth"] == 0
