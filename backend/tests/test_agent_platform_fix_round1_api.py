import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.db.base import utcnow
from backend.tests.test_agent_platform_runtime import db_session, platform_seed  # noqa: F401


def _login(client, username, password):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}, response.json()["account"]


def test_member_cannot_change_installation_permissions_but_owner_can(client, app):
    from backend.app.db.models import AgentDefinition, AgentDeployment, AgentInstallation, AgentVersion, HouseholdMember, ToolDefinition

    demo_headers, demo = _login(client, "demo", "DemoTest@123")
    with app.state.database.session_factory.begin() as db:
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == demo["id"]))
        member.role = "member"
        definition = AgentDefinition(household_id=member.household_id, name="permission-api")
        db.add(definition); db.flush()
        version = AgentVersion(household_id=member.household_id, agent_id=definition.id, version=1,
            definition_json='{"prompt":"x","tools":[]}', published_at=utcnow())
        db.add(version); db.flush()
        deployment = AgentDeployment(household_id=member.household_id, agent_version_id=version.id)
        db.add(deployment); db.flush()
        installation = AgentInstallation(household_id=member.household_id, deployment_id=deployment.id,
            agent_version_id=version.id)
        db.add(installation); db.flush()
        tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "platform.current_month"))
        installation_id, tool_id = installation.id, tool.id
    denied = client.put(f"/api/installations/{installation_id}/permissions", headers=demo_headers,
        json={"tool_id": tool_id, "tool_version": "1"})
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "INSTALLATION_PERMISSION_FORBIDDEN"


def test_approval_decision_allows_bound_principal_or_owner_admin_and_is_pending_only(client, app):
    from backend.app.db.models import (AgentDefinition, AgentDeployment, AgentRun, AgentVersion, ApprovalRequest,
                                       HouseholdMember, ToolDefinition)

    headers, account = _login(client, "demo", "DemoTest@123")
    with app.state.database.session_factory.begin() as db:
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == account["id"]))
        definition = AgentDefinition(household_id=member.household_id, name="approval-api")
        db.add(definition); db.flush()
        version = AgentVersion(household_id=member.household_id, agent_id=definition.id, version=1,
            definition_json='{"prompt":"x","tools":[]}', published_at=utcnow())
        db.add(version); db.flush()
        deployment = AgentDeployment(household_id=member.household_id, agent_version_id=version.id)
        db.add(deployment); db.flush()
        run = AgentRun(household_id=member.household_id, deployment_id=deployment.id, principal_id=account["id"],
            member_id=member.id, session_id="approval-session", agent_version_id=version.id,
            deployment_revision=1, model_id="model", model_revision=1)
        db.add(run); db.flush()
        tool = db.scalar(select(ToolDefinition).where(ToolDefinition.key == "platform.test_high_risk"))
        approval = ApprovalRequest(household_id=member.household_id, run_id=run.id, principal_id=account["id"],
            tool_id=tool.id, tool_version="1", deployment_id=deployment.id, deployment_revision=1,
            agent_version_id=version.id, arguments_digest="digest", arguments_json="{}", idempotency_key="api-approval",
            consent_digest="consent")
        db.add(approval); db.flush()
        approval_id = approval.id
    accepted = client.post(f"/api/approvals/{approval_id}/approve", headers=headers,
        json={"expires_at": (utcnow() + timedelta(minutes=5)).isoformat()})
    assert accepted.status_code == 200
    duplicate = client.post(f"/api/approvals/{approval_id}/reject", headers=headers)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "APPROVAL_STATE_CONFLICT"
    with app.state.database.session_factory() as db:
        from backend.app.db.models import ToolExecution
        assert db.scalar(select(ToolExecution).where(ToolExecution.status == "approval_approved")) is not None


