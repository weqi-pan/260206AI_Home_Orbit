import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.app.db.base import utcnow
from backend.tests.test_agent_platform_runtime import db_session, platform_seed  # noqa: F401


def _snapshot(db, seed):
    from backend.agent.platform.snapshots import CompiledSnapshotService

    return json.loads(json.dumps(CompiledSnapshotService(db).capture(
        seed["deployment_id"], seed["installation_id"], seed["context"])))


def test_production_resume_failure_emits_terminal_run_event_matching_db(db_session, platform_seed):
    from backend.agent.platform.policy_errors import PlatformToolError
    from backend.agent.platform.runner import DeploymentAgentRunner
    from backend.app.db.models import AgentRun

    class Failing:
        def resume(self, approval_id, context):
            raise PlatformToolError("APPROVAL_RETRY_REQUIRED")
            yield

    runner = DeploymentAgentRunner(lambda: db_session)
    runner._compile_agent = lambda context, session: Failing()
    events = list(runner.resume("approval", platform_seed["context"]))
    run = db_session.get(AgentRun, platform_seed["context"].run_id)
    assert events[-1]["data"] == {
        "run_id": run.id,
        "status": "retry_required",
        "error_code": "APPROVAL_RETRY_REQUIRED",
    }
    assert (run.status, run.error_code) == ("retry_required", "APPROVAL_RETRY_REQUIRED")


def test_public_resume_stream_failure_sse_matches_persisted_run(client, app, monkeypatch):
    from types import SimpleNamespace

    from backend.agent.platform.policy_errors import PlatformToolError
    from backend.agent.platform.runner import DeploymentAgentRunner
    from backend.app.api import approvals as approvals_api
    from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentRun, AgentVersion, ApprovalRequest,
                                       HouseholdMember, ToolDefinition)

    login = client.post("/api/auth/login", json={"username": "demo", "password": "DemoTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory.begin() as db:
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == login["account"]["id"]))
        definition = AgentDefinition(household_id=member.household_id, name="round4-public-resume")
        db.add(definition); db.flush()
        version = AgentVersion(household_id=member.household_id, agent_id=definition.id, version=1,
            definition_json='{"prompt":"resume","tools":[]}', published_at=utcnow())
        db.add(version); db.flush()
        deployment = AgentDeployment(household_id=member.household_id, agent_version_id=version.id)
        db.add(deployment); db.flush()
        run = AgentRun(household_id=member.household_id, deployment_id=deployment.id,
            principal_id=login["account"]["id"], member_id=member.id, session_id="round4-public-resume",
            agent_version_id=version.id, deployment_revision=1, model_id="model-1", model_revision=1,
            compiled_snapshot_json='{"grants":{},"skills":[]}', model_snapshot_json='{"id":"model-1"}',
            consent_snapshot_json="{}", budget_snapshot_json="{}")
        db.add(run); db.flush()
        tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "platform.test_high_risk"))
        approval = ApprovalRequest(household_id=member.household_id, run_id=run.id,
            principal_id=login["account"]["id"], tool_id=tool.id, tool_version="1",
            deployment_id=deployment.id, deployment_revision=1, agent_version_id=version.id,
            arguments_digest="digest", arguments_json="{}", idempotency_key="round4-public", status="approved")
        db.add(approval); db.flush(); approval_id, run_id = approval.id, run.id

    class Failing:
        def resume(self, approval_id, context):
            raise PlatformToolError("APPROVAL_RETRY_REQUIRED")
            yield

    runner = DeploymentAgentRunner(app.state.database.session_factory)
    runner._compile_agent = lambda context, session: Failing()
    app.state.deployment_agent_runner = runner
    snapshot = SimpleNamespace(model_id="model-1", config_revision=1)

    class Runtime:
        def __init__(self, db): pass
        def resolve(self, purpose, household_id): return SimpleNamespace(snapshot=snapshot)
        def build_chat_model(self, resolved): return object()

    monkeypatch.setattr(approvals_api, "ModelRuntimeService", Runtime)
    response = client.post(f"/api/approvals/{approval_id}/resume", headers=headers)
    assert response.status_code == 200
    payloads = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    terminal = payloads[-1]
    with app.state.database.session_factory() as db:
        persisted = db.get(AgentRun, run_id)
        assert terminal == {"run_id": run_id, "status": persisted.status, "error_code": persisted.error_code}


