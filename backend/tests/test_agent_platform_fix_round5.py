import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from backend.app.db.base import utcnow
from backend.tests.test_agent_platform_runtime import db_session, platform_seed  # noqa: F401


def _snapshot(db, seed):
    from backend.agent.platform.snapshots import CompiledSnapshotService

    return json.loads(json.dumps(CompiledSnapshotService(db).capture(
        seed["deployment_id"], seed["installation_id"], seed["context"])))


@pytest.mark.parametrize("method", ["stream", "resume"])
def test_callable_cancellation_persists_and_emits_terminal_event(db_session, platform_seed, method):
    from backend.agent.platform.policy_errors import PlatformToolError
    from backend.agent.platform.runner import DeploymentAgentRunner
    from backend.app.db.models import AgentRun

    class Cancelled:
        def stream(self, messages, context):
            raise PlatformToolError("RUN_CANCELLED")
            yield

        def resume(self, approval_id, context):
            raise PlatformToolError("RUN_CANCELLED")
            yield

    runner = DeploymentAgentRunner(lambda: db_session)
    runner._compile_agent = lambda context, session: Cancelled()
    events = list(getattr(runner, method)(*((["message"], platform_seed["context"])
        if method == "stream" else ("approval", platform_seed["context"]))))
    run = db_session.get(AgentRun, platform_seed["context"].run_id)
    assert events[-1] == {"event": "run", "data": {
        "run_id": run.id, "status": "cancelled", "error_code": "RUN_CANCELLED"}}
    assert (run.status, run.error_code) == ("cancelled", "RUN_CANCELLED")


def test_disconnect_cancellation_persists_without_claiming_terminal_delivery(db_session, platform_seed):
    from backend.agent.platform.runner import DeploymentAgentRunner
    from backend.app.db.models import AgentRun

    class Streaming:
        def stream(self, messages, context):
            yield {"event": "token", "data": {"content": "partial"}}
            yield {"event": "token", "data": {"content": "later"}}

    runner = DeploymentAgentRunner(lambda: db_session)
    runner._compile_agent = lambda context, session: Streaming()
    delivered = []
    iterator = runner.stream([], platform_seed["context"])
    delivered.extend([next(iterator), next(iterator)])
    iterator.close()
    run = db_session.get(AgentRun, platform_seed["context"].run_id)
    assert (run.status, run.error_code) == ("cancelled", "RUN_CANCELLED")
    assert not any(event.get("data", {}).get("status") == "cancelled" for event in delivered)