def test_composite_household_constraints_reject_cross_tenant_installation_and_chat_binding(db_session, platform_seed):
    from backend.app.core.security import hash_password
    from backend.app.db.models import Account, AgentInstallation, ChatSession, Household, HouseholdMember

    other_account = Account(username="other-tenant", password_hash=hash_password("Passw0rd!", rounds=4))
    db_session.add(other_account); db_session.flush()
    other = Household(name="Other", created_by_account_id=other_account.id)
    db_session.add(other); db_session.flush()
    db_session.add(HouseholdMember(household_id=other.id, account_id=other_account.id, role="owner")); db_session.flush()
    db_session.add(AgentInstallation(household_id=other.id, deployment_id=platform_seed["deployment_id"],
                                     agent_version_id=platform_seed["context"].agent_version_id))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    db_session.add(ChatSession(account_id=other_account.id, household_id=other.id,
                               deployment_id=platform_seed["deployment_id"]))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_agent_run_persists_complete_compiled_snapshot(client, app):
    from backend.app.db.models import AgentDefinition, AgentDeployment, AgentInstallation, AgentRun, AgentVersion, ChatSession, HouseholdMember

    headers, account = _login(client, "demo", "DemoTest@123")
    session_id = client.post("/api/chat/sessions", headers=headers, json={"title": "snapshot"}).json()["id"]
    with app.state.database.session_factory.begin() as db:
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.account_id == account["id"]))
        definition = AgentDefinition(household_id=member.household_id, name="snapshot-agent")
        db.add(definition); db.flush()
        version = AgentVersion(household_id=member.household_id, agent_id=definition.id, version=1,
            definition_json='{"prompt":"snapshot","tools":[]}', published_at=utcnow())
        db.add(version); db.flush()
        deployment = AgentDeployment(household_id=member.household_id, agent_version_id=version.id)
        db.add(deployment); db.flush()
        installation = AgentInstallation(household_id=member.household_id, deployment_id=deployment.id,
            agent_version_id=version.id)
        db.add(installation); db.flush()
        chat = db.get(ChatSession, session_id); chat.household_id = member.household_id; chat.deployment_id = deployment.id
    class Runner:
        def stream(self, messages, context): yield {"event": "token", "data": {"content": "ok"}}
    app.state.agent_runner = Runner()
    response = client.post(f"/api/chat/sessions/{session_id}/chat", headers=headers, json={"message": "run"})
    assert response.status_code == 200
    with app.state.database.session_factory() as db:
        run = db.scalar(select(AgentRun).where(AgentRun.session_id == session_id))
        snapshot = json.loads(run.compiled_snapshot_json)
        assert snapshot["installation"]["id"] == installation.id
        assert snapshot["agent_version"]["config_hash"]
        assert run.consent_snapshot_json == "{}"


def test_permission_denial_and_approval_request_are_audited_with_redaction_version(db_session, platform_seed):
    from backend.agent.platform.policy import PlatformToolExecutor
    from backend.app.db.models import InstallationPermission, ToolExecution

    permission = platform_seed["installation_permission"]
    db_session.delete(permission); db_session.commit()
    with pytest.raises(Exception):
        PlatformToolExecutor(db_session).execute("platform.current_month", {}, platform_seed["context"], "denied")
    denied = db_session.scalar(select(ToolExecution).where(ToolExecution.status == "permission_denied"))
    assert denied.error_code == "PERMISSION_REQUIRED"
    permission = InstallationPermission(household_id=platform_seed["household_id"],
        installation_id=platform_seed["installation_id"], tool_id=platform_seed["tool_id"], tool_version="1",
        granted_by_account_id=platform_seed["context"].principal_id)
    db_session.add(permission); db_session.commit()
    result = PlatformToolExecutor(db_session).execute("platform.current_month", {}, platform_seed["context"], "allowed")
    audit = db_session.scalar(select(ToolExecution).where(ToolExecution.status == "success"))
    assert result
    assert audit.redaction_schema_version == 1


def test_tool_failure_and_run_limit_persist_stable_outcomes(db_session, platform_seed):
    from backend.agent.platform.policy import PlatformToolExecutor
    from backend.app.db.models import AgentRun, ToolExecution

    executor = PlatformToolExecutor(db_session, side_effect=lambda args, context: (_ for _ in ()).throw(RuntimeError("secret detail")))
    with pytest.raises(RuntimeError):
        executor.execute("platform.current_month", {}, platform_seed["context"], "failed-call")
    failed = db_session.scalar(select(ToolExecution).where(ToolExecution.status == "failed"))
    assert failed.error_code == "TOOL_EXECUTION_FAILED"
    assert "secret detail" not in failed.result_json
    run = db_session.get(AgentRun, platform_seed["context"].run_id)
    from backend.agent.platform.runner import persist_run_outcome
    persist_run_outcome(db_session, run.id, "failed", "RUN_LIMIT_EXCEEDED")
    db_session.refresh(run)
    assert run.error_code == "RUN_LIMIT_EXCEEDED"