@pytest.mark.parametrize("field,value", [
    (("agent", "id"), "other-agent"),
    (("installation", "household_id"), "other-household"),
    (("installation", "deployment_id"), "other-deployment"),
    (("installation", "agent_version_id"), "other-version"),
    (("tools", 0, "installation_id"), "other-installation"),
    (("tools", 0, "tool_id"), "other-tool"),
    (("tools", 0, "key"), "platform.test_high_risk"),
])
def test_snapshot_rejects_parent_authority_identity_tampering(db_session, platform_seed, field, value):
    from backend.agent.platform.policy_errors import PlatformToolError
    from backend.agent.platform.snapshots import SnapshotCompiler

    snapshot = _snapshot(db_session, platform_seed)
    target = snapshot
    for part in field[:-1]:
        target = target[part]
    target[field[-1]] = value
    with pytest.raises(PlatformToolError) as error:
        SnapshotCompiler(db_session).validate_and_load(snapshot, platform_seed["context"])
    assert error.value.code == "SNAPSHOT_INVALID"


@pytest.mark.parametrize("field", ["workflow", "budget"])
def test_snapshot_rejects_duplicate_executable_fields_that_disagree_with_definition(
        db_session, platform_seed, field):
    from backend.agent.platform.policy_errors import PlatformToolError
    from backend.agent.platform.snapshots import SnapshotCompiler

    snapshot = _snapshot(db_session, platform_seed)
    snapshot[field] = {"tampered": True}
    with pytest.raises(PlatformToolError) as error:
        SnapshotCompiler(db_session).validate_and_load(snapshot, platform_seed["context"])
    assert error.value.code == "SNAPSHOT_INVALID"


def test_child_budget_enforces_scoped_tool_token_and_time_deltas(platform_seed, monkeypatch):
    from backend.agent.platform.compiler import BudgetedChildRunnable
    from backend.agent.platform.context import RunBudget

    class Child:
        def invoke(self, payload, context):
            context.budget.consume_tool()
            context.budget.consume_tool()
            return "unreachable"

        def with_config(self, config):
            return self

    budget = RunBudget(max_tool_calls=10, max_tokens=100, wall_time_seconds=100)
    context = type(platform_seed["context"])(**{**platform_seed["context"].__dict__, "budget": budget})
    with pytest.raises(RuntimeError, match="RUN_LIMIT_EXCEEDED"):
        BudgetedChildRunnable(Child(), budget, {"tool_calls": 1, "tokens": 100}).invoke({}, context=context)

    clock = [utcnow()]
    monkeypatch.setattr("backend.app.db.base.utcnow", lambda: clock[0])

    class SlowChild:
        def invoke(self, payload, context):
            clock[0] += timedelta(seconds=2)
            context.budget.check_time()

        def with_config(self, config):
            return self

    with pytest.raises(RuntimeError, match="RUN_LIMIT_EXCEEDED"):
        BudgetedChildRunnable(SlowChild(), budget, {"wall_time_seconds": 1}).invoke({}, context=context)