def test_snapshot_rejects_agent_definition_tamper_even_with_recomputed_hash(db_session, platform_seed):
    from backend.agent.platform.policy_errors import PlatformToolError
    from backend.agent.platform.snapshots import SnapshotCompiler
    from backend.agent.platform.versions import normalize_json, validate_agent_definition

    snapshot = _snapshot(db_session, platform_seed)
    snapshot["agent_version"]["definition"]["prompt"] = "tampered snapshot prompt"
    normalized = normalize_json(validate_agent_definition(snapshot["agent_version"]["definition"]))
    snapshot["agent_version"]["config_hash"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    with pytest.raises(PlatformToolError) as error:
        SnapshotCompiler(db_session).validate_and_load(snapshot, platform_seed["context"])
    assert error.value.code == "SNAPSHOT_INVALID"


def test_snapshot_rejects_skill_definition_tamper_even_with_recomputed_hash(db_session, platform_seed):
    from sqlalchemy import select

    from backend.agent.platform.policy_errors import PlatformToolError
    from backend.agent.platform.snapshots import CompiledSnapshotService, SnapshotCompiler
    from backend.agent.platform.versions import normalize_json, validate_skill_definition
    from backend.app.db.models import AgentSkillBinding, Skill, SkillVersion

    skill = Skill(household_id=platform_seed["household_id"], name="round5-skill")
    db_session.add(skill); db_session.flush()
    definition = validate_skill_definition({"instructions": "canonical instructions"})
    version = SkillVersion(household_id=platform_seed["household_id"], skill_id=skill.id, version=1,
        definition_json=normalize_json(definition), published_at=utcnow())
    db_session.add(version); db_session.flush()
    db_session.add(AgentSkillBinding(household_id=platform_seed["household_id"],
        agent_version_id=platform_seed["context"].agent_version_id, skill_version_id=version.id))
    db_session.commit()
    snapshot = json.loads(json.dumps(CompiledSnapshotService(db_session).capture(
        platform_seed["deployment_id"], platform_seed["installation_id"], platform_seed["context"])))
    snapshot["skills"][0]["definition"]["instructions"] = "tampered instructions"
    normalized = normalize_json(validate_skill_definition(snapshot["skills"][0]["definition"]))
    snapshot["skills"][0]["config_hash"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    with pytest.raises(PlatformToolError) as error:
        SnapshotCompiler(db_session).validate_and_load(snapshot, platform_seed["context"])
    assert error.value.code == "SNAPSHOT_INVALID"


def test_parallel_sibling_budget_scopes_do_not_charge_each_other(platform_seed):
    from backend.agent.platform.compiler import BudgetedChildRunnable
    from backend.agent.platform.context import RunBudget

    barrier = threading.Barrier(2)
    budget = RunBudget(max_tool_calls=2)
    context = type(platform_seed["context"])(**{**platform_seed["context"].__dict__, "budget": budget})

    class Child:
        def invoke(self, payload, context):
            barrier.wait(timeout=5)
            context.budget.consume_tool()
            barrier.wait(timeout=5)
            return "ok"

        def with_config(self, config):
            return self

    runnable = BudgetedChildRunnable(Child(), budget, {"tool_calls": 1})
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert [future.result(timeout=5) for future in
            [pool.submit(runnable.invoke, {}, context=context) for _ in range(2)]] == ["ok", "ok"]
    assert budget.snapshot()["tool_calls"] == 2


def test_nested_child_scopes_inherit_ancestry_and_global_limit(platform_seed):
    from backend.agent.platform.compiler import BudgetedChildRunnable
    from backend.agent.platform.context import RunBudget

    budget = RunBudget(max_tool_calls=2, max_delegation_depth=2)
    context = type(platform_seed["context"])(**{**platform_seed["context"].__dict__, "budget": budget})

    class Inner:
        def invoke(self, payload, context):
            context.budget.consume_tool()
            context.budget.consume_tool()

        def with_config(self, config):
            return self

    class Outer:
        def invoke(self, payload, context):
            return BudgetedChildRunnable(Inner(), budget, {"tool_calls": 2}).invoke({}, context=context)

        def with_config(self, config):
            return self

    with pytest.raises(RuntimeError, match="RUN_LIMIT_EXCEEDED"):
        BudgetedChildRunnable(Outer(), budget, {"tool_calls": 1}).invoke({}, context=context)

    global_budget = RunBudget(max_tool_calls=1)
    global_context = type(context)(**{**context.__dict__, "budget": global_budget})
    with pytest.raises(RuntimeError, match="RUN_LIMIT_EXCEEDED"):
        BudgetedChildRunnable(Inner(), global_budget, {"tool_calls": 2}).invoke({}, context=global_context)


def test_direct_deployment_switch_invalidates_grant_list_and_revoke(db_session, platform_seed):
    from backend.agent.platform.installations import InstallationService
    from backend.app.db.models import AgentDeployment, AgentVersion, InstallationPermission

    current = db_session.get(AgentVersion, platform_seed["context"].agent_version_id)
    newer = AgentVersion(household_id=current.household_id, agent_id=current.agent_id, version=2,
        definition_json=current.definition_json, published_at=utcnow())
    db_session.add(newer); db_session.flush()
    deployment = db_session.get(AgentDeployment, platform_seed["deployment_id"])
    deployment.agent_version_id = newer.id
    deployment.revision += 1
    db_session.commit()
    service = InstallationService(db_session, platform_seed["household_id"], platform_seed["context"].principal_id)
    with pytest.raises(RuntimeError, match="VERSION_CHANGED"):
        service.grant(platform_seed["installation_id"], platform_seed["tool_id"], "1")
    with pytest.raises(RuntimeError, match="VERSION_CHANGED"):
        service.authorized_tool_ids(platform_seed["installation_id"])
    with pytest.raises(RuntimeError, match="VERSION_CHANGED"):
        service.revoke(platform_seed["installation_id"], platform_seed["tool_id"])
    assert db_session.get(InstallationPermission, platform_seed["installation_permission"].id) is not None


def test_direct_deployment_switch_invalidates_permission_api_grant_and_revoke(client, app):
    from sqlalchemy import select

    from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentInstallation, AgentVersion,
                                       HouseholdMember, InstallationPermission, ToolDefinition)

    login = client.post("/api/auth/login", json={"username": "demo", "password": "DemoTest@123"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    with app.state.database.session_factory.begin() as db:
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == login["account"]["id"]))
        member.role = "owner"
        definition = AgentDefinition(household_id=member.household_id, name="round5-version-api")
        db.add(definition); db.flush()
        first = AgentVersion(household_id=member.household_id, agent_id=definition.id, version=1,
            definition_json='{"prompt":"first","tools":[]}', published_at=utcnow())
        second = AgentVersion(household_id=member.household_id, agent_id=definition.id, version=2,
            definition_json='{"prompt":"second","tools":[]}', published_at=utcnow())
        db.add_all([first, second]); db.flush()
        deployment = AgentDeployment(household_id=member.household_id, agent_version_id=first.id, revision=1)
        db.add(deployment); db.flush()
        installation = AgentInstallation(household_id=member.household_id, deployment_id=deployment.id,
            agent_version_id=first.id, status="installed", authority_slot="active")
        db.add(installation); db.flush()
        tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "platform.current_month"))
        permission = InstallationPermission(household_id=member.household_id, installation_id=installation.id,
            tool_id=tool.id, tool_version="1", granted_by_account_id=login["account"]["id"])
        db.add(permission); db.flush()
        installation_id, tool_id, permission_id = installation.id, tool.id, permission.id
        deployment.agent_version_id = second.id
        deployment.revision += 1

    granted = client.put(f"/api/installations/{installation_id}/permissions", headers=headers,
        json={"tool_id": tool_id, "tool_version": "1"})
    revoked = client.delete(f"/api/installations/{installation_id}/permissions/{tool_id}", headers=headers)
    assert (granted.status_code, granted.json()["detail"]["code"]) == (409, "VERSION_CHANGED")
    assert (revoked.status_code, revoked.json()["detail"]["code"]) == (409, "VERSION_CHANGED")
    with app.state.database.session_factory() as db:
        assert db.get(InstallationPermission, permission_id) is not None