def test_child_budget_enforces_scoped_token_and_recursive_depth(platform_seed):
    from backend.agent.platform.compiler import BudgetedChildRunnable
    from backend.agent.platform.context import RunBudget

    budget = RunBudget(max_delegation_depth=4, max_tokens=100)
    context = type(platform_seed["context"])(**{**platform_seed["context"].__dict__, "budget": budget})

    class TokenChild:
        def invoke(self, payload, context):
            context.budget.consume_tokens(3)
            return "unreachable"

        def with_config(self, config):
            return self

    with pytest.raises(RuntimeError, match="RUN_LIMIT_EXCEEDED"):
        BudgetedChildRunnable(TokenChild(), budget, {"tokens": 2}).invoke({}, context=context)

    class RecursiveChild:
        def invoke(self, payload, context):
            return BudgetedChildRunnable(self, budget, {"delegation_depth": 1}).invoke({}, context=context)

        def with_config(self, config):
            return self

    with pytest.raises(RuntimeError, match="RUN_LIMIT_EXCEEDED"):
        BudgetedChildRunnable(RecursiveChild(), budget, {"delegation_depth": 1}).invoke({}, context=context)


def test_compiled_parallel_tokens_consume_budget_deterministically(platform_seed):
    from backend.agent.platform.compiler import CompiledAgent
    from backend.agent.platform.context import RunBudget

    workflow = {"entry": "p", "nodes": [
        {"id": "p", "type": "parallel", "branches": ["a", "b"], "next": "e"},
        {"id": "a", "type": "respond", "text": "12345", "next": "ea"},
        {"id": "ea", "type": "end"},
        {"id": "b", "type": "respond", "text": "67890", "next": "eb"},
        {"id": "eb", "type": "end"},
        {"id": "e", "type": "end"},
    ]}
    budget = RunBudget(max_tokens=1)
    context = type(platform_seed["context"])(**{**platform_seed["context"].__dict__, "budget": budget})
    compiled = CompiledAgent("d", "v", "", workflow, (), None, {}, None)
    with pytest.raises(RuntimeError, match="RUN_LIMIT_EXCEEDED"):
        list(compiled.stream([], context))


def test_install_returns_only_current_active_version_after_upgrade(db_session, platform_seed):
    from backend.agent.platform.installations import InstallationService
    from backend.app.db.models import AgentVersion

    current = db_session.get(AgentVersion, platform_seed["context"].agent_version_id)
    newer = AgentVersion(household_id=current.household_id, agent_id=current.agent_id, version=2,
        definition_json=current.definition_json, published_at=utcnow())
    db_session.add(newer); db_session.flush()
    service = InstallationService(db_session, platform_seed["household_id"], platform_seed["context"].principal_id)
    upgraded = service.upgrade(platform_seed["deployment_id"], newer.id)
    assert service.install(platform_seed["deployment_id"]).id == upgraded.id
    assert service.install(platform_seed["deployment_id"]).agent_version_id == newer.id


def test_side_effect_failure_audit_error_rolls_back_approval_and_run_transition(
        db_session, platform_seed, monkeypatch):
    from backend.agent.platform.approvals import ApprovalResumeService
    from backend.agent.platform.policy import PlatformToolExecutor
    from backend.app.db.models import AgentRun, ApprovalRequest

    executor = PlatformToolExecutor(db_session)
    with pytest.raises(Exception) as interrupted:
        executor.execute("platform.test_high_risk", {"value": "safe"}, platform_seed["context"], "round4-audit")
    approval_id = interrupted.value.approval_id
    executor.approve(approval_id, platform_seed["context"].principal_id, utcnow() + timedelta(minutes=5))

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("backend.agent.platform.approvals.audit_approval", fail_audit)
    service = ApprovalResumeService(lambda: db_session,
        side_effect=lambda args, context: (_ for _ in ()).throw(RuntimeError("raw secret")))
    with pytest.raises(RuntimeError, match="audit unavailable"):
        service.claim_and_execute(approval_id, platform_seed["context"], {"value": "safe"})
    db_session.expire_all()
    approval = db_session.get(ApprovalRequest, approval_id)
    run = db_session.get(AgentRun, platform_seed["context"].run_id)
    assert approval.status == "executing" and approval.error_code is None
    assert run.status != "retry_required"