@pytest.mark.parametrize("method", ["stream", "resume"])
def test_runner_sanitizes_unknown_third_party_exception_code(db_session, platform_seed, method):
    from backend.agent.platform.runner import DeploymentAgentRunner
    from backend.app.db.models import AgentRun

    class ThirdPartyFailure(RuntimeError):
        code = "VENDOR_SECRET_DIAGNOSTIC"

    class Failing:
        def stream(self, messages, context):
            raise ThirdPartyFailure("raw provider detail")
            yield

        def resume(self, approval_id, context):
            raise ThirdPartyFailure("raw provider detail")
            yield

    runner = DeploymentAgentRunner(lambda: db_session)
    runner._compile_agent = lambda context, session: Failing()
    events = list(getattr(runner, method)(*((["message"], platform_seed["context"])
        if method == "stream" else ("approval", platform_seed["context"]))))
    run = db_session.get(AgentRun, platform_seed["context"].run_id)
    assert events[-1]["data"]["error_code"] == "AGENT_RUN_FAILED"
    assert run.error_code == "AGENT_RUN_FAILED"


@pytest.mark.parametrize("invalidated", [False, True])
def test_expired_or_invalidated_approval_state_and_audit_commit_before_raise(
        db_session, platform_seed, invalidated):
    from backend.agent.platform.approvals import ApprovalResumeService
    from backend.agent.platform.policy import PlatformToolExecutor
    from backend.agent.platform.policy_errors import PlatformToolError
    from backend.app.db.models import AgentDeployment, ApprovalRequest, ToolExecution

    executor = PlatformToolExecutor(db_session)
    with pytest.raises(Exception) as interrupted:
        executor.execute("platform.test_high_risk", {"value": "safe"}, platform_seed["context"],
            f"round5-{'invalidated' if invalidated else 'expired'}")
    approval_id = interrupted.value.approval_id
    executor.approve(approval_id, platform_seed["context"].principal_id,
        utcnow() + (timedelta(minutes=5) if invalidated else timedelta(seconds=-1)))
    if invalidated:
        deployment = db_session.get(AgentDeployment, platform_seed["deployment_id"])
        deployment.revision += 1
        db_session.commit()
    service = ApprovalResumeService(lambda: db_session, side_effect=lambda args, context: {"ok": True})
    expected = "APPROVAL_VERSION_CHANGED" if invalidated else "APPROVAL_EXPIRED"
    with pytest.raises(PlatformToolError) as error:
        service.claim_and_execute(approval_id, platform_seed["context"], {"value": "safe"})
    assert error.value.code == expected
    db_session.expire_all()
    approval = db_session.get(ApprovalRequest, approval_id)
    assert (approval.status, approval.error_code) == (
        "invalidated" if invalidated else "expired", expected)
    audit = db_session.query(ToolExecution).filter_by(
        idempotency_key=f"{approval.idempotency_key}:approval_{approval.status}").one()
    assert audit.error_code == expected


def test_expired_approval_audit_failure_rolls_back_state(db_session, platform_seed, monkeypatch):
    from backend.agent.platform.approvals import ApprovalResumeService
    from backend.agent.platform.policy import PlatformToolExecutor
    from backend.app.db.models import ApprovalRequest

    executor = PlatformToolExecutor(db_session)
    with pytest.raises(Exception) as interrupted:
        executor.execute("platform.test_high_risk", {"value": "safe"}, platform_seed["context"], "round5-expired-audit")
    approval_id = interrupted.value.approval_id
    executor.approve(approval_id, platform_seed["context"].principal_id, utcnow() - timedelta(seconds=1))

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("backend.agent.platform.approvals.audit_approval", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        ApprovalResumeService(lambda: db_session, lambda args, context: {}).claim_and_execute(
            approval_id, platform_seed["context"], {"value": "safe"})
    db_session.expire_all()
    approval = db_session.get(ApprovalRequest, approval_id)
    assert approval.status == "approved" and approval.error_code is None
